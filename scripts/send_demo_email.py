#!/usr/bin/env python3
"""
Send a demo email with a file attachment to the DMS email-in connector.

Usage:
    python3 scripts/send_demo_email.py                          # sends the default demo PDF
    python3 scripts/send_demo_email.py path/to/your/file.pdf     # sends any file you choose

Takes ~10-15 seconds after sending for the attachment to appear in DMS
(refresh the Drive page as connector@dms.local's account after that).
"""
import mimetypes
import smtplib
import sys
from email.message import EmailMessage
from pathlib import Path

SMTP_HOST = "localhost"
SMTP_PORT = 3025
TO_ADDRESS = "connector@dms.local"

DEFAULT_FILE = Path(__file__).parent.parent / "demo_assets" / "Citizen_Complaint_Main_St.pdf"


def main():
    file_path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_FILE

    if not file_path.exists():
        print(f"File not found: {file_path}")
        sys.exit(1)

    content_type, _ = mimetypes.guess_type(file_path.name)
    maintype, subtype = (content_type.split("/", 1) if content_type else ("application", "octet-stream"))

    msg = EmailMessage()
    msg["From"] = "client@example.com"
    msg["To"] = TO_ADDRESS
    msg["Subject"] = f"Sharing: {file_path.name}"
    msg.set_content(f"Please find {file_path.name} attached.")
    msg.add_attachment(file_path.read_bytes(), maintype=maintype, subtype=subtype, filename=file_path.name)

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as s:
        s.send_message(msg)

    print(f"Sent '{file_path.name}' to {TO_ADDRESS}")
    print("Give it ~10-15 seconds, then refresh the Drive page to see it appear.")


if __name__ == "__main__":
    main()
