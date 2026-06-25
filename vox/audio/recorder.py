import numpy as np
import sounddevice as sd


def _resample(audio: np.ndarray, src_rate: int, dst_rate: int) -> np.ndarray:
    if src_rate == dst_rate:
        return audio
    target_len = int(round(len(audio) * dst_rate / src_rate))
    indices = np.linspace(0, len(audio) - 1, target_len)
    return np.interp(indices, np.arange(len(audio)), audio).astype(np.float32)


class MicRecorder:
    """Records mono float32 audio from a named input device.

    Call start() to begin capturing, stop() to end and return the audio.
    The returned array is always at target_rate (default 16000 Hz — what
    Whisper expects).  If the device's native rate differs, audio is
    resampled before returning.
    """

    def __init__(self, device_name_hint: str, target_rate: int = 16000) -> None:
        self._hint = device_name_hint
        self._target_rate = target_rate
        self._chunks: list[np.ndarray] = []
        self._stream: sd.InputStream | None = None
        self._native_rate: int = target_rate

    def start(self) -> None:
        from vox.audio.devices import find_device

        device = find_device(self._hint, "input")
        self._native_rate = (
            int(sd.query_devices(device)["default_samplerate"])
            if device is not None
            else self._target_rate
        )
        self._chunks = []
        self._stream = sd.InputStream(
            samplerate=self._native_rate,
            channels=1,
            dtype="float32",
            device=device,
            callback=self._callback,
        )
        self._stream.start()

    def _callback(
        self,
        indata: np.ndarray,
        frames: int,
        time_info: object,
        status: sd.CallbackFlags,
    ) -> None:
        self._chunks.append(indata[:, 0].copy())

    def stop(self) -> np.ndarray:
        """Stop recording and return the captured audio at target_rate."""
        if self._stream:
            self._stream.stop()
            self._stream.close()
            self._stream = None
        audio = (
            np.concatenate(self._chunks)
            if self._chunks
            else np.empty(0, dtype=np.float32)
        )
        if self._native_rate != self._target_rate and len(audio) > 0:
            audio = _resample(audio, self._native_rate, self._target_rate)
        return audio
