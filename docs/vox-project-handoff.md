# vox — Project Handoff for UI Session

**Date:** 2026-06-23  
**Purpose:** Comprehensive state dump for the UI implementation session.
Covers architecture, every major implementation decision and its rationale,
known limitations, and what a UI session should tackle first.

---

## What vox is

A voice-driven Claude Code front-end for Windows 11. The user presses the
Beats Studio³ play/pause button to start recording, speaks a question,
presses again to stop, and hears Claude's response read aloud. Fully
hands-free once running.

---

## System architecture

```
Windows machine
│
├── AppCommandTrigger          ctypes RegisterShellHookWindow
│   └── hotkey_event ──────────────────────────────────────────┐
│                                                               │
├── MicRecorder                sounddevice InputStream          │
│   └── Beats Studio³ (HFP)                                    │
│                                                               ▼
├── WhisperSTT                 faster-whisper base.en int8   _get_user_input()
│                                                               │
├── asyncssh ─────────────────────────────────── aragorn (Linux)
│   └── ClaudeProcess          claude CLI, stream-json I/O      │
│       └── events() ◄──────────────────────────────── Claude response stream
│                │
│                ├── StreamSegmenter     sentence boundary detection
│                │
│                └── PiperTTS           en_US-lessac-high.onnx (local)
│                    └── AudioPlayer    sounddevice OutputStream
│                        └── Beats Studio³ (A2DP)
│
└── asyncio event loop         WindowsSelectorEventLoopPolicy
    └── _heartbeat()           50ms wakeup (prevents 30s SSH stall)
```

**Key point:** Claude runs on the remote Linux server (aragorn) over SSH.
The Windows machine handles all audio I/O, STT, and TTS locally. This split
exists because `claude --print --input-format stream-json` does not work
headlessly on Windows.

---

## File map

| File | Role |
|------|------|
| `vox/__main__.py` | CLI entry point, full session loop, chimes |
| `vox/config.py` | All tunable parameters (pre-roll, device hints, etc.) |
| `vox/hotkey/listener.py` | `AppCommandTrigger`, `CtrlSpaceTrigger`, `BeatsTrigger` |
| `vox/audio/player.py` | `AudioPlayer` — queue-backed sounddevice output |
| `vox/audio/recorder.py` | `MicRecorder` — sounddevice input |
| `vox/audio/devices.py` | Device name→index lookup |
| `vox/ssh/client.py` | asyncssh connection wrapper |
| `vox/ssh/claude_proc.py` | `ClaudeProcess` — launches claude on aragorn, streams events |
| `vox/ssh/protocol.py` | Event dataclasses: `TextDelta`, `TurnResult`, `ToolUse`, etc. |
| `vox/tts/piper.py` | `PiperTTS` — wraps piper-tts Python package |
| `vox/stt/whisper.py` | `WhisperSTT` — wraps faster-whisper |
| `vox/text/segmenter.py` | `StreamSegmenter` — feeds token stream, yields complete sentences |
| `docs/beats-button-investigation.md` | Full investigation log of the Beats button problem |

---

## Aragorn setup

Claude runs in `/home/aallen/vox-harness/` on aragorn. This directory has
its own `CLAUDE.md` that overrides the global dev-workflow rules:

- Responses under 150 words
- No markdown (no bullets, headers, bold)
- Natural spoken sentences only
- No skills, validation tiers, or AskUserQuestion tool

The claude command in `_build_command()` is prefixed with
`cd /home/aallen/vox-harness && ` so Claude picks up that directory's
CLAUDE.md first, then inherits the parent `/home/aallen/CLAUDE.md`.

---

## Key implementation decisions

### 1. Beats button via RegisterShellHookWindow (pure ctypes)

**What it does:** Creates a hidden Win32 window, registers it with
`RegisterShellHookWindow`, and listens for `HSHELL_APPCOMMAND` notifications
carrying `APPCOMMAND_MEDIA_PLAY_PAUSE`.

**Why this approach:** The Beats button is not a keyboard event. It travels
via Bluetooth AVRCP → Windows SMTC. Every input-layer approach (pynput,
RegisterHotKey, Raw Input, WM_APPCOMMAND on own window) failed. A full
investigation is in `docs/beats-button-investigation.md`.

**Why not SMTC/WinRT (the original approach):**  
SMTC requires `MediaPlayer.play()` to become the "current" session.
`play()` opens the Beats A2DP audio device. sounddevice holds A2DP
exclusively for TTS output. The WinRT thread pool thread that delivers
`button_pressed` callbacks blocks waiting for A2DP, so all callbacks arrive
only when Ctrl+C releases sounddevice. This is the root cause of the
"presses only fire at Ctrl+C" bug that consumed many sessions.

**Trade-off accepted:** Without an SMTC session, Windows tracks AVRCP state
internally and drops roughly every other command (the one that matches its
current state). Effective capture rate ≈ 50%. Start recording usually
works on the first press; stop recording typically takes 3–4 presses.
The user finds this acceptable.

### 2. 50ms heartbeat task

Windows `SelectorEventLoop.select()` can block up to 30 seconds (the SSH
keepalive interval) with no I/O. This means `call_soon_threadsafe` callbacks
from the hotkey thread (and Ctrl+C) stall for up to 30s. A background task
that does `await asyncio.sleep(0.05)` every 50ms keeps the loop waking
promptly. Without it, button presses appear delayed or bunched.

### 3. `await asyncio.sleep(0)` in the event loop

asyncssh buffers multiple lines from the SSH stream and yields them from
its async iterator without suspending to the event loop between lines.
This means `call_soon_threadsafe(hotkey_event.set)` is queued but never
runs while asyncssh is draining its buffer — so barge-in interrupt checks
always see the event as unset. One `await asyncio.sleep(0)` at the top of
each event loop iteration flushes the ready queue before checking. This is
required for the interrupt feature to work at all.

### 4. Pre-roll silence (2000ms)

Bluetooth A2DP goes into a low-power state during silence and needs
~1–2 seconds to re-establish when audio resumes. Without pre-roll, the
first sentence of every Claude response is truncated. The solution is to
enqueue 2000ms of silence before the first TTS chunk of each turn, giving
A2DP time to wake up. Controlled by `config.pre_roll_ms`.

### 5. Stream segmentation for TTS

TTS synthesis is triggered per sentence (via `StreamSegmenter`), not per
token. Triggering per token would produce choppy audio (hundreds of tiny
audio chunks). Waiting for the full response would add seconds of latency.
Per-sentence hits the sweet spot: the first sentence starts playing while
Claude is still generating, and audio is natural-length chunks.

### 6. Separate mic pre-roll (750ms)

Similar to the output pre-roll: the Beats microphone (HFP profile) also
needs time to initialize before the first words are captured. `config.mic_pre_roll_ms`
adds a delay between `recorder.start()` and the start chime so the first
words aren't dropped.

### 7. AudioPlayer thread with silent failure handling

The AudioPlayer runs a background thread with a `queue.Queue`. The thread
holds an `sd.OutputStream` open for the session lifetime. If the Beats A2DP
connection drops (e.g., from rapid button pressing), `stream.write()` throws.
This is now caught and logged; the thread exits gracefully. The main loop
continues — the player just goes silent until the session is restarted.
`MicRecorder.start()` errors are also caught gracefully with a helpful message
instead of crashing the session.

---

## Audio signal design

| Event | Frequency | Notes |
|-------|-----------|-------|
| Start recording | 523 Hz (C5) | Exponential decay chime |
| Stop recording | 415 Hz (G#4) | 4 semitones lower than start |
| Session ready | 440 → 523 → 659 Hz | Three-tone ascending arpeggio |

---

## Known limitations

### AVRCP parity (~50% capture rate)
Stop recording takes 3–4 presses. This is a fundamental property of the
shell hook approach with no SMTC session. Fixing it requires either:
(a) finding a way to open an SMTC session without occupying A2DP, or
(b) using a different trigger (Ctrl+Space works 100% but requires keyboard).

### Barge-in / interrupt (unreliable)
The interrupt mechanism is implemented: `await asyncio.sleep(0)` +
`hotkey_event.is_set()` check on each event. It doesn't reliably fire during
TTS playback. Suspected cause: AVRCP parity means the button press during
playback may be a no-op. Not fully debugged; left for a future session.

### No session persistence
Each launch starts a fresh Claude session. Conversation history is lost
when the process exits. `ClaudeProcess.session_id` is captured but not
persisted or restored.

### No reconnect
If the SSH connection to aragorn drops, vox crashes. No retry or reconnect
logic is implemented.

### No UI
All output is terminal-only. The text of Claude's responses prints to
stdout as it streams in but is not displayed anywhere the user can read
(by design, since the interface is voice-first). This is the primary
next development area.

---

## What the UI session should build

### Immediate goal
A small, always-on-top floating window (or system tray indicator) showing:

1. **State indicator** — idle / recording / transcribing / responding
2. **Last exchange** — the user's transcribed input and Claude's text response
   (even though responses are spoken, having text is useful for review)
3. **Volume / speaking indicator** — optional mic level during recording

### Technology recommendation: PyQt6 + qasync *(revised — see plan)*

> **Note:** The UI implementation plan locks a different stack: **pywebview (WebView2) + aiohttp + xterm.js**, with asyncio on a background thread and no qasync. The PyQt6+qasync recommendation below is superseded. It is kept here for historical context.

### Original recommendation (superseded): PyQt6 + qasync

The entire vox session runs on an asyncio event loop
(`asyncio.run(_ssh_test(...))`). Any UI framework must integrate with that
loop, not replace it.

**PyQt6 + qasync** is the best fit:
- `qasync.QEventLoop` replaces `asyncio.run()`, running Qt and asyncio
  on the same thread
- Qt signals work natively with `async def` slots
- Native Windows look and feel
- Good Python support

**Alternative:** `tkinter` is simpler but asyncio integration is awkward
(requires manual polling). `webview` (FastAPI + browser) is easy to build
but heavyweight. WinUI3 / WinForms require C# interop.

### Integration approach

The session loop in `__main__.py` needs to emit state changes that the UI
can react to. The cleanest approach is a thin callback/signal layer:

```python
# Proposed: pass a ui_callbacks object into _ssh_test()
@dataclass
class UICallbacks:
    on_state_change: Callable[[str], None]      # "idle"|"recording"|"transcribing"|"responding"
    on_user_text: Callable[[str], None]         # transcribed user input
    on_assistant_text: Callable[[str], None]    # streaming Claude text
    on_assistant_done: Callable[[], None]       # turn complete
```

The session loop already has the right structure — it's just missing the
callback hooks. Adding them is localized to `_ssh_test()` in `__main__.py`.

### Files to change for UI integration

- `vox/__main__.py`: Add callback hooks, replace `asyncio.run()` with
  `qasync.QEventLoop`
- `vox/ui/` (new): Qt window, state machine, tray icon
- `pyproject.toml`: Add `PyQt6`, `qasync` dependencies

---

## Running vox

```powershell
# From C:\Users\adall\Home\Projects\vox
uv run vox --ssh-test --mic --hotkey   # full live session
uv run vox --hotkey-test               # test Beats button only (20s)
uv run vox --tts-test                  # test TTS audio only
uv run vox --mic-test                  # test mic + STT only
```

Beats must be connected and set as default audio device before starting.
The boot chime (three ascending tones) plays when the session is fully ready.
