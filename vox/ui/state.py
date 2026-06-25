from enum import Enum


class SessionState(str, Enum):
    IDLE = "idle"
    RECORDING = "recording"
    TRANSCRIBING = "transcribing"
    RESPONDING = "responding"


# State name -> hex color; shared contract between Python and frontend CSS vars.
STATE_COLORS: dict[str, str] = {
    "idle": "#4a9eff",
    "recording": "#ff4a4a",
    "transcribing": "#ffa54a",
    "responding": "#4aff9e",
}
