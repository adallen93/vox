"""
Windows neural TTS via WinRT SpeechSynthesizer.

Matches the PiperTTS interface (load / synthesize / close / sample_rate)
so it drops straight into _ssh_test with no other changes.

Synthesis runs inside the Windows speech service — zero model RAM cost
to the Python process. The WAV stream is returned as a float32 numpy
array at the voice's native rate (24 kHz for neural voices on Win 11).
"""
import asyncio
import io
import logging
import wave

import numpy as np

log = logging.getLogger(__name__)

_NEURAL_SAMPLE_RATE = 24000   # Microsoft neural voices (Aria, Guy, Jenny, …)
_LEGACY_SAMPLE_RATE = 22050   # older SAPI voices (David, Zira)


class WindowsTTS:
    def __init__(self, voice_name: str | None = None, voice_rate: float = 1.0) -> None:
        self._voice_name = voice_name
        self._voice_rate = voice_rate
        self._synth = None
        self._sample_rate = _NEURAL_SAMPLE_RATE

    @property
    def sample_rate(self) -> int:
        return self._sample_rate

    def load(self) -> None:
        from winsdk.windows.media.speechsynthesis import SpeechSynthesizer

        self._synth = SpeechSynthesizer()
        voices = list(SpeechSynthesizer.all_voices)
        names = [v.display_name for v in voices]
        log.info("Windows TTS voices: %s", names)

        if self._voice_name:
            needle = self._voice_name.lower()
            match = next((v for v in voices if needle in v.display_name.lower()), None)
            if match:
                self._synth.voice = match
                log.info("Selected voice: %s", match.display_name)
            else:
                log.warning("Voice %r not found — using default. Available: %s",
                            self._voice_name, names)

        if self._voice_rate != 1.0:
            self._synth.options.speaking_rate = float(self._voice_rate)
            log.info("WindowsTTS speaking_rate set to %.2f", self._voice_rate)

        default = self._synth.voice
        log.info("WindowsTTS ready — voice: %s  lang: %s",
                 default.display_name, default.language)

        # Legacy (non-neural) voices output at 22050 Hz; detect from the name.
        name_lower = default.display_name.lower()
        if any(old in name_lower for old in ("david", "zira", "mark", "hazel")):
            self._sample_rate = _LEGACY_SAMPLE_RATE
        else:
            self._sample_rate = _NEURAL_SAMPLE_RATE

    async def synthesize(self, text: str, length_scale: float = 1.0) -> np.ndarray:
        if not text.strip():
            return np.zeros(0, dtype=np.float32)

        from winsdk.windows.storage.streams import DataReader

        stream = await self._synth.synthesize_text_to_stream_async(text)
        size = stream.size
        if size == 0:
            return np.zeros(0, dtype=np.float32)

        reader = DataReader(stream.get_input_stream_at(0))
        count = await reader.load_async(size)
        buf = bytearray(count)
        reader.read_bytes(buf)

        return self._parse_wav(bytes(buf))

    def _parse_wav(self, data: bytes) -> np.ndarray:
        with io.BytesIO(data) as f:
            with wave.open(f) as wf:
                actual_rate = wf.getframerate()
                if actual_rate != self._sample_rate:
                    log.info("Updating sample rate %d → %d from WAV header",
                             self._sample_rate, actual_rate)
                    self._sample_rate = actual_rate
                frames = wf.readframes(wf.getnframes())
        return np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0

    def close(self) -> None:
        self._synth = None
