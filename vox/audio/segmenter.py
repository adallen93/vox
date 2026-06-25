import re


def strip_markdown_for_tts(text: str) -> str:
    """Remove markdown syntax so TTS doesn't read punctuation aloud.

    Handles the common LLM patterns: bold, italic, inline code, headings,
    horizontal rules, and links.  Any asterisks or underscores that aren't
    part of a recognised pair are stripped by the final catch-all pass.
    """
    # Bold **text** / __text__
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text, flags=re.DOTALL)
    text = re.sub(r"__(.+?)__", r"\1", text)
    # Italic *text* / _text_
    text = re.sub(r"\*(.+?)\*", r"\1", text)
    text = re.sub(r"_(.+?)_", r"\1", text)
    # Inline code and fenced blocks
    text = re.sub(r"```[\s\S]*?```", "", text)
    text = re.sub(r"`+(.+?)`+", r"\1", text)
    # ATX headings
    text = re.sub(r"^#{1,6}\s+", "", text, flags=re.MULTILINE)
    # Horizontal rules
    text = re.sub(r"^\s*[-*_]{3,}\s*$", "", text, flags=re.MULTILINE)
    # Links [label](url) and [label][ref]
    text = re.sub(r"\[(.+?)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"\[(.+?)\]\[[^\]]*\]", r"\1", text)
    # Catch-all: remaining unmatched * _ characters
    text = re.sub(r"[*_]", "", text)
    return text.strip()


def segment_sentences(text: str) -> list[str]:
    """Split a completed text block into sentences using regex.

    Handles common abbreviations (Mr., Dr., etc.) by not splitting on them.
    Suitable for feeding a finished LLM turn to TTS in sentence-sized chunks.
    For streaming use (token-by-token from LLM), use StreamSegmenter.
    """
    protected = re.sub(
        r"\b(Mr|Mrs|Ms|Dr|Prof|Sr|Jr|vs|etc|e\.g|i\.e)\.",
        r"\1<DOT>",
        text,
    )
    parts = re.split(r"(?<=[.!?])\s+", protected.strip())
    return [p.replace("<DOT>", ".").strip() for p in parts if p.strip()]


class StreamSegmenter:
    """Accumulates streaming LLM tokens and emits complete sentences.

    Call feed() on each new text delta; it returns any newly-completed
    sentences. Call flush() at turn-end to emit any trailing fragment.
    """

    def __init__(self) -> None:
        self._buf: str = ""

    def feed(self, token: str) -> list[str]:
        self._buf += token
        sentences: list[str] = []
        while True:
            m = re.search(r"(?<=[.!?])\s+", self._buf)
            if not m:
                break
            sentence = self._buf[: m.start() + 1].strip()
            if sentence:
                sentences.append(sentence)
            self._buf = self._buf[m.end():]
        return sentences

    def flush(self) -> list[str]:
        remainder = self._buf.strip()
        self._buf = ""
        return [remainder] if remainder else []
