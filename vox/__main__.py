import asyncio
# Must be set before any other asyncio or Qt initialization.
# Python 3.11 on Windows defaults to ProactorEventLoop; asyncssh and qasync
# both require SelectorEventLoop.
asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

import argparse
import logging
import sys

import numpy as np


def _make_beep(
    sample_rate: int,
    freq: float = 523.0,
    duration: float = 0.35,
    amplitude: float = 0.12,
) -> np.ndarray:
    t = np.linspace(0, duration, int(sample_rate * duration), endpoint=False)
    # Exponential decay gives a chime character instead of a flat beep
    envelope = np.exp(-6.0 * t / duration).astype(np.float32)
    # Short fade-in (first 10 ms) to avoid a click on onset
    fade_in = min(int(sample_rate * 0.01), len(t))
    envelope[:fade_in] *= np.linspace(0.0, 1.0, fade_in, dtype=np.float32)
    return (amplitude * np.sin(2 * np.pi * freq * t) * envelope).astype(np.float32)

from vox.util.logging import configure_logging

log = logging.getLogger(__name__)


async def _tts_test() -> None:
    import time
    import logging
    from vox.audio.segmenter import segment_sentences
    from vox.audio.tts import PiperTTS
    from vox.audio.player import AudioPlayer
    from vox.config import DEFAULT_CONFIG

    log = logging.getLogger("vox.tts_test")

    paragraph = (
        "The voice interface is loading and preparing to speak. "
        "It will stream each sentence as soon as synthesis completes. "
        "By the time the third sentence is ready, the first should already be playing."
    )

    print()
    print("[vox] P1 TTS streaming test")
    sentences = segment_sentences(paragraph)
    print(f"  Segmented into {len(sentences)} sentences:")
    for i, s in enumerate(sentences):
        print(f"    S{i + 1}: {s!r}")
    print()

    print("  Loading Piper model (warm ~1-2 s)...")
    tts = PiperTTS(DEFAULT_CONFIG.piper_model_path)
    tts.load()
    print(f"  Loaded. Sample rate: {tts.sample_rate} Hz")
    print()

    player = AudioPlayer(sample_rate=tts.sample_rate)
    player.start()

    t0 = time.perf_counter()
    timestamps: dict[str, float] = {}
    audio_chunks: list = []

    def ts() -> str:
        return f"{time.perf_counter() - t0:.3f}s"

    for i, sentence in enumerate(sentences):
        print(f"  [{ts()}] S{i + 1} synthesis START")
        timestamps[f"s{i + 1}_synth_start"] = time.perf_counter()

        audio = await tts.synthesize(sentence)
        dur = len(audio) / tts.sample_rate
        timestamps[f"s{i + 1}_synth_done"] = time.perf_counter()
        print(f"  [{ts()}] S{i + 1} synthesis DONE  ({dur:.2f}s audio)")

        player.enqueue(audio)
        audio_chunks.append(audio)

        if i == 0:
            player.first_audio_started.wait(timeout=1.0)
            timestamps["s1_play_start"] = time.perf_counter()
            print(f"  [{ts()}] S1 playback STARTED")

    total_dur = sum(len(a) for a in audio_chunks) / tts.sample_rate
    print(f"\n  Waiting for {total_dur:.1f}s of audio...")
    await asyncio.sleep(total_dur + 0.5)
    player.stop()
    tts.close()

    print()
    print("  --- P1 VERIFICATION ---")
    s1_play = timestamps["s1_play_start"] - t0
    s3_done = timestamps["s3_synth_done"] - t0
    print(f"  S1 playback started:    {s1_play:.3f}s")
    print(f"  S3 synthesis done:      {s3_done:.3f}s")
    if s1_play < s3_done:
        print("  STREAMING CONFIRMED: S1 playing before S3 synthesis finished.")
    else:
        print("  Note: all synthesis completed before first playback (fast CPU?).")
    print()


async def _ssh_diag() -> None:
    """Run one piped turn through claude and print raw stdout to diagnose protocol timing."""
    import json as _json
    from vox.config import DEFAULT_CONFIG
    from vox.ssh.client import SSHClient

    cfg = DEFAULT_CONFIG
    print()
    print(f"[vox] P2 SSH protocol diagnostic -> {cfg.host}")
    print("  Connecting...")
    client = SSHClient(cfg)
    await client.connect()
    print("  Connected. Running single-turn probe via pipe...\n")

    turn = _json.dumps({
        "type": "user",
        "message": {"role": "user", "content": [{"type": "text", "text": "say hi"}]},
    })
    add_dirs = " ".join(f'--add-dir "{d}"' for d in cfg.add_dirs)
    cmd = (
        f"echo '{turn}' |"
        f" {cfg.claude_bin}"
        " --print --input-format stream-json --output-format stream-json"
        " --include-partial-messages --verbose --bare"
        f' --allowedTools "{cfg.allowed_tools}"'
        f' --disallowedTools "{cfg.disallowed_tools}"'
        " --permission-mode default"
        f" {add_dirs}"
        " 2>&1"
    )

    result = await client.conn.run(cmd, check=False, timeout=60)
    print(f"Exit status: {result.exit_status}")
    print("--- stdout+stderr ---")
    for i, line in enumerate(result.stdout.splitlines(), 1):
        print(f"  {i:3d}: {line[:160]}")
    print("--- end ---\n")
    await client.close()


async def _mic_test() -> None:
    import time
    from vox.config import DEFAULT_CONFIG
    from vox.audio.recorder import MicRecorder
    from vox.audio.stt import WhisperSTT
    from vox.audio.player import AudioPlayer

    cfg = DEFAULT_CONFIG
    loop = asyncio.get_running_loop()

    print()
    print("[vox] P4 mic + Whisper STT test")
    print(f"  Loading Whisper model ({cfg.whisper_model}, {cfg.whisper_compute_type})...")
    stt = WhisperSTT(cfg.whisper_model, cfg.whisper_compute_type, cfg.whisper_cache_dir)
    stt.load()
    print("  Loaded.")

    player = AudioPlayer(sample_rate=22050, device_name_hint=cfg.speaker_name_hint)
    player.start()
    beep = _make_beep(22050)

    recorder = MicRecorder(cfg.mic_name_hint)

    for i in range(3):
        await loop.run_in_executor(None, input, "  Press ENTER to start recording...")
        recorder.start()
        await asyncio.sleep(cfg.mic_pre_roll_ms / 1000)
        player.enqueue(beep)
        t_start = time.perf_counter()
        print("  Recording... (press ENTER to stop)")
        await loop.run_in_executor(None, input, "")
        audio = recorder.stop()
        duration = time.perf_counter() - t_start
        print(f"  Captured {duration:.1f}s / {len(audio)} samples at {WhisperSTT.SAMPLE_RATE} Hz")

        print("  Transcribing...")
        t_stt = time.perf_counter()
        text = await stt.transcribe(audio)
        stt_ms = (time.perf_counter() - t_stt) * 1000
        print(f"  [{stt_ms:.0f}ms] -> {text!r}")
        print()

    player.stop()
    stt.close()
    print("P4 mic test complete.")
    print()


async def _start_vox_server(conn, cfg) -> tuple[int, int, object]:
    """Start python -m vox.server on Aragorn.

    Returns (port, tts_sample_rate, proc).  Raises RuntimeError with stderr
    context on timeout so misconfiguration surfaces immediately.
    """
    import asyncssh

    proc = await conn.create_process(
        f"{cfg.vox_server_python} -m vox.server",
        encoding="utf-8",
        stderr=asyncssh.PIPE,
    )

    stderr_lines: list[str] = []

    async def _drain_stderr() -> None:
        async for line in proc.stderr:
            stderr_lines.append(line.rstrip())
            log.debug("vox-server stderr: %s", line.rstrip())

    asyncio.create_task(_drain_stderr())

    try:
        async with asyncio.timeout(30.0):
            async for line in proc.stdout:
                line = line.strip()
                if line.startswith("READY "):
                    parts = line.split()
                    return int(parts[1]), int(parts[2]), proc
    except asyncio.TimeoutError:
        proc.close()
        ctx = "\n".join(stderr_lines[-20:]) if stderr_lines else "(no stderr)"
        raise RuntimeError(
            f"vox-server did not send READY within 30 s\n--- stderr ---\n{ctx}"
        ) from None

    proc.close()
    raise RuntimeError("vox-server stdout closed before READY")


async def _ssh_test(
    use_mic: bool = False,
    use_hotkey: bool = False,
    ui_mode: bool = False,
    callbacks=None,
    trigger_fn=None,
    _hotkey_event=None,
) -> None:
    import time
    from vox.config import DEFAULT_CONFIG
    from vox.ssh.client import SSHClient
    from vox.ssh.claude_proc import ClaudeProcess
    from vox.ssh.protocol import TextDelta, TurnResult, ToolUse, ToolResult, AssistantMessage
    from vox.audio.remote import RemoteSTT, RemoteTTS
    from vox.audio.player import AudioPlayer
    from vox.audio.segmenter import StreamSegmenter, strip_markdown_for_tts
    from vox.audio.recorder import MicRecorder
    from vox.hotkey.listener import AppCommandTrigger
    from vox.ui.hub import UICallbacks
    if callbacks is None:
        callbacks = UICallbacks()

    cfg = DEFAULT_CONFIG
    loop = asyncio.get_running_loop()

    mode = "mic" if use_mic else "typed"
    print()
    print(f"[vox] P3/P4 {mode} REPL + live TTS -> {cfg.host}")
    print("  Connecting...")
    client = SSHClient(cfg)
    await client.connect()
    print("  Connected.")

    print("  Starting vox-server on Aragorn (loading models ~1-2 s)...")
    _server_port, _server_tts_sr, _vox_proc = await _start_vox_server(client.conn, cfg)
    print(f"  vox-server ready (port {_server_port}).")

    tts = RemoteTTS(client.conn, _server_port, _server_tts_sr)
    tts.load()
    player = AudioPlayer(sample_rate=tts.sample_rate)
    player.start()
    print(f"  TTS ready (remote Piper). {tts.sample_rate} Hz")

    hotkey_event: asyncio.Event | None = None
    trigger: AppCommandTrigger | None = None
    if ui_mode and _hotkey_event is not None:
        hotkey_event = _hotkey_event
        trigger = AppCommandTrigger(loop, trigger_fn or hotkey_event.set)
        await asyncio.sleep(0.5)
        print("  Hotkey ready.")
    elif use_hotkey:
        hotkey_event = asyncio.Event()
        trigger = AppCommandTrigger(loop, trigger_fn or hotkey_event.set)
        await asyncio.sleep(0.5)
        print("  Hotkey ready.")

    stt: RemoteSTT | None = None
    recorder: MicRecorder | None = None
    if use_mic:
        stt = RemoteSTT(client.conn, _server_port)
        stt.load()
        recorder = MicRecorder(cfg.mic_name_hint)
        print("  STT ready (remote Whisper).")

    print("  Starting claude process (probe turn ~2-5 s)...")
    proc = ClaudeProcess(client.conn, cfg)
    await proc.start()
    print(f"  Session ready: {proc.session_id}")
    print()
    if use_hotkey:
        print("  Press Beats play/pause to start recording, press again to stop and send.")
        print("  Ctrl+C to quit.")
    elif use_mic:
        print("  Press ENTER to start recording, ENTER again to stop and send.")
        print("  Type 'exit' to quit.")
    else:
        print("  Type your question. Audio streams sentence-by-sentence.")
        print("  Type 'exit' to quit.")
    print()

    # Three-tone ascending chime: signals that vox is fully ready.
    if use_hotkey:
        for freq in (440.0, 523.0, 659.0):
            player.enqueue(_make_beep(tts.sample_rate, freq=freq, duration=0.18, amplitude=0.10))

    async def _synth_enqueue(text: str) -> None:
        clean = strip_markdown_for_tts(text)
        if clean:
            audio = await tts.synthesize(clean, length_scale=cfg.speaking_rate)
            player.enqueue(audio)

    async def _get_user_input() -> str | None:
        """Return the next user utterance, or None to exit."""
        callbacks.on_state_change("idle")
        if use_hotkey and hotkey_event is not None and stt is not None and recorder is not None:
            # Wait for first Beats press to start recording.
            hotkey_event.clear()
            await hotkey_event.wait()
            log.debug("hotkey event (start) received on loop @ %.3f", time.perf_counter())
            try:
                recorder.start()
            except Exception as exc:
                print(f"  (mic error: {exc} — reconnect Beats and try again)", flush=True)
                return ""
            callbacks.on_state_change("recording")
            await asyncio.sleep(cfg.mic_pre_roll_ms / 1000)
            player.enqueue(_make_beep(tts.sample_rate))
            print("  Recording...", flush=True)
            # Wait for second Beats press to stop.
            hotkey_event.clear()
            await hotkey_event.wait()
            log.debug("hotkey event (stop) received on loop @ %.3f", time.perf_counter())
            audio = recorder.stop()
            player.enqueue(_make_beep(tts.sample_rate, freq=415.0))
            print("  Transcribing...", flush=True)
            callbacks.on_state_change("transcribing")
            text = await stt.transcribe(audio)
            if not text.strip():
                print("  (nothing heard — press Beats to try again)", flush=True)
                return ""
            print(f"  you> {text}", flush=True)
            callbacks.on_user_text(text)
            return text
        elif use_mic and stt is not None and recorder is not None:
            try:
                await loop.run_in_executor(None, input, "  Press ENTER to record...")
            except EOFError:
                return None
            recorder.start()
            callbacks.on_state_change("recording")
            await asyncio.sleep(cfg.mic_pre_roll_ms / 1000)
            player.enqueue(_make_beep(tts.sample_rate))
            print("  Recording... (press ENTER to stop)", flush=True)
            try:
                await loop.run_in_executor(None, input, "")
            except EOFError:
                recorder.stop()
                return None
            audio = recorder.stop()
            print("  Transcribing...", flush=True)
            callbacks.on_state_change("transcribing")
            text = await stt.transcribe(audio)
            if not text.strip():
                print("  (nothing transcribed — try again)", flush=True)
                return ""
            print(f"  you> {text}", flush=True)
            callbacks.on_user_text(text)
            return text
        else:
            try:
                text_in = await loop.run_in_executor(None, input, "you> ")
            except EOFError:
                return None
            if text_in:
                callbacks.on_user_text(text_in)
            return text_in

    # Heartbeat: on Windows SelectorEventLoop, select() blocks until the next
    # scheduled timer, so cross-thread callbacks (the WinRT hotkey thread's
    # call_soon_threadsafe) and SIGINT (Ctrl+C) can stall until the 30 s SSH
    # keepalive fires. A short periodic sleep keeps the loop waking promptly.
    async def _heartbeat() -> None:
        _tick = 0
        while True:
            await asyncio.sleep(0.05)
            _tick += 1
            if _tick % 10 == 0:
                log.debug("heartbeat #%d @ %.3f", _tick, time.perf_counter())

    heartbeat = asyncio.create_task(_heartbeat())

    try:
        while True:
            user_input = await _get_user_input()
            if user_input is None:
                break
            if user_input.strip().lower() in ("exit", "quit", ":q"):
                break
            if not user_input.strip():
                continue

            t0 = time.perf_counter()
            await proc.send_turn(user_input)
            callbacks.on_state_change("responding")

            # Pre-roll: enqueue silence to wake the Bluetooth stream before
            # the first TTS chunk arrives, preventing leading audio truncation.
            if cfg.pre_roll_ms > 0:
                silence = np.zeros(
                    int(cfg.pre_roll_ms / 1000 * tts.sample_rate),
                    dtype=np.float32,
                )
                player.enqueue(silence)

            segmenter = StreamSegmenter()
            synth_tasks: list[asyncio.Task] = []
            first_token_ms: float | None = None
            player.first_audio_started.clear()

            # Arm barge-in: a Beats press during playback interrupts the response.
            interrupted = False
            if use_hotkey and hotkey_event is not None:
                hotkey_event.clear()

            async for event in proc.events():
                # asyncssh buffers lines and drains them without suspending,
                # so call_soon_threadsafe callbacks never run between events.
                # One sleep(0) forces the event loop to flush the ready queue
                # (including hotkey_event.set) before we check.
                await asyncio.sleep(0)
                if use_hotkey and hotkey_event is not None and hotkey_event.is_set():
                    interrupted = True
                    break
                if isinstance(event, TextDelta):
                    if first_token_ms is None:
                        first_token_ms = (time.perf_counter() - t0) * 1000
                        print(f"[TTFT {first_token_ms:.0f}ms] ", end="", flush=True)
                    print(event.text, end="", flush=True)
                    callbacks.on_assistant_delta(event.text)
                    for sent in segmenter.feed(event.text):
                        synth_tasks.append(asyncio.create_task(_synth_enqueue(sent)))
                elif isinstance(event, ToolUse):
                    print(f"\n  [TOOL] {event.tool_name}", flush=True)
                    callbacks.on_tool(event.tool_name)
                elif isinstance(event, ToolResult):
                    print("  [TOOL RESULT]", flush=True)
                elif isinstance(event, TurnResult):
                    # Flush any trailing sentence fragment and wait for all synthesis.
                    for sent in segmenter.flush():
                        synth_tasks.append(asyncio.create_task(_synth_enqueue(sent)))
                    if synth_tasks:
                        await asyncio.gather(*synth_tasks)
                    callbacks.on_turn_complete({"subtype": event.subtype})
                    total_ms = (time.perf_counter() - t0) * 1000
                    ttft = f"{first_token_ms:.0f}" if first_token_ms else "n/a"
                    print(
                        f"\n  [RTT {total_ms:.0f}ms | TTFT {ttft}ms | {event.subtype}]",
                        flush=True,
                    )

            if interrupted:
                # Cancel in-flight synthesis and drop buffered audio.
                for t in synth_tasks:
                    t.cancel()
                player.clear()
                # Drain Claude's remaining output so its stdout doesn't fill and
                # block the next stdin write.
                async def _drain_turn() -> None:
                    async for ev in proc.events():
                        if isinstance(ev, TurnResult):
                            break
                try:
                    await asyncio.wait_for(_drain_turn(), timeout=5.0)
                except asyncio.TimeoutError:
                    pass
                print("\n  [interrupted — press Beats to continue]", flush=True)
                continue

            print()
    finally:
        heartbeat.cancel()
        if trigger is not None:
            trigger.close()
        player.stop()
        tts.close()
        if stt is not None:
            stt.close()
        await proc.close()
        _vox_proc.close()
        try:
            await asyncio.wait_for(_vox_proc.wait_closed(), timeout=5.0)
        except (asyncio.TimeoutError, Exception):
            pass
        await client.close()

    print("Session ended.")
    print()


async def _beats_trigger_test(use_ctrl_space: bool = False) -> None:
    import time

    from vox.config import DEFAULT_CONFIG

    loop = asyncio.get_running_loop()
    cfg = DEFAULT_CONFIG

    # Diagnostic: count EVERY landed press over the window (don't exit on first)
    # so the trigger's reliability is visible across multiple presses.
    presses = {"n": 0}

    def _on_press() -> None:
        presses["n"] += 1
        print(f"  CAPTURED press #{presses['n']} @ {time.perf_counter():.3f}", flush=True)

    print()
    if use_ctrl_space:
        from vox.hotkey.listener import CtrlSpaceTrigger

        print("[vox] CtrlSpaceTrigger test (pynput GlobalHotKeys, isolation)")
        print("  Installing Ctrl+Space global hotkey...")
        trigger = CtrlSpaceTrigger(loop, _on_press)
        print("  Installed. Press Ctrl+Space slowly ~5 times over 20 s.")
        print()
    else:
        from vox.hotkey.listener import AppCommandTrigger

        print("[vox] AppCommandTrigger test (WM_APPCOMMAND shell hook + SMTC toggle)")
        print("  Registering shell hook window and SMTC session...")
        trigger = AppCommandTrigger(loop, _on_press)
        await asyncio.sleep(0.5)
        print("  Ready. Press the Beats play/pause button slowly ~5 times over 20 s.")
        print()

    try:
        await asyncio.sleep(20.0)
    finally:
        trigger.close()

    print()
    print(f"  Total presses registered: {presses['n']}")
    print()


def main() -> None:
    configure_logging()

    parser = argparse.ArgumentParser(
        prog="vox",
        description="Voice-driven interface for Claude Code",
    )
    parser.add_argument(
        "--list-devices",
        action="store_true",
        help="List all audio input/output devices and exit",
    )
    parser.add_argument(
        "--hotkey-test",
        action="store_true",
        help="Test Beats play/pause interception via SMTC/WinRT and exit (isolation only)",
    )
    parser.add_argument(
        "--ctrl-space",
        action="store_true",
        help="With --hotkey-test, test the CtrlSpaceTrigger (keyboard) instead of BeatsTrigger",
    )
    parser.add_argument(
        "--tts-test",
        action="store_true",
        help="Run P1 TTS streaming verification and exit",
    )
    parser.add_argument(
        "--ssh-test",
        action="store_true",
        help="Open SSH connection to aragorn and run a typed claude REPL (P2 verify)",
    )
    parser.add_argument(
        "--mic",
        action="store_true",
        help="Use mic + Whisper STT for input in --ssh-test instead of keyboard",
    )
    parser.add_argument(
        "--hotkey",
        action="store_true",
        help="Use Beats play/pause button to trigger recording (implies --mic)",
    )
    parser.add_argument(
        "--mic-test",
        action="store_true",
        help="Record 3 utterances and print Whisper transcriptions (P4 verify)",
    )
    parser.add_argument(
        "--ssh-diag",
        action="store_true",
        help="Pipe one turn to claude and print raw output (diagnose stream-JSON protocol)",
    )
    parser.add_argument(
        "--ui",
        action="store_true",
        help="Open the graphical UI window (pywebview + WebSocket panel)",
    )
    parser.add_argument(
        "--whisper-model",
        default=None,
        metavar="MODEL",
        help="Whisper STT model name (e.g. tiny.en, base.en, small.en). Overrides config.",
    )
    parser.add_argument(
        "--tts-model",
        default=None,
        metavar="NAME",
        help="Piper TTS model short name (e.g. lessac-medium, lessac-high) or full .onnx path.",
    )
    parser.add_argument(
        "--tts-engine",
        default=None,
        choices=["piper", "windows"],
        help="TTS engine: 'piper' (local ONNX model) or 'windows' (OS neural TTS, ~0 MB RAM).",
    )
    parser.add_argument(
        "--windows-voice",
        default=None,
        metavar="NAME",
        help="Windows TTS voice name substring (e.g. 'Aria', 'Guy'). Used with --tts-engine windows.",
    )
    parser.add_argument(
        "--list-tts-voices",
        action="store_true",
        help="List available Windows TTS voices and exit.",
    )
    args = parser.parse_args()

    # Apply model overrides — mutate DEFAULT_CONFIG before any dispatch so every
    # code path (--ssh-test, --ui, --tts-test, etc.) sees the same values.
    from vox.config import DEFAULT_CONFIG
    from pathlib import Path as _Path

    if args.whisper_model:
        DEFAULT_CONFIG.whisper_model = args.whisper_model
        log.info("whisper model override: %s", args.whisper_model)

    if args.tts_model:
        name = args.tts_model
        if name.endswith(".onnx") or _Path(name).is_absolute():
            DEFAULT_CONFIG.piper_model_path = _Path(name)
        else:
            DEFAULT_CONFIG.piper_model_path = (
                DEFAULT_CONFIG.piper_model_path.parent / f"en_US-{name}.onnx"
            )
        log.info("piper model override: %s", DEFAULT_CONFIG.piper_model_path)
        if not DEFAULT_CONFIG.piper_model_path.exists():
            print(
                f"[vox] TTS model not found: {DEFAULT_CONFIG.piper_model_path}\n"
                f"      Download it first — see scripts/download_piper_model.py",
                file=sys.stderr,
            )
            sys.exit(1)

    if args.tts_engine:
        DEFAULT_CONFIG.tts_engine = args.tts_engine
        log.info("tts engine override: %s", args.tts_engine)

    if args.windows_voice:
        DEFAULT_CONFIG.windows_tts_voice = args.windows_voice
        log.info("windows tts voice override: %s", args.windows_voice)

    if args.list_tts_voices:
        from winsdk.windows.media.speechsynthesis import SpeechSynthesizer
        voices = list(SpeechSynthesizer.all_voices)
        print("[vox] Available Windows TTS voices:")
        for v in voices:
            print(f"  {v.display_name}  ({v.language})")
        sys.exit(0)

    if args.list_devices:
        from vox.audio.devices import list_audio_devices
        list_audio_devices()
        sys.exit(0)

    if args.hotkey_test:
        asyncio.run(_beats_trigger_test(use_ctrl_space=args.ctrl_space))
        sys.exit(0)

    if args.tts_test:
        asyncio.run(_tts_test())
        sys.exit(0)

    if args.mic_test:
        asyncio.run(_mic_test())
        sys.exit(0)

    if args.ssh_test:
        asyncio.run(_ssh_test(use_mic=args.mic or args.hotkey, use_hotkey=args.hotkey))
        sys.exit(0)

    if args.ssh_diag:
        asyncio.run(_ssh_diag())
        sys.exit(0)

    if args.ui:
        from vox.ui.app import run_ui
        from vox.config import DEFAULT_CONFIG
        run_ui(DEFAULT_CONFIG)
        sys.exit(0)

    parser.print_help()
    sys.exit(0)


if __name__ == "__main__":
    main()
