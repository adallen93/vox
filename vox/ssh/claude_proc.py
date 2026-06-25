"""
Manages the single persistent claude process running on aragorn over SSH.

User turns are written as stream-json lines to stdin; the stdout reader
yields parsed events. The process is started once and kept alive for the
session; reconnect logic lives in P7.
"""
import asyncio
import json
import logging
from typing import AsyncIterator

import asyncssh

from vox.config import Config
from vox.ssh.protocol import Event, SessionInit, TurnResult, parse_event

log = logging.getLogger(__name__)

# Minimal probe sent at startup to break the stdin deadlock.
# claude with --print --input-format stream-json blocks on stdin before
# emitting any output, including session-init.  Sending a probe turn
# immediately causes claude to output session-init + response, letting us
# capture the session id.  The probe response is drained and discarded.
_PROBE_TURN = json.dumps({
    "type": "user",
    "message": {
        "role": "user",
        "content": [{"type": "text", "text": "hi"}],
    },
}) + "\n"


class ClaudeProcess:
    def __init__(self, conn: asyncssh.SSHClientConnection, config: Config) -> None:
        self._conn = conn
        self._config = config
        self._process: asyncssh.SSHClientProcess | None = None
        self.session_id: str | None = None

    async def start(self) -> None:
        """Start claude on aragorn and wait for session-init.

        Sends a one-token probe turn to trigger output (claude emits nothing
        until stdin receives data).  The probe response is drained and
        discarded before returning.
        """
        cmd = self._build_command()
        log.debug("Launching claude: %s", cmd[:160])
        self._process = await self._conn.create_process(cmd, encoding="utf-8")

        # Send probe immediately — breaks the stdin deadlock.
        self._process.stdin.write(_PROBE_TURN)
        await self._process.stdin.drain()

        # Read until session-init, then drain the rest of the probe turn.
        async with asyncio.timeout(60.0):
            async for line in self._process.stdout:
                line = line.rstrip()
                if not line:
                    continue
                log.debug("startup: %r", line[:200])
                event = parse_event(line)
                if isinstance(event, SessionInit):
                    self.session_id = event.session_id
                    log.debug("Claude session ready: %s", self.session_id)
                if isinstance(event, TurnResult):
                    return  # probe response consumed; session is ready

        raise RuntimeError("claude did not send session init within 60 s")

    async def send_turn(self, text: str) -> None:
        assert self._process is not None
        msg = json.dumps({
            "type": "user",
            "message": {
                "role": "user",
                "content": [{"type": "text", "text": text}],
            },
        })
        self._process.stdin.write(msg + "\n")
        await self._process.stdin.drain()

    async def events(self) -> AsyncIterator[Event]:
        """Yield parsed events from stdout until the next TurnResult."""
        assert self._process is not None
        async for line in self._process.stdout:
            line = line.rstrip()
            if not line:
                continue
            event = parse_event(line)
            yield event
            if isinstance(event, TurnResult):
                return

    async def close(self) -> None:
        if self._process:
            self._process.close()
            try:
                await asyncio.wait_for(self._process.wait_closed(), timeout=3.0)
            except (asyncio.TimeoutError, Exception):
                pass
            self._process = None

    def _build_command(self) -> str:
        cfg = self._config
        add_dirs = " ".join(f'--add-dir "{d}"' for d in cfg.add_dirs)
        return (
            "cd /home/aallen/vox-harness && "
            f"{cfg.claude_bin}"
            " --print"
            " --input-format stream-json"
            " --output-format stream-json"
            " --include-partial-messages"
            " --verbose"
            f' --allowedTools "{cfg.allowed_tools}"'
            f' --disallowedTools "{cfg.disallowed_tools}"'
            f" --permission-mode {cfg.permission_mode}"
            f" {add_dirs}"
        )
