"""
Binary framing protocol shared by vox-server and the Windows remote stubs.

Frame formats
-------------
STT request  : op(1) sample_count(4) pcm_bytes(sample_count*4)
TTS request  : op(1) text_len(4) length_scale(4f) text_bytes(text_len)
STT response : text_len(4) text_bytes(text_len)
TTS response : sample_rate(4) pcm_byte_count(4) pcm_bytes(pcm_byte_count)

All multi-byte integers are big-endian.  PCM is float32.
"""
import asyncio
import struct
from dataclasses import dataclass

import numpy as np

OP_STT: int = 0x01
OP_TTS: int = 0x02


@dataclass
class STTRequest:
    audio: np.ndarray  # float32, 16 kHz mono


@dataclass
class TTSRequest:
    text: str
    length_scale: float


# ---------------------------------------------------------------------------
# Server-side helpers (read request, write response)
# ---------------------------------------------------------------------------

async def read_request(reader: asyncio.StreamReader) -> STTRequest | TTSRequest:
    op = (await reader.readexactly(1))[0]
    if op == OP_STT:
        (sample_count,) = struct.unpack(">I", await reader.readexactly(4))
        pcm_bytes = await reader.readexactly(sample_count * 4)
        return STTRequest(audio=np.frombuffer(pcm_bytes, dtype=np.float32).copy())
    if op == OP_TTS:
        header = await reader.readexactly(8)
        (text_len,) = struct.unpack(">I", header[:4])
        (length_scale,) = struct.unpack(">f", header[4:8])
        return TTSRequest(
            text=(await reader.readexactly(text_len)).decode("utf-8"),
            length_scale=length_scale,
        )
    raise ValueError(f"unknown op: {op:#x}")


async def write_stt_response(writer: asyncio.StreamWriter, text: str) -> None:
    text_bytes = text.encode("utf-8")
    writer.write(struct.pack(">I", len(text_bytes)) + text_bytes)
    await writer.drain()


async def write_tts_response(
    writer: asyncio.StreamWriter, sample_rate: int, pcm: np.ndarray
) -> None:
    pcm_bytes = pcm.astype(np.float32).tobytes()
    writer.write(struct.pack(">II", sample_rate, len(pcm_bytes)) + pcm_bytes)
    await writer.drain()


# ---------------------------------------------------------------------------
# Client-side helpers (write request, read response)
# ---------------------------------------------------------------------------

async def write_stt_request(writer, audio: np.ndarray) -> None:
    pcm_bytes = audio.astype(np.float32).tobytes()
    writer.write(struct.pack(">BI", OP_STT, len(audio)) + pcm_bytes)
    await writer.drain()


async def write_tts_request(writer, text: str, length_scale: float) -> None:
    text_bytes = text.encode("utf-8")
    writer.write(
        struct.pack(">BIf", OP_TTS, len(text_bytes), length_scale) + text_bytes
    )
    await writer.drain()


async def read_stt_response(reader) -> str:
    (text_len,) = struct.unpack(">I", await reader.readexactly(4))
    return (await reader.readexactly(text_len)).decode("utf-8")


async def read_tts_response(reader) -> tuple[int, np.ndarray]:
    header = await reader.readexactly(8)
    sample_rate, pcm_byte_count = struct.unpack(">II", header)
    pcm_bytes = await reader.readexactly(pcm_byte_count)
    return sample_rate, np.frombuffer(pcm_bytes, dtype=np.float32).copy()
