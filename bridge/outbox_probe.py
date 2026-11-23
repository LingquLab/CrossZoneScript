# Copyright (c) 2026 TileXR Project.
"""Read Bridge queue metadata and test one small message to the selected session.

Run locally from the operator's Bridge installation. Credentials stay in memory;
the existing database, pending results and simulator processes are unchanged.
"""

import argparse
import getpass
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid


def queue_metadata(database, read_wire):
    if not database.is_file():
        return {"database_exists": False}
    with sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True) as db:
        rows = db.execute("SELECT id,text FROM outbox WHERE sent=0 ORDER BY rowid LIMIT 5").fetchall()
        pending = db.execute("SELECT count(*) FROM outbox WHERE sent=0").fetchone()[0]
    messages = []
    for event_id, text in rows:
        event = read_wire(text)
        request = {"text": text, "localId": event_id, "deliveryMode": "queue"}
        messages.append({"event_id": event_id, "type": event.get("type"),
                         "task_id": event.get("task_id"), "iteration": event.get("iteration"),
                         "status": event.get("status"), "text_bytes": len(text.encode("utf-8")),
                         "http_body_bytes": len(json.dumps(request).encode("utf-8"))})
    return {"database_exists": True, "pending_count": pending, "first_messages": messages}


def small_post(config, session, client_class):
    key = config.get("access_key") or os.environ.get("HAPI_ACCESS_KEY") or getpass.getpass("HAPI access key (hidden): ")
    client = client_class(config["hub_url"], key)
    try:
        token = client.authenticate()
    except Exception as error:
        return {"stage": "auth", "error_class": type(error).__name__, "status": getattr(error, "status", None)}
    event_id = "bridge-probe-" + uuid.uuid4().hex
    body = json.dumps({"text": "Bridge diagnostic probe: small message delivery check.",
                       "localId": event_id, "deliveryMode": "queue"}).encode("utf-8")
    request = urllib.request.Request(client.base + "sessions/" + session + "/messages", data=body,
                                     headers={"Content-Type": "application/json", "User-Agent": "cross-zone-bridge-probe",
                                              "Authorization": "Bearer " + token}, method="POST")
    result = {"stage": "small_post", "request_bytes": len(body), "event_id": event_id}
    try:
        with client.opener.open(request, timeout=20) as response:
            result["status"] = response.status
    except urllib.error.HTTPError as error:
        raw = error.read(4096)
        result.update(status=error.code, content_type=error.headers.get("Content-Type", ""),
                      server=error.headers.get("Server", ""), response_prefix_bytes=len(raw))
        text = raw.decode("utf-8", errors="replace")
        result["response_kind"] = "html" if re.search(r"<html|<!doctype", text, re.I) else "json" if text.lstrip().startswith("{") else "other"
        title = re.search(r"<title[^>]*>([^<]{1,160})</title>", text, re.I)
        if title:
            result["response_title"] = title.group(1).replace(key, "[redacted]").replace(token, "[redacted]")
        error.close()
    except (OSError, urllib.error.URLError) as error:
        result["error_class"] = type(error).__name__
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--session", required=True, type=lambda value: str(uuid.UUID(value)))
    parser.add_argument("--bridge-root", type=Path, default=Path.cwd())
    parser.add_argument("--config", type=Path, default=Path("config.local.json"))
    parser.add_argument("--no-post", action="store_true")
    args = parser.parse_args()
    root = args.bridge_root.resolve()
    sys.path.insert(0, str(root))
    from cross_zone.config import load_config
    from cross_zone.hapi import HapiClient
    from cross_zone.protocol import read_wire
    config_path = args.config.resolve()
    result = {"session": args.session, "queue": queue_metadata(config_path.parent / ".state" / args.session / "bridge.sqlite3", read_wire)}
    if not args.no_post:
        result["probe"] = small_post(load_config(config_path), args.session, HapiClient)
    print(json.dumps(result, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
