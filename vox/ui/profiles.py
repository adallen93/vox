"""
Parse Windows Terminal settings.json to extract shell profiles.

Returns a list of {"name": str, "commandline": str} dicts for non-hidden profiles.
Falls back to a single PowerShell entry if the settings file is missing or malformed.
"""
import json
import logging
from pathlib import Path

log = logging.getLogger(__name__)

_FALLBACK = [{"name": "Windows PowerShell", "commandline": "powershell.exe"}]


def load_profiles(settings_path: Path) -> list[dict]:
    if not settings_path.exists():
        log.info("Windows Terminal settings not found at %s — using fallback", settings_path)
        return _FALLBACK

    try:
        data = json.loads(settings_path.read_text(encoding="utf-8"))
    except Exception as exc:
        log.warning("Failed to parse Windows Terminal settings: %s", exc)
        return _FALLBACK

    profiles_section = data.get("profiles", {})
    if isinstance(profiles_section, dict):
        profile_list = profiles_section.get("list", [])
    elif isinstance(profiles_section, list):
        profile_list = profiles_section
    else:
        return _FALLBACK

    results = []
    for p in profile_list:
        if p.get("hidden", False):
            continue
        name = p.get("name", "").strip()
        cmdline = p.get("commandline", "").strip()
        if name and cmdline:
            results.append({"name": name, "commandline": cmdline})

    if not results:
        log.info("No usable profiles found in Windows Terminal settings — using fallback")
        return _FALLBACK

    return results
