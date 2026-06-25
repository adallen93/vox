"""
Hotkey triggers for vox.

AppCommandTrigger (primary for Beats hardware) — intercepts the Beats
play/pause button via RegisterShellHookWindow (HSHELL_APPCOMMAND).  No SMTC
session, no audio device opened → no sounddevice/A2DP conflict.  Captures
~every other press due to AVRCP PLAY/PAUSE alternation; if the first press
is a no-op, the second will fire.

BeatsTrigger (SMTC/WinRT, superseded) — kept for reference.  Worked in
isolation but the WinRT callback thread is blocked when sounddevice holds
the Beats A2DP device, preventing delivery in live sessions.

MediaPlayPauseTrigger — intercepts VK_MEDIA_PLAY_PAUSE (0xB3) via a pynput
low-level keyboard hook.  Works for physical keyboard media keys only — does
NOT fire for the Beats play/pause button (AVRCP/SMTC path, not a keystroke).

CtrlSpaceTrigger — proven keyboard fallback via pynput GlobalHotKeys.
100% reliable; requires being near a keyboard.
"""
import asyncio
import logging
from pathlib import Path

log = logging.getLogger(__name__)


class MediaPlayPauseTrigger:
    """Intercepts the Beats (or any) media play/pause key via a pynput hook.

    Suppresses VK_MEDIA_PLAY_PAUSE so Spotify/YouTube won't see it while
    vox is running.  All other keys pass through untouched.
    Works alongside sounddevice audio streams.
    """

    def __init__(
        self,
        loop: asyncio.AbstractEventLoop,
        on_press: callable,
    ) -> None:
        self._loop = loop
        self._on_press = on_press
        from pynput import keyboard

        outer_loop = loop
        outer_on_press = on_press

        class _Listener(keyboard.Listener):
            _VK = 0xB3       # VK_MEDIA_PLAY_PAUSE
            _KD = 0x0100     # WM_KEYDOWN
            _SKD = 0x0104    # WM_SYSKEYDOWN

            def __init__(self):
                # Dummy on_press required — pynput 1.8.x won't fire the hook
                # if win32_event_filter is the only override.
                super().__init__(on_press=lambda k: None)

            def win32_event_filter(self, msg, data):
                if data.vkCode == self._VK:
                    self.suppress_event()
                    if msg in (self._KD, self._SKD):
                        log.debug("MediaPlayPauseTrigger: key pressed")
                        outer_loop.call_soon_threadsafe(outer_on_press)
                # All other keys: suppress_event() not called, so they pass through.

        self._listener = _Listener()
        self._listener.start()

    def close(self) -> None:
        self._listener.stop()


class CtrlSpaceTrigger:
    """Intercepts Ctrl+Space globally via pynput GlobalHotKeys.

    Does not suppress the Beats play/pause button — use MediaPlayPauseTrigger
    if you want to use the physical play/pause key as the vox trigger.
    Works alongside sounddevice audio streams.
    """

    def __init__(
        self,
        loop: asyncio.AbstractEventLoop,
        on_press: callable,
    ) -> None:
        self._loop = loop
        self._on_press = on_press
        from pynput import keyboard
        self._listener = keyboard.GlobalHotKeys(
            {"<ctrl>+<space>": self._on_hotkey},
        )
        self._listener.start()

    def _on_hotkey(self) -> None:
        log.debug("CtrlSpaceTrigger: hotkey pressed")
        self._loop.call_soon_threadsafe(self._on_press)

    def close(self) -> None:
        self._listener.stop()


class AppCommandTrigger:
    """Intercepts the Beats play/pause button via RegisterShellHookWindow.

    Mechanism:
      A hidden top-level window is registered with RegisterShellHookWindow so
      the shell delivers HSHELL_APPCOMMAND notifications system-wide,
      regardless of foreground state.  No SMTC session is created and no audio
      device is opened, so there is no conflict with sounddevice's hold on the
      Beats A2DP output.

    Limitation:
      The Beats alternates AVRCP PLAY and PAUSE commands on successive presses.
      Without an SMTC session to toggle state, Windows drops roughly every
      other command (the one that matches its internally tracked state).
      Effective capture rate is ~50 % — if the first press is a no-op, the
      second will fire.
    """

    def __init__(
        self,
        loop: asyncio.AbstractEventLoop,
        on_press: callable,
    ) -> None:
        import ctypes
        import ctypes.wintypes as wt
        import threading

        self._loop = loop
        self._on_press = on_press

        # 64-bit-correct types (ctypes.wintypes.LPARAM is c_long = 32-bit,
        # which overflows on 64-bit Windows).
        LRESULT = ctypes.c_ssize_t
        WPARAM  = ctypes.c_size_t
        LPARAM  = ctypes.c_ssize_t
        WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wt.HWND, wt.UINT, WPARAM, LPARAM)

        user32   = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32

        user32.DefWindowProcW.restype        = LRESULT
        user32.DefWindowProcW.argtypes       = [wt.HWND, wt.UINT, WPARAM, LPARAM]
        user32.PostQuitMessage.argtypes      = [ctypes.c_int]
        user32.RegisterShellHookWindow.argtypes   = [wt.HWND]
        user32.DeregisterShellHookWindow.argtypes = [wt.HWND]
        user32.RegisterWindowMessageW.restype  = wt.UINT
        user32.RegisterWindowMessageW.argtypes = [wt.LPCWSTR]
        user32.ShowWindow.argtypes   = [wt.HWND, ctypes.c_int]
        user32.PostMessageW.argtypes = [wt.HWND, wt.UINT, WPARAM, LPARAM]

        WM_DESTROY          = 0x0002
        WS_OVERLAPPEDWINDOW = 0x00CF0000
        WS_EX_TOOLWINDOW    = 0x00000080
        SW_HIDE             = 0
        HSHELL_APPCOMMAND   = 12
        APPCOMMAND_MEDIA_PLAY_PAUSE = 14

        self._user32     = user32
        self._hwnd       = [0]
        self._WM_DESTROY = WM_DESTROY
        self._shell_msg  = [0]

        def wnd_proc(hwnd: int, msg: int, wparam: int, lparam: int) -> int:
            if self._shell_msg[0] and msg == self._shell_msg[0]:
                if (wparam & 0xFFFF) == HSHELL_APPCOMMAND:
                    cmd = (lparam >> 16) & 0x0FFF
                    if cmd == APPCOMMAND_MEDIA_PLAY_PAUSE:
                        import time as _time
                        log.debug(
                            "AppCommandTrigger: MEDIA_PLAY_PAUSE @ %.3f",
                            _time.perf_counter(),
                        )
                        self._loop.call_soon_threadsafe(self._on_press)
                return 1
            if msg == WM_DESTROY:
                user32.PostQuitMessage(0)
                return 0
            return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

        self._wnd_proc_cb = WNDPROC(wnd_proc)  # must outlive the thread

        class WNDCLASSEXW(ctypes.Structure):
            _fields_ = [
                ("cbSize",        wt.UINT),
                ("style",         wt.UINT),
                ("lpfnWndProc",   WNDPROC),
                ("cbClsExtra",    ctypes.c_int),
                ("cbWndExtra",    ctypes.c_int),
                ("hInstance",     wt.HINSTANCE),
                ("hIcon",         wt.HICON),
                ("hCursor",       wt.HANDLE),
                ("hbrBackground", wt.HBRUSH),
                ("lpszMenuName",  wt.LPCWSTR),
                ("lpszClassName", wt.LPCWSTR),
                ("hIconSm",       wt.HICON),
            ]

        class MSG(ctypes.Structure):
            _fields_ = [
                ("hwnd",    wt.HWND),
                ("message", wt.UINT),
                ("wParam",  WPARAM),
                ("lParam",  LPARAM),
                ("time",    wt.DWORD),
                ("pt",      wt.POINT),
            ]

        class_name = f"VoxAppCmd_{id(self)}"
        self._class_name = class_name

        def _message_loop() -> None:
            hinstance = kernel32.GetModuleHandleW(None)

            wc = WNDCLASSEXW()
            wc.cbSize        = ctypes.sizeof(WNDCLASSEXW)
            wc.lpfnWndProc   = self._wnd_proc_cb
            wc.hInstance     = hinstance
            wc.lpszClassName = class_name
            user32.RegisterClassExW(ctypes.byref(wc))

            hwnd = user32.CreateWindowExW(
                WS_EX_TOOLWINDOW, class_name, "vox-appcommand",
                WS_OVERLAPPEDWINDOW, 0, 0, 1, 1,
                None, None, hinstance, None,
            )
            self._hwnd[0] = hwnd
            user32.RegisterShellHookWindow(hwnd)
            self._shell_msg[0] = user32.RegisterWindowMessageW("SHELLHOOK")
            user32.ShowWindow(hwnd, SW_HIDE)

            msg = MSG()
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0):
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))

            user32.DeregisterShellHookWindow(hwnd)
            user32.DestroyWindow(hwnd)
            user32.UnregisterClassW(class_name, hinstance)

        self._thread = threading.Thread(target=_message_loop, daemon=True)
        self._thread.start()

    def close(self) -> None:
        if self._hwnd[0]:
            self._user32.PostMessageW(self._hwnd[0], self._WM_DESTROY, 0, 0)
        self._thread.join(timeout=2.0)


class BeatsTrigger:
    """SMTC-based trigger.  Primary for Beats hardware in live sessions.

    Plays a silent looping file through the system's Communications audio
    endpoint (on Bluetooth headphones this is the HFP/headset profile, a
    separate Windows device from the A2DP/music profile used by sounddevice
    for TTS output) so that:
      1. vox holds an active SMTC session and receives every button press,
         not just alternating ones (playing actual audio makes the session
         definitively "active" and avoids AVRCP routing drift).
      2. The silent audio plays through a different device than sounddevice's
         TTS stream, so the WinRT thread that delivers _on_button callbacks
         is never blocked by sounddevice's exclusive hold on the A2DP device.

    If the Communications device happens to be the same as the Default device
    (rare — means Beats is registered as a single Windows audio endpoint),
    `p.play()` may open the same device as sounddevice.  In that case the
    OSError is caught in _on_button and the call_soon_threadsafe has already
    fired, so callback delivery is not delayed.
    """

    def __init__(
        self,
        loop: asyncio.AbstractEventLoop,
        on_press: callable,
        silence_wav: Path,
    ) -> None:
        from winsdk.windows.foundation import Uri
        from winsdk.windows.media import MediaPlaybackStatus
        from winsdk.windows.media.core import MediaSource
        from winsdk.windows.media.playback import MediaPlayer

        self._loop = loop
        self._on_press = on_press
        self._MediaPlaybackStatus = MediaPlaybackStatus

        p = self._player = MediaPlayer()
        p.is_looping_enabled = True
        p.volume = 0.0
        p.command_manager.is_enabled = False
        # Use the default MEDIA audio category (A2DP on Bluetooth headphones).
        # COMMUNICATIONS (HFP) was tried but AVRCP button delivery was
        # unreliable over HFP — the headset only sends passthrough commands
        # reliably when an A2DP stream is active.
        uri = f"file:///{str(silence_wav).replace(chr(92), '/')}"
        p.source = MediaSource.create_from_uri(Uri(uri))

        s = self._smtc = p.system_media_transport_controls
        s.is_enabled = True
        s.is_play_enabled = True
        s.is_pause_enabled = True
        # Start advertising PAUSED. The Beats, seeing no real A2DP stream,
        # sends an AVRCP PLAY on the first press; PAUSED->Play is a *valid*
        # transition that Windows forwards as a ButtonPressed event. Advertising
        # PLAYING here makes that first PLAY a redundant no-op that Windows drops
        # (the observed 0/N capture). We then toggle on every press to stay in
        # sync with the headset's alternating state so all subsequent presses
        # remain valid transitions.
        self._status = MediaPlaybackStatus.PAUSED
        s.playback_status = self._status

        self._token = s.add_button_pressed(self._on_button)
        p.play()

    def _on_button(self, sender, args) -> None:
        # call_soon_threadsafe fires BEFORE the playback_status update so the
        # asyncio callback is never delayed by a slow or failing WinRT call.
        import time as _time
        try:
            btn = args.button  # SystemMediaTransportControlsButton: 0=Play 1=Pause 2=Stop ...
        except Exception as e:
            btn = f"<err {e}>"
        # Toggle advertised state to mirror the headset, keeping the next press
        # a valid transition.
        Status = self._MediaPlaybackStatus
        self._status = Status.PLAYING if self._status == Status.PAUSED else Status.PAUSED
        log.debug(
            "BeatsTrigger: button received (WinRT thread) @ %.3f  button=%s  -> now advertising %s",
            _time.perf_counter(), btn, self._status,
        )
        self._loop.call_soon_threadsafe(self._on_press)
        try:
            sender.playback_status = self._status
        except OSError:
            pass

    def close(self) -> None:
        self._smtc.remove_button_pressed(self._token)
        self._player.pause()
        self._player.close()
