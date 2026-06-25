"""
RemoteSTT and RemoteTTS: drop-in replacements for WhisperSTT / PiperTTS
that forward requests to the vox-server running on Aragorn over SSH
direct-tcpip channels.

Interface contract
------------------
RemoteSTT mirrors WhisperSTT:
  - SAMPLE_RATE class constant (16000)
  - load() — no-op; models live on the server
  - async transcribe(audio: np.ndarray) -> str
  - close()

RemoteTTS mirrors PiperTTS:
  - sample_rate instance attribute (set from the READY handshake)
  - load() — no-op
  - async synthesize(text: str, length_scale: float) -> np.ndarray
  - close()
"""
import asyncio
import logging

import numpy as np

from vox.server.protocol import (
    read_stt_response,
    read_tts_response,
    write_stt_request,
    write_tts_request,
)

log = logging.getLogger(__name__)


class RemoteSTT:
    SAMPLE_RATE = 16000

    def __init__(self, conn, port: int) -> None:
        self._conn = conn
        self._port = port

    def load(self) -> None:
        pass

    async def transcribe(self, audio: np.ndarray) -> str:
        reader, writer = await self._conn.open_connection("127.0.0.1", self._port)
        try:
            await write_stt_request(writer, audio)
            return await read_stt_response(reader)
        finally:
            writer.close()

    def close(self) -> None:
        pass


class RemoteTTS:
    def __init__(self, conn, port: int, sample_rate: int) -> None:
        self._conn = conn
        self._port = port
        self.sample_rate = sample_rate

    def load(self) -> None:
        pass

    async def synthesize(self, text: str, length_scale: float = 1.0) -> np.ndarray:
        reader, writer = await self._conn.open_connection("127.0.0.1", self._port)
        try:
            await write_tts_request(writer, text, length_scale)
            _, pcm = await read_tts_response(reader)
            return pcm
        finally:
            writer.close()

    def close(self) -> None:
        pass
