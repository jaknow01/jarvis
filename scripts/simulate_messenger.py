"""Dev helper: POST a signed fake Messenger message to the local webhook.

Exercises the full inbound pipeline (X-Hub-Signature-256 check → engine
→ coordinator → Send API) without going through Meta. The Send API call at the
end will only actually deliver if MESSENGER_PAGE_ACCESS_TOKEN + a real recipient
PSID are valid; otherwise it just logs an error (swallowed), which is fine — the
point is to verify the webhook wiring and see the coordinator's reply in the logs.

Usage:
    poetry run python scripts/simulate_messenger.py "jaka jest pogoda w Warszawie?"
    poetry run python scripts/simulate_messenger.py --url http://localhost:8002/webhook "hej"

Reads MESSENGER_APP_SECRET (to sign) from the environment / .env.
"""

import argparse
import hashlib
import hmac
import json
import os
import sys

import httpx
from dotenv import load_dotenv

load_dotenv()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("text", help="message text to send as the fake user")
    parser.add_argument("--url", default="http://localhost:8002/webhook")
    parser.add_argument("--sender", default="DEV_TESTER", help="fake PSID")
    args = parser.parse_args()

    app_secret = os.getenv("MESSENGER_APP_SECRET", "")
    if not app_secret:
        print("MESSENGER_APP_SECRET is not set (needed to sign the payload).")
        return 1

    body = {
        "object": "page",
        "entry": [
            {
                "messaging": [
                    {
                        "sender": {"id": args.sender},
                        "message": {"text": args.text},
                    }
                ]
            }
        ],
    }
    raw = json.dumps(body).encode()
    signature = "sha256=" + hmac.new(app_secret.encode(), raw, hashlib.sha256).hexdigest()

    resp = httpx.post(
        args.url,
        content=raw,
        headers={
            "Content-Type": "application/json",
            "X-Hub-Signature-256": signature,
        },
        timeout=30,
    )
    print(f"POST {args.url} -> {resp.status_code}")
    print("Watch the server logs for the [MESSENGER ...] line and the reply.")
    return 0 if resp.status_code == 200 else 2


if __name__ == "__main__":
    sys.exit(main())
