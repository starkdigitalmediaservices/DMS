"""T40 — the typed ingestion-connector contract.

Before this, "the abstraction" was a shared function call
(`connector_ingest_service.ingest_bytes()`) that every connector happened
to call, plus three separately-named poll/loop functions
(`poll_watched_folder_once`/`watch_folder_loop`, `poll_sftp_once`/
`sftp_poll_loop`, `poll_email_once`/`email_poll_loop`) wired into
`main.py`'s lifespan by hand. Nothing enforced conformance, and adding a
fourth connector meant editing `main.py` and hoping the new names matched
the pattern. `Connector` below is the actual typed contract; `Connector`
implementations register themselves in `get_enabled_connectors()`, and
`main.py` starts whatever that returns without ever being edited again
for a new source.

Existing connectors keep their original module-level functions unchanged
(`poll_sftp_once` etc. are called directly by tests and scripts) — the
`Connector` classes are thin wrappers so this is additive, not a rewrite.
"""
from typing import List, Protocol, runtime_checkable

from ..config import settings


@runtime_checkable
class Connector(Protocol):
    """Anything ingestion pulls documents from: a name for logging/health,
    one polling pass that returns how many documents it ingested, and a
    continuous loop `main.py` can run as a background task."""

    name: str

    async def poll_once(self) -> int: ...

    async def run_loop(self) -> None: ...


def get_enabled_connectors() -> List[Connector]:
    """The single place that decides which connectors are active. A new
    connector is: implement Connector, append an instance here — no other
    file changes."""
    from .watched_folder_connector import WatchedFolderConnector
    from .sftp_connector import SFTPConnector

    connectors: List[Connector] = [WatchedFolderConnector(), SFTPConnector()]
    if settings.email_enabled:
        from .email_connector import EmailConnector
        connectors.append(EmailConnector())
    return connectors
