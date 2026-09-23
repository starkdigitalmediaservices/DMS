from fastapi import APIRouter, Depends

from app.config import settings
from app.deps import require_tenant_access
from app.schemas.auth import TokenPayload

router = APIRouter(prefix="/connectors", tags=["Connectors"])


@router.get("/info")
async def get_connector_info(current_user: TokenPayload = Depends(require_tenant_access)):
    """Connection details for the non-HTTP ingestion channels, so a user can hand
    them to another machine without needing to ask an engineer for credentials.

    The SFTP password is a shared service credential, not a per-user one:
    it grants write access to the drop folder every tenant's connector
    ingests from. This used to be returned in plaintext to any
    authenticated caller of any role, so a read-only user in one tenant
    could read it straight out of the API and drop files that land as
    ingested documents. It is now only included for an it_admin — everyone
    else gets the connection details they need and is told who to ask.
    """
    is_admin = current_user.role == "it_admin"
    return {
        "sftp": {
            "enabled": settings.sftp_enabled,
            "host": settings.sftp_external_host,
            "port": settings.sftp_external_port,
            "username": settings.sftp_username,
            "password": settings.sftp_password if is_admin else None,
            "password_hint": None if is_admin else "Ask an IT admin for the SFTP password.",
            "remote_dir": settings.sftp_remote_dir,
            "note": "Both machines must be on the same network. Drop a file into "
                    "this folder from any SFTP client and it appears in your DMS "
                    "Drive automatically within 20-30 seconds.",
        },
        "email_webhook": {
            "enabled": settings.email_webhook_enabled,
            "endpoint": "/api/v1/connectors/email-inbound",
            "note": "Cloudflare Email Routing + Cloudflare Worker inbound email webhook. "
                    "Receives emails delivered to Cloudflare Email Routing and ingests attachments automatically.",
        },
        "email_imap_legacy": {
            "enabled": settings.email_enabled,
            "address": settings.email_address,
            "smtp_host": settings.email_external_smtp_host,
            "smtp_port": settings.email_external_smtp_port,
            "note": "Legacy IMAP polling connector (for local dev/testing with GreenMail).",
        },
        "watched_folder": {
            "note": "Only available on this server's own local disk, not from "
                    "another machine — use the SFTP connector above to share "
                    "files from a different computer.",
        },
    }
