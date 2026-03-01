#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import time
import sys
import uuid
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def send_ingest(
    *,
    base_url: str,
    auth_token: str,
    signing_key: str,
    source: str,
    kind: str,
    task_id: str,
    content: str,
) -> dict | None:
    ts = str(int(time.time()))
    nonce = uuid.uuid4().hex
    payload = {
        "source": source,
        "kind": kind,
        "task_id": task_id,
        "content": content,
    }
    body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    signing_material = ts.encode("utf-8") + b"\n" + nonce.encode("utf-8") + b"\n" + body
    sig = hmac.new(signing_key.encode("utf-8"), signing_material, hashlib.sha256).hexdigest()

    headers = {
        "Authorization": f"Bearer {auth_token}",
        "X-Jarvis-Timestamp": ts,
        "X-Jarvis-Nonce": nonce,
        "X-Jarvis-Signature": sig,
        "Content-Type": "application/json",
    }
    req = Request(
        url=f"{base_url.rstrip('/')}/ingest",
        method="POST",
        data=body,
        headers=headers,
    )
    try:
        with urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8", errors="replace"))
    except HTTPError as e:
        sys.stderr.write(f"HTTP Error: {e.code} {e.reason}\n")
        return None
    except URLError as e:
        sys.stderr.write(f"URL Error: {e.reason}\n")
        return None
    except json.JSONDecodeError:
        sys.stderr.write("Error: Failed to decode JSON response from server.\n")
        return None


def main() -> int:
    parser = argparse.ArgumentParser(description="Samsung/Android client for Jarvis mobile ingest API.")
    parser.add_argument("--base-url", required=True, help="Example: http://192.168.1.10:8787")
    parser.add_argument("--auth-token", default=os.getenv("JARVIS_AUTH_TOKEN", ""))
    parser.add_argument("--signing-key", default=os.getenv("JARVIS_SIGNING_KEY", ""))
    parser.add_argument("--source", default="user", choices=["user", "claude", "opus", "gemini", "task_outcome"])
    parser.add_argument("--kind", default="episodic", choices=["episodic", "semantic", "procedural"])
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--content", required=True)
    args = parser.parse_args()
    if not args.auth_token:
        parser.error("Missing auth token. Use --auth-token or JARVIS_AUTH_TOKEN.")
    if not args.signing_key:
        parser.error("Missing signing key. Use --signing-key or JARVIS_SIGNING_KEY.")

    result = send_ingest(
        base_url=args.base_url,
        auth_token=args.auth_token,
        signing_key=args.signing_key,
        source=args.source,
        kind=args.kind,
        task_id=args.task_id,
        content=args.content,
    )
    if result is None:
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
