import asyncio
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
from piper import PiperVoice


class PiperTTS:
    """Wraps PiperVoice for async use. Call load() before synthesize()."""

    def __init__(self, model_path: Path) -> None:
        self._model_path = model_path
        self._voice: PiperVoice | None = None
        self.sample_rate: int = 22050  # updated by load()
        # Dedicated single-thread executor: piper is not thread-safe across calls
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="piper")

    def load(self) -> None:
        self._voice = PiperVoice.load(str(self._model_path))
        self.sample_rate = self._voice.config.sample_rate

    def _synthesize_sync(self, text: str, length_scale: float = 1.0) -> np.ndarray:
        assert self._voice is not None, "call load() first"
        from piper import SynthesisConfig
        syn_cfg = SynthesisConfig(length_scale=length_scale) if length_scale != 1.0 else None
        chunks = [c.audio_float_array for c in self._voice.synthesize(text, syn_cfg)]
        return np.concatenate(chunks) if chunks else np.empty(0, dtype=np.float32)

    async def synthesize(self, text: str, length_scale: float = 1.0) -> np.ndarray:
        """Synthesize text in the piper executor; return float32 PCM array."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            self._executor, self._synthesize_sync, text, length_scale
        )

    def close(self) -> None:
        self._executor.shutdown(wait=False)
