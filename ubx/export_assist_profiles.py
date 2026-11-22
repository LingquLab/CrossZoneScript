"""Copy the current Dispatch profiling files to the green Windows downloads folder."""

from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shlex
import sys
import uuid
from zipfile import ZipFile

from stage_sources import connect, run_remote
from tail_queue import ASSIST_REVISION, assist_identity


COLLECT = r'''
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
from zipfile import ZIP_DEFLATED, ZipFile

root = Path(sys.argv[1])
destination = root / "exports" / sys.argv[2]
revision = sys.argv[4]
provenance = "installs/tail-reviewed-on/provenance.txt"
if f"revision={revision}" not in (root / provenance).read_text().splitlines():
    raise ValueError("capture provenance differs from the requested commit")
destination.parent.mkdir(exist_ok=True)
temporary = destination.with_suffix(".part")
cases = sorted((root / "runs").glob("*-on"))
if sys.argv[3] == "snapshot":
    cases += sorted((root / "attempts").glob("*/*-on"))
entries = []
with ZipFile(temporary, "w", compression=ZIP_DEFLATED, compresslevel=1) as archive:
    for relative in ("matrix.json", "status.json", "report-status.json", "retry-status.json",
                     "retry-remaining.json", "restore_expert_copy.patch",
                     provenance):
        path = root / relative
        if path.is_file():
            archive.writestr(relative, path.read_bytes())
    changed = []
    for case in cases:
        name = case.relative_to(root).as_posix()
        counts = {"rank_summaries": 0, "record_captures": 0, "analysis_files": 0, "files": 0}
        for path in sorted(case.rglob("*")):
            relative = path.relative_to(root)
            if ("peer-analysis-inputs" in relative.parts or path.is_symlink()
                    or not path.is_file() or path.suffix == ".part"):
                continue
            try:
                stamp = path.stat()
                archive.write(path, relative.as_posix())
                later = path.stat()
            except FileNotFoundError:
                changed.append(relative.as_posix())
                continue
            if (stamp.st_size, stamp.st_mtime_ns) != (later.st_size, later.st_mtime_ns):
                changed.append(relative.as_posix())
            counts["files"] += 1
            counts["rank_summaries"] += path.name.endswith(".summary.json")
            counts["record_captures"] += path.name.endswith(".records.bin")
            counts["analysis_files"] += path.parent.name == "tail-report" and path.suffix == ".json"
        entries.append({"directory": name, "completed_node": any(case.glob("node-*.json")), **counts})
    index = {"revision": revision,
             "snapshot_utc": datetime.now(timezone.utc).isoformat(),
             "cases": entries, "files_changed_during_snapshot": changed}
    archive.writestr("snapshot-index.json", json.dumps(index, indent=2) + "\n")
temporary.replace(destination)
with destination.open("rb") as handle:
    digest = hashlib.file_digest(handle, "sha256").hexdigest()
print(json.dumps({"path": str(destination), "size": destination.stat().st_size, "sha256": digest,
                  "cases": entries, "changed_files": len(changed)}, separators=(",", ":")))
'''


def export(host, stamp, snapshot=True, revision=ASSIST_REVISION):
    revision, namespace = assist_identity({"revision": revision})
    name = f"TileXR-{revision[:8]}-profiling-on-{host.rsplit('.', 1)[-1]}-{stamp}.zip"
    with connect(host) as client, client.open_sftp() as transfer:
        root = PurePosixPath(transfer.normalize(".")) / ".local/state/tilexr-ubx" / namespace
        mode = "snapshot" if snapshot else "complete"
        command = "nice -n 10 /usr/bin/python3 -B - " + shlex.join([str(root), name, mode, revision])
        code, output, _ = run_remote(client, command, timeout=540, input_text=COLLECT)
        if code:
            raise RuntimeError(f"could not collect profiling snapshot from {host}")
        data = json.loads(output)
        if not snapshot and (data["changed_files"] or len(data["cases"]) != 3
                             or any(not case["completed_node"] or case["rank_summaries"] != 400
                                    or case["record_captures"] != 400 for case in data["cases"])):
            raise ValueError(f"incomplete final profiling coverage on {host}")
        downloads = Path(os.environ["USERPROFILE"]) / "Downloads"
        target = downloads / name
        temporary = target.with_suffix(".part")
        transfer.get_channel().settimeout(125)
        transfer.get(data["path"], str(temporary))
        with temporary.open("rb") as handle:
            if hashlib.file_digest(handle, "sha256").hexdigest() != data["sha256"]:
                raise ValueError(f"profiling snapshot checksum differs for {host}")
        with ZipFile(temporary) as archive:
            if archive.testzip() is not None:
                raise ValueError(f"profiling snapshot ZIP is corrupt for {host}")
        temporary.replace(target)
    data["green_windows_bundle"] = str(target)
    return {"host": host, **data}


if __name__ == "__main__":
    arguments = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
    stamp = uuid.uuid4().hex[:8]
    revision, _ = assist_identity(arguments)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(export, host, stamp, True, revision) for host in arguments["hosts"]]
        for future in futures:
            print(json.dumps(future.result(), separators=(",", ":")), flush=True)
