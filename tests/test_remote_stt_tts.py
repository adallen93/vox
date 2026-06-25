"""
Live integration tests for RemoteSTT / RemoteTTS over SSH tunnel.

Requires Aragorn to be reachable and the vox repo cloned + deps installed there.

Run standalone:  python tests/test_remote_stt_tts.py
Run via pytest:  pytest tests/test_remote_stt_tts.py -v --tb=short
"""
import asyncio
import sys

import numpy as np


async def _run() -> None:
    from vox.config import DEFAULT_CONFIG
    from vox.ssh.client import SSHClient
    from vox.audio.remote import RemoteSTT, RemoteTTS
    from vox.__main__ import _start_vox_server
    from vox.server.protocol import write_tts_request

    cfg = DEFAULT_CONFIG
    client = SSHClient(cfg)
    print("Connecting SSH...", flush=True)
    await client.connect()

    print("Starting vox-server...", flush=True)
    port, tts_sr, proc = await _start_vox_server(client.conn, cfg)
    print(f"  READY: port={port} tts_sr={tts_sr}", flush=True)

    stt = RemoteSTT(client.conn, port)
    tts = RemoteTTS(client.conn, port, tts_sr)
    passed = 0
    failed = 0

    async def check(name: str, coro) -> None:
        nonlocal passed, failed
        print(f"\n[?] {name}", flush=True)
        try:
            await coro
            print(f"  PASS", flush=True)
            passed += 1
        except Exception as exc:
            print(f"  FAIL: {exc}", flush=True)
            failed += 1

    # --- Test 1: STT happy path ---
    async def t1():
        audio = np.zeros(16000, dtype=np.float32)  # 1 s silence
        result = await stt.transcribe(audio)
        assert isinstance(result, str), f"expected str, got {type(result)}"
        print(f"  transcript: {result!r}", flush=True)

    await check("STT: silence → text (str, no crash)", t1())

    # --- Test 2: TTS happy path ---
    async def t2():
        pcm = await tts.synthesize("Hello, this is a test.", length_scale=1.0)
        assert isinstance(pcm, np.ndarray), f"expected ndarray, got {type(pcm)}"
        assert pcm.dtype == np.float32, f"expected float32, got {pcm.dtype}"
        assert len(pcm) > 0, "empty PCM"
        dur = len(pcm) / tts_sr
        print(f"  {len(pcm)} samples @ {tts_sr} Hz ({dur:.2f}s)", flush=True)

    await check("TTS: text → float32 PCM", t2())

    # --- Test 3: length_scale ---
    async def t3():
        fast = await tts.synthesize("Testing speed.", length_scale=0.8)
        slow = await tts.synthesize("Testing speed.", length_scale=1.2)
        assert len(fast) < len(slow), f"fast={len(fast)}, slow={len(slow)}"
        print(f"  fast={len(fast)}  slow={len(slow)}", flush=True)

    await check("TTS: length_scale 0.8 shorter than 1.2", t3())

    # --- Test 4: dropped channel cancellation ---
    async def t4():
        reader, writer = await client.conn.open_connection("127.0.0.1", port)
        await write_tts_request(
            writer,
            "This is a fairly long sentence intended to take a moment to synthesize.",
            1.0,
        )
        writer.close()  # drop before reading response
        await asyncio.sleep(0.5)
        # Server must still respond to a subsequent request
        pcm = await tts.synthesize("Still alive.", 1.0)
        assert len(pcm) > 0, "server wedged after dropped channel"
        print(f"  server alive: {len(pcm)} samples", flush=True)

    await check("Dropped channel → server stays alive", t4())

    # --- Teardown ---
    proc.close()
    try:
        await asyncio.wait_for(proc.wait_closed(), timeout=5.0)
    except Exception:
        pass
    await client.close()

    print(f"\nResults: {passed} passed, {failed} failed", flush=True)
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(_run())
