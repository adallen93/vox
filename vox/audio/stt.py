import asyncio
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np


class WhisperSTT:
    """Wraps faster-whisper for async transcription. Call load() before transcribe()."""

    SAMPLE_RATE = 16000  # faster-whisper always expects 16 kHz float32

    def __init__(
        self,
        model_name: str,
        compute_type: str,
        cache_dir: Path,
    ) -> None:
        self._model_name = model_name
        self._compute_type = compute_type
        self._cache_dir = cache_dir
        self._model = None
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="whisper")

    def load(self) -> None:
        from faster_whisper import WhisperModel

        self._model = WhisperModel(
            self._model_name,
            device="cpu",
            compute_type=self._compute_type,
            download_root=str(self._cache_dir),
        )

    def _transcribe_sync(self, audio: np.ndarray) -> str:
        assert self._model is not None, "call load() first"
        segments, _ = self._model.transcribe(
            audio,
            language="en",
            beam_size=1,
            vad_filter=True,
        )
        return " ".join(s.text.strip() for s in segments).strip()

    async def transcribe(self, audio: np.ndarray) -> str:
        """Transcribe float32 16 kHz mono audio; returns the text."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self._executor, self._transcribe_sync, audio)

    def close(self) -> None:
        self._executor.shutdown(wait=False)
