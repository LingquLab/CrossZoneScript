# Copyright (c) 2026 TileXR Project.
"""Back up one unsent inspection result and replace only its verbose summary.

Runs in the bound Bridge session directory. SQLite serializes the queue update;
event identity, execution facts, checks and task state remain unchanged.
"""

import argparse
import hashlib
import json
from pathlib import Path
import sqlite3
import uuid


def compact(root, args):
    if root.name != args["session"]:
        raise ValueError("session directory mismatch")
    database = root / "bridge.sqlite3"
    if not database.is_file():
        raise ValueError("session queue missing")
    changed = []
    with sqlite3.connect(database, timeout=15) as db:
        db.execute("BEGIN IMMEDIATE")
        for event_id, text in db.execute("SELECT id,text FROM outbox WHERE sent=0 ORDER BY rowid").fetchall():
            event = json.loads(text[text.index("{"):text.rindex("}") + 1])
            if (event.get("type") != "RESULT" or event.get("task_id") != args["task_id"]
                    or event.get("iteration") != args["iteration"]):
                continue
            if event.get("status") != "PASS" or event.get("execution", {}).get("exit_code") != 0:
                raise ValueError("inspection result does not establish successful command execution")
            if event.get("event_id") != event_id:
                raise ValueError("queue event identity mismatch")
            old_summary = event["summary"]
            if old_summary.startswith("Compact inspection result:"):
                continue
            position = old_summary.index("{")
            evidence = json.loads(old_summary[position:])
            report = evidence["report"]
            if not report.startswith("/work/cases/8p-bs4-") or "/wiring-inspect-" not in report:
                raise ValueError("unexpected inspection report")
            event["summary"] = "Compact inspection result: " + json.dumps({
                "report": report, "model_file_count": len(evidence.get("model_files", [])),
                "scope": "read-only model wiring inspection; operator validation not performed",
                "details": "Full original result retained in the green session queue backup."})
            replacement = "CROSS_ZONE\n```json\n" + json.dumps(event, ensure_ascii=True, indent=2) + "\n```"
            if len(replacement.encode("utf-8")) > 4096:
                raise ValueError("compacted result still exceeds diagnostic budget")
            backups = root / "outbox-backups"
            backups.mkdir(exist_ok=True)
            backup = backups / ("inspection-" + uuid.uuid4().hex + ".json")
            with backup.open("x", encoding="utf-8") as saved:
                json.dump({"event_id": event_id, "original_text": text,
                           "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest()}, saved)
            count = db.execute("UPDATE outbox SET text=? WHERE id=? AND sent=0 AND text=?",
                               (replacement, event_id, text)).rowcount
            if count != 1:
                raise RuntimeError("queue changed before update")
            changed.append({"event_id": event_id, "old_bytes": len(text.encode("utf-8")),
                            "new_bytes": len(replacement.encode("utf-8")), "backup": str(backup)})
    return {"ok": True, "changed": changed, "changed_count": len(changed)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--args-file", type=Path, required=True)
    arguments = json.loads(parser.parse_args().args_file.read_text(encoding="utf-8"))
    print(json.dumps(compact(Path.cwd(), arguments), ensure_ascii=True))
