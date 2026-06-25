"""
vox-server: runs on Aragorn, loads WhisperSTT + PiperTTS once, then serves
one request per TCP connection over SSH direct-tcpip channels.

Startup:
    python -m vox.server

Lifecycle:
    - Prints "READY <port> <tts_sample_rate>" to stdout once models are loaded
      and the socket is bound.
    - Exits cleanly when stdin reaches EOF (SSH channel closed).
    - A dropped client channel during synthesis is treated as cancellation.
"""
import asyncio
import logging
import sys

from vox.audio.stt import WhisperSTT
from vox.audio.tts import PiperTTS
from vox.config import DEFAULT_CONFIG
from vox.server.protocol import (
    STTRequest,
    TTSRequest,
    read_request,
    write_stt_response,
    write_tts_response,
)

log = logging.getLogger(__name__)

_tasks: set[asyncio.Task] = set()


async def _handle(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    stt: WhisperSTT,
    tts: PiperTTS,
) -> None:
    try:
        request = await read_request(reader)
    except Exception as exc:
        log.debug("read_request failed: %s", exc)
        writer.close()
        return

    if isinstance(request, STTRequest):
        work_task: asyncio.Task = asyncio.create_task(stt.transcribe(request.audio))
    else:
        work_task = asyncio.create_task(
            tts.synthesize(request.text, request.length_scale)
        )

    # Watch for EOF on the channel while work is running.  A dropped
    # direct-tcpip channel sends EOF on the reader side.
    eof_task: asyncio.Task = asyncio.create_task(reader.read(1))
    try:
        done, _ = await asyncio.wait(
            [work_task, eof_task], return_when=asyncio.FIRST_COMPLETED
        )
        if eof_task in done:
            work_task.cancel()
            try:
                await work_task
            except asyncio.CancelledError:
                log.debug("work cancelled by dropped channel")
            return

        eof_task.cancel()
        try:
            await eof_task
        except asyncio.CancelledError:
            pass

        result = work_task.result()
        if isinstance(request, STTRequest):
            await write_stt_response(writer, result)
        else:
            await write_tts_response(writer, tts.sample_rate, result)

    except (BrokenPipeError, ConnectionResetError, asyncio.IncompleteReadError):
        log.debug("client disconnected during response write")
    finally:
        try:
            writer.close()
            await writer.wait_closed()
        except Exception:
            pass


def _make_handler(stt: WhisperSTT, tts: PiperTTS):
    def handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        task = asyncio.create_task(_handle(reader, writer, stt, tts))
        _tasks.add(task)
        task.add_done_callback(_tasks.discard)
    return handler


async def _watch_stdin(server: asyncio.AbstractServer) -> None:
    """Shut down when stdin closes (SSH channel EOF)."""
    loop = asyncio.get_running_loop()
    stdin_reader = asyncio.StreamReader()
    proto = asyncio.StreamReaderProtocol(stdin_reader)
    await loop.connect_read_pipe(lambda: proto, sys.stdin.buffer)
    while True:
        data = await stdin_reader.read(64)
        if not data:
            log.debug("stdin EOF — shutting down")
            server.close()
            return


async def main() -> None:
    cfg = DEFAULT_CONFIG

    print("Loading STT model...", file=sys.stderr, flush=True)
    stt = WhisperSTT(cfg.whisper_model, cfg.whisper_compute_type, cfg.whisper_cache_dir)
    stt.load()

    print("Loading TTS model...", file=sys.stderr, flush=True)
    tts = PiperTTS(cfg.piper_model_path)
    tts.load()

    server = await asyncio.start_server(
        _make_handler(stt, tts), "127.0.0.1", 0
    )
    port = server.sockets[0].getsockname()[1]
    # READY line is the handshake the Windows client waits for.
    print(f"READY {port} {tts.sample_rate}", flush=True)
    log.debug("vox-server listening on 127.0.0.1:%d (tts_sr=%d)", port, tts.sample_rate)

    asyncio.create_task(_watch_stdin(server))

    async with server:
        await server.serve_forever()

    stt.close()
    tts.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG, stream=sys.stderr)
    asyncio.run(main())
