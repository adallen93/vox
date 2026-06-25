import sounddevice as sd


def list_audio_devices() -> None:
    devices = sd.query_devices()
    default_in, default_out = sd.default.device
    print(f"\n{'#':>3}  {'Name':<52}  {'In':>3}  {'Out':>3}  {'Default'}")
    print("-" * 80)
    for i, d in enumerate(devices):
        flags = []
        if i == default_in:
            flags.append("IN*")
        if i == default_out:
            flags.append("OUT*")
        print(
            f"{i:>3}  {d['name']:<52}  "
            f"{d['max_input_channels']:>3}  "
            f"{d['max_output_channels']:>3}  "
            f"{' '.join(flags)}"
        )
    print()


# For output: MME first — accepts the source sample rate (22050 Hz) natively;
# Windows does the SRC to the hardware rate. WASAPI forces 48000 Hz only and
# the double-SRC chain (our resample → WASAPI's internal SRC to A2DP rate)
# produces a chipmunk artifact on Beats.
# For input: WASAPI first — lower latency matters for recording.
_OUTPUT_API_PREFERENCE = ["MME", "Windows WASAPI", "Windows DirectSound"]
_INPUT_API_PREFERENCE = ["Windows WASAPI", "MME", "Windows DirectSound"]


def find_device(name_hint: str, kind: str) -> int | None:
    """Return the best device index whose name contains name_hint.

    Output prefers MME (accepts native 22050 Hz, no double-SRC).
    Input prefers WASAPI (lower latency for recording).
    Skips WDM-KS (blocking streams unsupported).
    Returns None if nothing matches; caller should fall back to system default.
    """
    devices = sd.query_devices()
    hostapis = sd.query_hostapis()
    ch_key = "max_input_channels" if kind == "input" else "max_output_channels"
    api_pref = _INPUT_API_PREFERENCE if kind == "input" else _OUTPUT_API_PREFERENCE

    candidates: list[tuple[int, int]] = []  # (pref_rank, device_idx)
    for i, d in enumerate(devices):
        if name_hint.lower() not in d["name"].lower():
            continue
        if d[ch_key] == 0:
            continue
        api_name = hostapis[d["hostapi"]]["name"]
        if api_name not in api_pref:
            continue  # skip WDM-KS and others
        candidates.append((api_pref.index(api_name), i))

    if not candidates:
        return None
    candidates.sort()
    return candidates[0][1]
