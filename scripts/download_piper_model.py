"""
Download a Piper TTS voice model to ~/.local/share/piper/.

Usage:
    uv run python scripts/download_piper_model.py lessac-medium
    uv run python scripts/download_piper_model.py lessac-low
    uv run python scripts/download_piper_model.py lessac-high   # re-download current

Available en_US voices with quality tiers:
    lessac-high / lessac-medium / lessac-low
    ryan-high   / ryan-medium   / ryan-low
    amy-medium
    joe-medium
    kathleen-low

Each download fetches the .onnx model and its .onnx.json config (~7–65 MB).
"""
import sys
import urllib.request
from pathlib import Path

BASE_URL = (
    "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0"
    "/en/en_US/{voice}/{quality}/en_US-{voice}-{quality}.onnx{suffix}"
)
DEST_DIR = Path.home() / ".local" / "share" / "piper"


def _parse(name: str) -> tuple[str, str]:
    """Split 'lessac-medium' into ('lessac', 'medium')."""
    parts = name.rsplit("-", 1)
    if len(parts) != 2:
        sys.exit(f"Bad model name {name!r} — expected <voice>-<quality>, e.g. lessac-medium")
    return parts[0], parts[1]


def download(name: str) -> None:
    voice, quality = _parse(name)
    DEST_DIR.mkdir(parents=True, exist_ok=True)

    for suffix in ("", ".json"):
        url = BASE_URL.format(voice=voice, quality=quality, suffix=suffix)
        dest = DEST_DIR / f"en_US-{name}.onnx{suffix}"
        if dest.exists():
            print(f"  already exists: {dest}")
            continue
        print(f"  downloading {url}")
        print(f"           -> {dest}")
        urllib.request.urlretrieve(url, dest)
        print(f"  done ({dest.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(0)
    download(sys.argv[1])
    print("Model ready.")
