"""Quick local test script for testing the Email-to-Upload Webhook.

Creates a test RFC822 raw email with an attached text document, base64 encodes it,
and POSTs it to the FastAPI webhook endpoint at http://localhost:8000/api/v1/connectors/email-inbound.
"""
import base64
import email.mime.application
import email.mime.multipart
import email.mime.text
import json
import sys
import urllib.request

# 1. Create a sample email with a file attachment
msg = email.mime.multipart.MIMEMultipart()
msg["From"] = "test-sender@example.com"
msg["To"] = "uploads@yourdomain.com"
msg["Subject"] = "Test Ingestion Document"

body = email.mime.text.MIMEText("Please ingest the attached sample document.", "plain")
msg.attach(body)

# Attachment details
filename = "sample_invoice.pdf"
file_content = b"%PDF-1.4 Sample PDF Content for DMS Ingestion Test"

attachment = email.mime.application.MIMEApplication(file_content, Name=filename)
attachment.add_header("Content-Disposition", "attachment", filename=filename)
msg.attach(attachment)

raw_email_bytes = msg.as_bytes()
raw_email_b64 = base64.b64encode(raw_email_bytes).decode("utf-8")

# 2. Target backend webhook URL and Secret
url = "http://localhost:8000/api/v1/connectors/email-inbound"
secret = "64d229d63ce4b83a0ec981703a1be25fc256fc6a1174bc31ce8591a98ee28750"

payload = json.dumps({"raw_email_b64": raw_email_b64}).encode("utf-8")

headers = {
    "Content-Type": "application/json",
    "X-Webhook-Secret": secret,
}

print(f"Sending test email with attachment '{filename}' to {url}...")

req = urllib.request.Request(url, data=payload, headers=headers, method="POST")

try:
    with urllib.request.urlopen(req) as response:
        resp_body = response.read().decode("utf-8")
        print("\nSUCCESS! Backend response:")
        print(json.dumps(json.loads(resp_body), indent=2))
except urllib.error.HTTPError as e:
    print(f"\nHTTP Error {e.code}: {e.reason}")
    print(e.read().decode("utf-8"))
except urllib.error.URLError as e:
    print(f"\nCould not connect to server at {url}: {e.reason}")
    print("Make sure your FastAPI server or Docker backend container is running (e.g. docker compose up)!")
