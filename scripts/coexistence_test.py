"""
Coexistence test: vox grabs the SMTC session FIRST, then user starts new media.
Does the button stay with vox, or does new media steal the session?

Run with: uv run python scripts/coexistence_test.py
"""
import asyncio
import sys
import wave
from pathlib import Path

# Must be set before asyncio use on Windows
asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from winsdk.windows.foundation import Uri
from winsdk.windows.media import MediaPlaybackStatus
from winsdk.windows.media.core import MediaSource
from winsdk.windows.media.playback import MediaPlayer

ASSETS_DIR = Path(__file__).parent.parent / "vox" / "assets"
SILENCE_WAV = ASSETS_DIR / "silence.wav"


def generate_silence(path: Path, seconds: int = 30) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "w") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(44100)
        w.writeframes(b"\x00\x00" * 44100 * seconds)
    print(f"  Generated {path} ({seconds}s mono silence)")


def make_player(silence_wav: Path) -> tuple:
    uri_str = f"file:///{str(silence_wav).replace(chr(92), '/')}"
    player = MediaPlayer()
    player.is_looping_enabled = True
    player.volume = 0.0
    player.command_manager.is_enabled = False
    player.source = MediaSource.create_from_uri(Uri(uri_str))
    smtc = player.system_media_transport_controls
    smtc.is_enabled = True
    smtc.is_play_enabled = True
    smtc.is_pause_enabled = True
    smtc.playback_status = MediaPlaybackStatus.PLAYING
    return player, smtc


async def run_test(loop: asyncio.AbstractEventLoop) -> None:
    press_count = 0
    press_event = asyncio.Event()

    if not SILENCE_WAV.exists():
        print("Generating silence asset...")
        generate_silence(SILENCE_WAV)

    print()
    print("=" * 60)
    print("  COEXISTENCE TEST")
    print("=" * 60)
    print()
    print("  vox is grabbing the SMTC session now...")

    player, smtc = make_player(SILENCE_WAV)

    def on_button(sender, args):
        nonlocal press_count
        sender.playback_status = MediaPlaybackStatus.PLAYING
        player.play()
        press_count += 1
        loop.call_soon_threadsafe(press_event.set)

    token = smtc.add_button_pressed(on_button)
    player.play()

    print("  Session grabbed. Playback asserted.")
    print()
    print("  STEP 1 — Press the Beats button NOW (vox should capture it).")
    print("           Press Enter when done to confirm what happened.")

    await asyncio.get_event_loop().run_in_executor(None, input)
    n_after_step1 = press_count

    print()
    if n_after_step1 > 0:
        print(f"  Step 1: {n_after_step1} press(es) captured by vox. Good.")
    else:
        print("  Step 1: 0 presses captured — session not held. Stopping.")
        smtc.remove_button_pressed(token)
        player.pause()
        player.close()
        return

    print()
    print("  STEP 2 — Open a YouTube video or Spotify and START playing it.")
    print("           Then press Enter here.")

    await asyncio.get_event_loop().run_in_executor(None, input)

    print()
    print("  STEP 3 — Press the Beats button NOW (does vox still capture it?).")
    print("           Then press Enter and report: did Chrome/Spotify pause?")

    press_event.clear()
    n_before = press_count

    await asyncio.get_event_loop().run_in_executor(None, input)
    n_after_step3 = press_count - n_before

    print()
    print("=" * 60)
    print("  RESULTS")
    print("=" * 60)
    if n_after_step3 > 0:
        print(f"  vox captured {n_after_step3} press(es) after new media started.")
        print("  If Chrome/Spotify did NOT pause -> coexistence WORKS.")
        print("  If Chrome/Spotify DID pause -> session was shared (partial steal).")
    else:
        print("  vox captured 0 presses after new media started.")
        print("  -> Session was stolen. Need periodic re-assert.")
    print()
    print("  Total presses captured by vox:", press_count)
    print()

    smtc.remove_button_pressed(token)
    player.pause()
    player.close()


if __name__ == "__main__":
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(run_test(loop))
    finally:
        loop.close()
