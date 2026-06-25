import queue
import threading

import numpy as np
import sounddevice as sd


class AudioPlayer:
    """Queue-based audio player backed by a dedicated playback thread.

    Enqueue float32 PCM arrays; the player thread feeds them to sounddevice
    in order. first_audio_started fires when the first chunk begins writing
    to the device — use it to timestamp the start of audible output.

    For barge-in (P7): call stop() to abort immediately; queued audio is
    discarded when the thread exits.
    """

    def __init__(self, sample_rate: int, device_name_hint: str = "Beats") -> None:
        self._sample_rate = sample_rate
        self._device_name_hint = device_name_hint
        self._queue: queue.Queue = queue.Queue()
        self.first_audio_started: threading.Event = threading.Event()
        self._thread: threading.Thread | None = None
        self._running = False

    def start(self) -> None:
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        self._queue.put(None)  # sentinel — unblocks the thread if waiting
        if self._thread:
            self._thread.join(timeout=5.0)

    def enqueue(self, audio: np.ndarray) -> None:
        self._queue.put(audio)

    def clear(self) -> None:
        """Discard all queued chunks; the current chunk plays to its end."""
        while True:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                break

    @staticmethod
    def _resample(audio: np.ndarray, orig_rate: int, target_rate: int) -> np.ndarray:
        if orig_rate == target_rate:
            return audio
        target_len = int(round(len(audio) * target_rate / orig_rate))
        indices = np.linspace(0, len(audio) - 1, target_len)
        return np.interp(indices, np.arange(len(audio)), audio).astype(np.float32)

    def _run(self) -> None:
        import logging
        from vox.audio.devices import find_device

        log = logging.getLogger(__name__)
        device = find_device(self._device_name_hint, "output")
        if device is None:
            log.warning("AudioPlayer: no output device matching %r; using system default", self._device_name_hint)
        else:
            log.debug("AudioPlayer: using device %d for output", device)

        device_rate = int(sd.query_devices(device)["default_samplerate"]) if device is not None else self._sample_rate
        if device_rate != self._sample_rate:
            log.debug("AudioPlayer: resampling %d Hz -> %d Hz", self._sample_rate, device_rate)

        first = True
        try:
            with sd.OutputStream(
                samplerate=device_rate,
                channels=1,
                dtype="float32",
                device=device,
            ) as stream:
                while True:
                    try:
                        audio = self._queue.get(timeout=0.1)
                    except queue.Empty:
                        if not self._running:
                            break
                        continue
                    if audio is None:
                        break
                    if device_rate != self._sample_rate:
                        audio = self._resample(audio, self._sample_rate, device_rate)
                    if first:
                        self.first_audio_started.set()
                        first = False
                    try:
                        stream.write(audio)
                    except Exception as exc:
                        log.warning("AudioPlayer: stream.write failed (%s) — audio device lost?", exc)
                        break
        except Exception as exc:
            log.error("AudioPlayer: output stream failed to open or died: %s", exc)
        finally:
            self._running = False
