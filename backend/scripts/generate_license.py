#!/usr/bin/env python3
"""T81 — offline generator for a signed on-prem/air-gapped capacity license.

Run OUTSIDE the deployment it's licensing (this is the vendor-side tool,
not something a customer runs) and hand the customer only the resulting
.lic file. Never ship this script's private key inside the product image.

DEV-ONLY private key below, matching the public key hardcoded in
app/services/license_service.py — see docs/decisions/T81_licensing_assumptions.md. Before
any real deployment, generate a fresh keypair (this script's __main__
block shows how) and keep the private half in a secrets vault, never in
version control.

Usage:
    python3 scripts/generate_license.py --customer "Waqf Board XYZ" \\
        --days 365 --max-nodes 3 --max-gpu 1 --plan on_prem_standard \\
        --out xyz.lic
"""
import argparse
import base64
import json
import uuid
from datetime import datetime, timedelta

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

# DEV-ONLY — see module docstring.
VENDOR_PRIVATE_KEY_B64 = "64Mj3nu7+jgIFaogLl8eCq9YkwFq8XIC/86sJDX0UU8="


def _canonical_payload_bytes(payload: dict) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def generate_license(customer_name: str, days_valid: int, max_nodes: int, max_gpu_count: int, plan_key: str) -> dict:
    payload = {
        "license_id": str(uuid.uuid4()),
        "customer_name": customer_name,
        "issued_at": datetime.utcnow().isoformat() + "Z",
        "expires_at": (datetime.utcnow() + timedelta(days=days_valid)).isoformat() + "Z",
        "max_nodes": max_nodes,
        "max_gpu_count": max_gpu_count,
        "plan_key": plan_key,
    }
    private_key = Ed25519PrivateKey.from_private_bytes(base64.b64decode(VENDOR_PRIVATE_KEY_B64))
    signature = private_key.sign(_canonical_payload_bytes(payload))
    return {"payload": payload, "signature_b64": base64.b64encode(signature).decode()}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--customer", required=True)
    parser.add_argument("--days", type=int, default=365)
    parser.add_argument("--max-nodes", type=int, default=1)
    parser.add_argument("--max-gpu", type=int, default=0)
    parser.add_argument("--plan", default="on_prem_standard")
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    envelope = generate_license(args.customer, args.days, args.max_nodes, args.max_gpu, args.plan)
    with open(args.out, "w") as f:
        json.dump(envelope, f, indent=2)
    print(f"Wrote {args.out} for '{args.customer}', valid {args.days} days, expires {envelope['payload']['expires_at']}")
