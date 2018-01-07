# -*- coding: utf-8 -*-

"""
The MIT License (MIT)

Copyright (c) 2015-2017 Rapptz

Permission is hereby granted, free of charge, to any person obtaining a
copy of this software and associated documentation files (the "Software"),
to deal in the Software without restriction, including without limitation
the rights to use, copy, modify, merge, publish, distribute, sublicense,
and/or sell copies of the Software, and to permit persons to whom the
Software is furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in
all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS
OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING
FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER
DEALINGS IN THE SOFTWARE.
"""

"""Some documentation to refer to:

- Our main web socket (mWS) sends opcode 4 with a guild ID and channel ID.
- The mWS receives VOICE_STATE_UPDATE and VOICE_SERVER_UPDATE.
- We pull the session_id from VOICE_STATE_UPDATE.
- We pull the token, endpoint and server_id from VOICE_SERVER_UPDATE.
- Then we initiate the voice web socket (vWS) pointing to the endpoint.
- We send opcode 0 with the user_id, server_id, session_id and token using the vWS.
- The vWS sends back opcode 2 with an ssrc, port, modes(array) and hearbeat_interval.
- We send a UDP discovery packet to endpoint:port and receive our IP and our port in LE.
- Then we send our IP and port via vWS with opcode 1.
- When that's all done, we receive opcode 4 from the vWS.
- Finally we can transmit data to endpoint:port.
"""

import asyncio
import socket
import logging
import struct
import threading

log = logging.getLogger(__name__)

try:
    import nacl.secret
    has_nacl = True
except ImportError:
    has_nacl = False

from . import opus
from .backoff import ExponentialBackoff
from .gateway import *
from .errors import ClientException, ConnectionClosed
from .player import AudioPlayer, AudioSource

class VoiceClient:
    """Represents a Discord voice connection.

    You do not create these, you typically get them from
    e.g. :meth:`VoiceChannel.connect`.

    Warning
    --------
    In order to play audio, you must have loaded the opus library
    through :func:`opus.load_opus`.

    If you don't do this then the library will not be able to
    transmit audio.

    Attributes
    -----------
    session_id: :class:`str`
        The voice connection session ID.
    token: :class:`str`
        The voice connection token.
    endpoint: :class:`str`
        The endpoint we are connecting to.
    channel: :class:`abc.Connectable`
        The voice channel connected to.
    loop
        The event loop that the voice client is running on.
    """
    def __init__(self, state, timeout, channel):
        if not has_nacl:
            raise RuntimeError("PyNaCl library needed in order to use voice")

        self.channel = channel
        self.main_ws = None
        self.timeout = timeout
        self.ws = None
        self.socket = None
        self.loop = state.loop
        self._state = state
        # this will be used in the AudioPlayer thread
        self._connected = threading.Event()
        self._handshake_complete = asyncio.Event(loop=self.loop)

        self._connections = 0
        self.sequence = 0
        self.timestamp = 0
        self._runner = None
        self._player = None
        self.encoder = opus.Encoder()

    warn_nacl = not has_nacl

    @property
    def guild(self):
        """Optional[:class:`Guild`]: The guild we're connected to, if applicable."""
        return getattr(self.channel, 'guild', None)

    @property
    def user(self):
        """:class:`ClientUser`: The user connected to voice (i.e. ourselves)."""
        return self._state.user

    def checked_add(self, attr, value, limit):
        val = getattr(self, attr)
        if val + value > limit:
            setattr(self, attr, 0)
        else:
            setattr(self, attr, val + value)

    # connection related

    @asyncio.coroutine
    def start_handshake(self):
        log.info('Starting voice handshake...')

        key_id, key_name = self.channel._get_voice_client_key()
        guild_id, channel_id = self.channel._get_voice_state_pair()
        state = self._state
        self.main_ws = ws = state._get_websocket(guild_id)
        self._connections += 1

        # request joining
        yield from ws.voice_state(guild_id, channel_id)

        try:
            yield from asyncio.wait_for(self._handshake_complete.wait(), timeout=self.timeout, loop=self.loop)
        except asyncio.TimeoutError as e:
            yield from self.terminate_handshake(remove=True)
            raise e

        log.info('Voice handshake complete. Endpoint found %s (IP: %s)', self.endpoint, self.endpoint_ip)

    @asyncio.coroutine
    def terminate_handshake(self, *, remove=False):
        guild_id, channel_id = self.channel._get_voice_state_pair()
        self._handshake_complete.clear()
        yield from self.main_ws.voice_state(guild_id, None, self_mute=True)

        log.info('The voice handshake is being terminated for Channel ID %s (Guild ID %s)', channel_id, guild_id)
        if remove:
            log.info('The voice client has been removed for Channel ID %s (Guild ID %s)', channel_id, guild_id)
            key_id, _ = self.channel._get_voice_client_key()
            self._state._remove_voice_client(key_id)

    @asyncio.coroutine
    def _create_socket(self, server_id, data):
        self._connected.clear()
        self.session_id = self.main_ws.session_id
        self.server_id = server_id
        self.token = data.get('token')
        endpoint = data.get('endpoint')

        if endpoint is None or self.token is None:
            log.warning('Awaiting endpoint... This requires waiting. ' \
                        'If timeout occurred considering raising the timeout and reconnecting.')
            return

        self.endpoint = endpoint.replace(':80', '')
        self.endpoint_ip = socket.gethostbyname(self.endpoint)

        if self.socket:
            try:
                self.socket.close()
            except:
                pass

        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.socket.setblocking(False)

        if self._handshake_complete.is_set():
            # terminate the websocket and handle the reconnect loop if necessary.
            self._handshake_complete.clear()
            yield from self.ws.close(1006)
            return

        self._handshake_complete.set()

    @asyncio.coroutine
    def connect(self, *, reconnect=True, _tries=0, do_handshake=True):
        log.info('Connecting to voice...')
        try:
            del self.secret_key
        except AttributeError:
            pass

        if do_handshake:
            yield from self.start_handshake()

        try:
            self.ws = yield from DiscordVoiceWebSocket.from_client(self)
            self._connected.clear()
            while not hasattr(self, 'secret_key'):
                yield from self.ws.poll_event()
            self._connected.set()
        except (ConnectionClosed, asyncio.TimeoutError):
            if reconnect and _tries < 5:
                log.exception('Failed to connect to voice... Retrying...')
                yield from asyncio.sleep(1 + _tries * 2.0, loop=self.loop)
                yield from self.terminate_handshake()
                yield from self.connect(reconnect=reconnect, _tries=_tries + 1)
            else:
                raise

        if self._runner is None:
            self._runner = self.loop.create_task(self.poll_voice_ws(reconnect))

    @asyncio.coroutine
    def poll_voice_ws(self, reconnect):
        backoff = ExponentialBackoff()
        while True:
            try:
                yield from self.ws.poll_event()
            except (ConnectionClosed, asyncio.TimeoutError) as e:
                if isinstance(e, ConnectionClosed):
                    if e.code == 1000:
                        yield from self.disconnect()
                        break

                if not reconnect:
                    yield from self.disconnect()
                    raise e

                retry = backoff.delay()
                log.exception('Disconnected from voice... Reconnecting in %.2fs.', retry)
                self._connected.clear()
                yield from asyncio.sleep(retry, loop=self.loop)
                yield from self.terminate_handshake()
                try:
                    yield from self.connect(reconnect=True)
                except asyncio.TimeoutError:
                    # at this point we've retried 5 times... let's continue the loop.
                    log.warning('Could not connect to voice... Retrying...')
                    continue

    @asyncio.coroutine
    def disconnect(self, *, force=False):
        """|coro|

        Disconnects this voice client from voice.
        """
        if not force and not self._connected.is_set():
            return

        self.stop()
        self._connected.clear()

        try:
            if self.ws:
                yield from self.ws.close()

            yield from self.terminate_handshake(remove=True)
        finally:
            if self.socket:
                self.socket.close()

    @asyncio.coroutine
    def move_to(self, channel):
        """|coro|

        Moves you to a different voice channel.

        Parameters
        -----------
        channel: :class:`abc.Snowflake`
            The channel to move to. Must be a voice channel.
        """
        guild_id, _ = self.channel._get_voice_state_pair()
        yield from self.main_ws.voice_state(guild_id, channel.id)

    def is_connected(self):
        """:class:`bool`: Indicates if the voice client is connected to voice."""
        return self._connected.is_set()

    # audio related

    def _get_voice_packet(self, data):
        header = bytearray(12)
        nonce = bytearray(24)
        box = nacl.secret.SecretBox(bytes(self.secret_key))

        # Formulate header
        header[0] = 0x80
        header[1] = 0x78
        struct.pack_into('>H', header, 2, self.sequence)
        struct.pack_into('>I', header, 4, self.timestamp)
        struct.pack_into('>I', header, 8, self.ssrc)

        # Copy header to nonce's first 12 bytes
        nonce[:12] = header

        # Encrypt and return the data
        return header + box.encrypt(bytes(data), bytes(nonce)).ciphertext

    def play(self, source, *, after=None):
        """Plays an :class:`AudioSource`.

        The finalizer, ``after`` is called after the source has been exhausted
        or an error occurred.

        If an error happens while the audio player is running, the exception is
        caught and the audio player is then stopped.

        Parameters
        -----------
        source: :class:`AudioSource`
            The audio source we're reading from.
        after
            The finalizer that is called after the stream is exhausted.
            All exceptions it throws are silently discarded. This function
            must have a single parameter, ``error``, that denotes an
            optional exception that was raised during playing.

        Raises
        -------
        ClientException
            Already playing audio or not connected.
        TypeError
            source is not a :class:`AudioSource` or after is not a callable.
        """

        if not self._connected:
            raise ClientException('Not connected to voice.')

        if self.is_playing():
            raise ClientException('Already playing audio.')

        if not isinstance(source, AudioSource):
            raise TypeError('source must an AudioSource not {0.__class__.__name__}'.format(source))

        self._player = AudioPlayer(source, self, after=after)
        self._player.start()

    def is_playing(self):
        """Indicates if we're currently playing audio."""
        return self._player is not None and self._player.is_playing()

    def is_paused(self):
        """Indicates if we're playing audio, but if we're paused."""
        return self._player is not None and self._player.is_paused()

    def stop(self):
        """Stops playing audio."""
        if self._player:
            self._player.stop()
            self._player = None

    def pause(self):
        """Pauses the audio playing."""
        if self._player:
            self._player.pause()

    def resume(self):
        """Resumes the audio playing."""
        if self._player:
            self._player.resume()

    @property
    def source(self):
        """Optional[:class:`AudioSource`]: The audio source being played, if playing.

        This property can also be used to change the audio source currently being played.
        """
        return self._player.source if self._player else None

    @source.setter
    def source(self, value):
        if not isinstance(value, AudioSource):
            raise TypeError('expected AudioSource not {0.__class__.__name__}.'.format(value))

        if self._player is None:
            raise ValueError('Not playing anything.')

        self._player._set_source(value)

    def send_audio_packet(self, data, *, encode=True):
        """Sends an audio packet composed of the data.

        You must be connected to play audio.

        Parameters
        ----------
        data: bytes
            The *bytes-like object* denoting PCM or Opus voice data.
        encode: bool
            Indicates if ``data`` should be encoded into Opus.

        Raises
        -------
        ClientException
            You are not connected.
        OpusError
            Encoding the data failed.
        """

        self.checked_add('sequence', 1, 65535)
        if encode:
            encoded_data = self.encoder.encode(data, self.encoder.SAMPLES_PER_FRAME)
        else:
            encoded_data = data
        packet = self._get_voice_packet(encoded_data)
        try:
            self.socket.sendto(packet, (self.endpoint_ip, self.voice_port))
        except BlockingIOError:
            log.warning('A packet has been dropped (seq: %s, timestamp: %s)', self.sequence, self.timestamp)

        self.checked_add('timestamp', self.encoder.SAMPLES_PER_FRAME, 4294967295)