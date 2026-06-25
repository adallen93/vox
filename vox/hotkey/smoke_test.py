"""P0 smoke-test: pynput hook check + media-key suppression attempt."""
import threading

from pynput import keyboard

VK_MEDIA_PLAY_PAUSE = 0xB3


class _SpyListener(keyboard.Listener):
    """Dumps all raw vk codes seen by WH_KEYBOARD_LL."""

    def __init__(self) -> None:
        self._any: threading.Event = threading.Event()
        super().__init__()

    def win32_event_filter(self, msg, data):  # type: ignore[override]
        print(f"  [spy] msg=0x{msg:04X}  vk=0x{data.vkCode:02X}  scan=0x{data.scanCode:02X}")
        self._any.set()
        return True

    @property
    def any_seen(self) -> threading.Event:
        return self._any


class _SuppressListener(keyboard.Listener):
    """Intercepts and suppresses Key.media_play_pause."""

    def __init__(self) -> None:
        self._detected: threading.Event = threading.Event()
        super().__init__()

    def win32_event_filter(self, msg, data):  # type: ignore[override]
        if data.vkCode == VK_MEDIA_PLAY_PAUSE:
            self.suppress_event()
            self._detected.set()
        return True

    @property
    def detected(self) -> threading.Event:
        return self._detected


def run_key_spy(timeout: float = 8.0) -> bool:
    """Phase 1: confirm pynput hook is working at all. Returns True if any key seen."""
    print()
    print("[vox] Phase 1 — pynput hook sanity check (on_press callback)")
    print(f"  Press ANY keyboard key within {timeout:.0f} s...")
    print()
    done = threading.Event()

    def on_press(key):
        try:
            vk = getattr(key, "vk", None)
            print(f"  [spy/callback] key={key!r}  vk={vk!r}")
        except Exception:
            pass
        done.set()

    with keyboard.Listener(on_press=on_press) as listener:
        ok = done.wait(timeout=timeout)

    if ok:
        print("  Hook is alive.")
    else:
        print(f"  Nothing seen in {timeout:.0f} s.")
        print("  Try: run the terminal as Administrator, or check pynput install.")
    return ok


def run_fallback_test(timeout: float = 10.0) -> bool:
    """Phase 3: confirm Ctrl+Space is detectable as the fallback hotkey."""
    print()
    print("[vox] Phase 3 — fallback hotkey check (Ctrl+Space)")
    print(f"  Press Ctrl+Space within {timeout:.0f} s...")
    print()
    done = threading.Event()

    def on_activate() -> None:
        print("  [fallback] Ctrl+Space detected!")
        done.set()

    with keyboard.GlobalHotKeys({"<ctrl>+<space>": on_activate}):
        ok = done.wait(timeout=timeout)

    if ok:
        print("  RESULT: Ctrl+Space works. Fallback hotkey confirmed.")
        print("  Note: full suppression (so editors don't also see it) validated at P5.")
    else:
        print(f"  RESULT: no Ctrl+Space in {timeout:.0f} s.")
    print()
    return ok


def run_smoke_test(timeout: float = 15.0) -> None:
    # Phase 1: confirm pynput hook works with a regular key
    hook_ok = run_key_spy()

    print()
    print("[vox] Phase 2 — Beats play/pause suppression test")
    if not hook_ok:
        print("  Skipping: hook not working. Check pynput install / admin rights.")
        print()
        return

    print(f"  Press the Beats play/pause button within {timeout:.0f} s...")
    print()

    listener = _SuppressListener()
    listener.start()
    try:
        hit = listener.detected.wait(timeout=timeout)
    finally:
        listener.stop()

    if hit:
        print("  RESULT: vk=0xB3 intercepted + suppress_event() called.")
        print()
        print("  >>> Did Spotify / system audio respond to the press? <<<")
        print("  If NO  -> suppression works. Proceed to P1.")
        print("  If YES -> Beats button bypasses WH_KEYBOARD_LL (WM_APPCOMMAND path).")
        print("             Switch to fallback hotkey (Ctrl+Space).")
        print()
    else:
        print(f"  RESULT: no media_play_pause seen in {timeout:.0f} s.")
        print("  Diagnosis: Beats button delivered as WM_APPCOMMAND (bypasses LL hook).")
        print("  Switching to fallback hotkey...")
        run_fallback_test()
        print("  *** P0 complete with fallback hotkey. Add UI warning before P1. ***")
        print()
