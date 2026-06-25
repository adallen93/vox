import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Config:
    # --- SSH / aragorn ---
    host: str = "100.81.196.17"       # aragorn Tailscale IP
    user: str = "aallen"
    known_hosts: Path = field(default_factory=lambda: Path.home() / ".ssh" / "known_hosts")
    client_key_path: Path = field(default_factory=lambda: Path.home() / ".ssh" / "id_ed25519")
    ssh_keepalive_interval: int = 30  # seconds

    # --- Claude (full path required; not on non-interactive SSH PATH) ---
    claude_bin: str = "/home/aallen/.local/bin/claude"

    # --- vox-server (runs on Aragorn; full path to venv python) ---
    vox_server_python: str = "/home/aallen/vox/.venv/bin/python"

    # --- Add-dir roots forwarded to --add-dir on aragorn ---
    add_dirs: list[str] = field(default_factory=lambda: ["/home/aallen"])

    # --- Hotkey (SMTC / Beats Studio³) ---
    # The Beats play/pause button is an AVRCP command routed to the active
    # Windows media session — it bypasses all keyboard hooks entirely.
    # vox intercepts it by holding a silent SMTC session (see hotkey/listener.py).
    # Validated in isolation (--hotkey-test, no sounddevice): 11/11 presses captured;
    # coexistence with Chrome confirmed.  Live-session coexistence (sounddevice +
    # event loop) is verified separately via --ssh-test --mic --hotkey.
    silence_wav: Path = field(
        default_factory=lambda: Path(__file__).parent / "assets" / "silence.wav"
    )

    # --- Device name hints (matched as case-insensitive substrings at runtime) ---
    mic_name_hint: str = "Beats"
    speaker_name_hint: str = "Beats"

    # --- STT ---
    whisper_model: str = "base.en"
    whisper_compute_type: str = "int8"
    whisper_cache_dir: Path = field(
        default_factory=lambda: Path.home() / ".cache" / "faster-whisper"
    )

    # --- TTS ---
    piper_model_path: Path = field(
        default_factory=lambda: Path.home() / ".local" / "share" / "piper" / "en_US-lessac-medium.onnx"
    )
    # Piper length_scale: 1.0 = normal, 0.8 = ~25% faster, 1.25 = slower.
    # No pitch change — pure articulation rate.
    speaking_rate: float = 1.0
    # Silence (ms) enqueued before the first TTS chunk each turn.
    # Wakes Bluetooth headphones before speech arrives so the first sentence
    # isn't truncated by the A2DP stream re-establishment delay.
    pre_roll_ms: int = 2000
    # Delay (ms) between opening the mic stream and prompting the user to speak.
    # Gives the Bluetooth mic time to wake up so the first words aren't dropped.
    mic_pre_roll_ms: int = 750

    # --- TTS engine ---
    tts_engine: str = "windows"         # "piper" or "windows"
    windows_tts_voice: str | None = None  # e.g. "Aria" → Microsoft Aria (Neural)
    # WinRT SpeechSynthesizerOptions.speaking_rate: 0.5–6.0, 1.0 = normal.
    # 0.85 is noticeably slower without sounding unnatural.
    windows_tts_rate: float = 0.85

    # --- UI ---
    ui_port: int = 7654
    ui_host: str = "127.0.0.1"
    ui_window_mode: str = "compact"  # compact = small always-on-top; full = normal window
    wt_settings_path: Path = field(
        default_factory=lambda: Path(os.environ.get("LOCALAPPDATA", ""))
        / "Packages/Microsoft.WindowsTerminal_8wekyb3d8bbwe/LocalState/settings.json"
    )

    # --- Claude permission flags ---
    # bypassPermissions auto-approves tool calls without interactive prompts.
    # The allowedTools whitelist is the safety rail — only read + git-read
    # commands are listed, so write operations are unreachable even with bypass.
    permission_mode: str = "bypassPermissions"
    allowed_tools: str = (
        "Read Grep Glob "
        "Bash(git status:*) Bash(git log:*) Bash(git diff:*) "
        "Bash(git show:*) Bash(git branch:*) Bash(git blame:*) "
        "Bash(git remote:*)"
    )
    disallowed_tools: str = "Edit Write NotebookEdit"


DEFAULT_CONFIG = Config()
