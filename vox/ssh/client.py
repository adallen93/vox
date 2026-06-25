import asyncio
import logging

import asyncssh

from vox.config import Config

log = logging.getLogger(__name__)


class SSHClient:
    """Persistent asyncssh connection to aragorn."""

    def __init__(self, config: Config) -> None:
        self._config = config
        self._conn: asyncssh.SSHClientConnection | None = None

    async def connect(self) -> None:
        cfg = self._config
        log.debug("SSH connecting to %s@%s", cfg.user, cfg.host)
        self._conn = await asyncssh.connect(
            cfg.host,
            username=cfg.user,
            known_hosts=str(cfg.known_hosts),
            client_keys=[str(cfg.client_key_path)],
            keepalive_interval=cfg.ssh_keepalive_interval,
        )
        log.debug("SSH connected")

    async def close(self) -> None:
        if self._conn:
            self._conn.close()
            try:
                await asyncio.wait_for(self._conn.wait_closed(), timeout=5.0)
            except (asyncio.TimeoutError, Exception):
                pass
            self._conn = None
            log.debug("SSH connection closed")

    @property
    def conn(self) -> asyncssh.SSHClientConnection:
        assert self._conn is not None, "call connect() first"
        return self._conn
