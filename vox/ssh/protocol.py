"""
Stream-JSON event parser for the persistent claude process stdout.

Event taxonomy verified against Claude Code CLI 2.1.172 with
--output-format stream-json --include-partial-messages --verbose --bare.
"""
import json
from dataclasses import dataclass, field
from typing import Any


@dataclass
class SessionInit:
    session_id: str


@dataclass
class TextDelta:
    text: str


@dataclass
class ThinkingDelta:
    pass  # model chain-of-thought — never reaches TTS


@dataclass
class ToolUse:
    tool_name: str
    tool_input: dict = field(default_factory=dict)


@dataclass
class ToolResult:
    content: Any  # display in UI/transcript; never speak


@dataclass
class AssistantMessage:
    """Assembled assistant turn. Text-only blocks extracted; thinking/tool_use filtered."""
    text: str


@dataclass
class TurnResult:
    """Authoritative turn-complete signal."""
    subtype: str
    cost_usd: float | None = None


@dataclass
class Ignored:
    pass


Event = (
    SessionInit | TextDelta | ThinkingDelta | ToolUse | ToolResult
    | AssistantMessage | TurnResult | Ignored
)


def parse_event(line: str) -> Event:
    try:
        obj = json.loads(line)
    except (json.JSONDecodeError, ValueError):
        return Ignored()

    t = obj.get("type")

    if t == "system":
        if obj.get("subtype") == "init":
            return SessionInit(session_id=obj.get("session_id", ""))
        return Ignored()

    if t == "stream_event":
        ev = obj.get("event", {})
        if ev.get("type") == "content_block_delta":
            delta = ev.get("delta", {})
            dt = delta.get("type")
            if dt == "text_delta":
                return TextDelta(text=delta.get("text", ""))
            if dt == "thinking_delta":
                return ThinkingDelta()
        return Ignored()

    if t == "assistant":
        content = obj.get("message", {}).get("content", [])
        text = "".join(
            b.get("text", "") for b in content if b.get("type") == "text"
        )
        # Surface tool_use blocks so the REPL can label them
        for b in content:
            if b.get("type") == "tool_use":
                return ToolUse(
                    tool_name=b.get("name", "?"),
                    tool_input=b.get("input", {}),
                )
        return AssistantMessage(text=text)

    if t == "user":
        content = obj.get("message", {}).get("content", [])
        for b in content:
            if b.get("type") == "tool_result":
                return ToolResult(content=b.get("content"))
        return Ignored()

    if t == "result":
        return TurnResult(
            subtype=obj.get("subtype", ""),
            cost_usd=obj.get("cost_usd"),
        )

    return Ignored()
