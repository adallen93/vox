# Beats Studio³ play/pause interception on Windows 11

**Date:** 2026-06-10
**Goal:** Use the Beats Studio³ play/pause button as a dedicated push-to-talk
trigger for vox — detect the press *and* stop it from reaching other apps
(Chrome, Spotify).
**Status:** Solved. Winning approach validated on hardware (11/11 presses
captured, zero leaked to Chrome).

---

## Bottom line

The button is **not** a keyboard or HID input event. It is a Bluetooth
**AVRCP** transport command that Windows routes to the **current media
session**. That is why every input-layer interception fails. The solution is
to *become* the current media session: vox holds a silent, looping audio
stream and registers a **SystemMediaTransportControls (SMTC)** session via
WinRT. Windows then delivers play/pause to vox **exclusively**, and other apps
never see it.

## Results matrix

| # | Approach | Detects? | Suppresses? | Notes |
|---|----------|:---:|:---:|---|
| 0 | WH_KEYBOARD_LL (pynput) | No | — | vk 0xB3 never arrives |
| 1 | RegisterHotKey(0xB3) | No | — | Registers OK; `WM_HOTKEY` never fires |
| 2 | Raw Input, Consumer page 0x0C | No | No | `RIDEV_NOLEGACY` is illegal for the consumer page → `ERROR_INVALID_PARAMETER` (87). Even without it: 0 HID events |
| 3 | WM_APPCOMMAND (own window) | No | No | 0 events. Chrome reacts while *backgrounded* → rules out foreground-WM_APPCOMMAND. Global suppression would need an injected C DLL regardless |
| 4 | `keyboard` PyPI lib | No | — | Same WH_KEYBOARD_LL backend as pynput; blind to the button |
| 5 | SMTC session (empty) | **Yes** | No | Detects, but Chrome (real audio) outranks an empty session |
| 6 | SMTC + silent audio, auto CommandManager | **Yes** | Partial | Captured until the OS auto-paused our player, then reverted to Chrome |
| **7** | **SMTC + silent audio, CommandManager OFF, re-assert play** | **Yes** | **Yes** | **Winner.** 11/11 presses captured; Chrome never paused |

### Why the input-layer approaches all fail

In the approach-1/2 tests, **Chrome responded to the button even though it was
in the background** and the terminal was the foreground window. A
foreground-delivered `WM_APPCOMMAND` could not explain that. The press is
routed by Windows' media-session manager straight to the active media
session — bypassing the keyboard hook, hotkey table, raw-input stream, and
foreground window entirely.

## Winning approach (Approach 7)

Two non-obvious requirements, both learned the hard way:

1. **Hold genuine, active playback** (a silent looping WAV at volume 0). An
   *empty* SMTC session (Approach 5) does not outrank an app that is actually
   rendering audio. Real playback makes vox the current session.
2. **Disable the auto `CommandManager`** and re-assert `PLAYING` on every
   press. With the CommandManager enabled (Approach 6), the OS *paused our own
   player* when it delivered the pause command; vox then lost "currently
   playing" status and the button reverted to Chrome.

### Minimal working code (asyncio-ready)

```python
import asyncio
from winsdk.windows.foundation import Uri
from winsdk.windows.media import MediaPlaybackStatus, SystemMediaTransportControlsButton
from winsdk.windows.media.core import MediaSource
from winsdk.windows.media.playback import MediaPlayer


class BeatsTrigger:
    """Owns the media session so the Beats play/pause routes here, not to Chrome."""

    def __init__(self, loop: asyncio.AbstractEventLoop, on_press, silence_wav: str):
        self._loop = loop
        self._on_press = on_press                      # invoked on the asyncio loop
        p = self._player = MediaPlayer()
        p.is_looping_enabled = True
        p.volume = 0.0                                 # inaudible
        p.command_manager.is_enabled = False           # CRITICAL: OS must not pause us
        uri = f"file:///{silence_wav.replace(chr(92), '/')}"
        p.source = MediaSource.create_from_uri(Uri(uri))
        s = self._smtc = p.system_media_transport_controls
        s.is_enabled = s.is_play_enabled = s.is_pause_enabled = True
        s.playback_status = MediaPlaybackStatus.PLAYING
        self._token = s.add_button_pressed(self._on_button)  # keep the token!
        p.play()

    def _on_button(self, sender, args):
        # Fires on a WinRT thread-pool thread — NOT the asyncio/Qt thread.
        sender.playback_status = MediaPlaybackStatus.PLAYING   # never cede 'current'
        self._player.play()
        self._loop.call_soon_threadsafe(self._on_press)        # marshal to the loop

    def close(self):
        self._smtc.remove_button_pressed(self._token)
        self._player.pause()
        self._player.close()
```

### Generating the silent asset

```python
import wave, os
with wave.open(os.path.abspath("silence.wav"), "w") as w:
    w.setnchannels(1); w.setsampwidth(2); w.setframerate(44100)
    w.writeframes(b"\x00\x00" * 44100 * 30)   # 30s mono silence; long loop = rare boundaries
```

## Gotchas

1. **No admin rights, no message pump.** SMTC delivers events on a WinRT
   thread-pool thread — there is no `GetMessage` loop to run and nothing
   blocks the event loop. Bridge to asyncio via `loop.call_soon_threadsafe`.
   Clean fit for qasync. (Contrast: the `keyboard` lib's suppression needs
   admin.)
2. **Keep references alive.** The `MediaPlayer`, the SMTC object, the callback,
   and the `add_button_pressed` **token** must be held as attributes or the GC
   silently kills events.
3. **TOGGLE, not press-and-hold.** AVRCP sends one command per press — no
   separate down/up. This button supports **press-to-toggle** dictation, not
   hold-to-talk. Product decision required.
4. **Always-`PAUSE` is a feature.** Because we re-assert `PLAYING`, every press
   arrives as `PAUSE`. No state tracking needed — any button event is "the
   trigger."
5. **Untested coexistence risk.** In every test Chrome was playing *first*,
   then vox grabbed the session. The reverse — vox running, then the user
   starts new media — may let the new app steal the session until vox
   re-asserts. This is the key open question for an always-on PTT trigger and
   should be tested before relying on it all day.

## Dependencies

- **`winsdk`** (PyPI, `1.0.0b10` used here) — WinRT projection for
  `windows.media.*`. Required for the winning approach. *Not yet declared in
  `pyproject.toml`* — add it during integration.
- `keyboard` (PyPI) was installed only to test Approach 4 and has been
  removed; it is not needed.
```
