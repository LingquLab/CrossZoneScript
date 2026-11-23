"""Read only the saved native profiling-off statistics for the original RECV assist."""

import json
import os
from pathlib import Path, PurePosixPath
import shlex
import sys
from zipfile import ZipFile

from stage_sources import connect, run_remote

REVISION = "e8ee4fffc2da1dfdf1acf7f67ac9a71129de868b"
BATCHES = (64, 128, 32)
CAMPAIGN = "ep32-assist-e8ee4fff-377fbe6d"
ARCHIVE = "TileXR-UBX-EP32-Dispatch-assist-e8ee4fff.zip"


def summarize(batch, timing):
    provenance = dict(line.split("=", 1) for line in timing["provenance"] if "=" in line)
    if (timing["case"] != f"tail-reviewed-dispatch-bs{batch}-off" or timing["samples"] != 640
            or not timing["native"] or provenance.get("revision") != REVISION
            or provenance.get("kernel_profiling") != "off"):
        raise ValueError("saved timing does not match the expected benchmark")
    numbers = timing["native_task_us"]["round_slowest_rank"]
    if not 0 < numbers["min"] <= numbers["median"] <= numbers["max"]:
        raise ValueError("invalid saved statistics")
    return {key: round(numbers[key], 4) for key in ("min", "median", "max")}


def read_stats(host):
    archive = Path(os.environ["USERPROFILE"]) / "Downloads" / ARCHIVE
    if archive.is_file():
        with ZipFile(archive) as bundle:
            stats = {batch: summarize(batch, json.loads(bundle.read(
                f"runs/tail-reviewed-dispatch-bs{batch}-off/timing.json"))) for batch in BATCHES}
        return stats, "saved green archive"
    with connect(host) as client, client.open_sftp() as transfer:
        root = PurePosixPath(transfer.normalize(".")) / ".local/state/tilexr-ubx" / CAMPAIGN / "runs"
        stats = {}
        for batch in BATCHES:
            path = root / f"tail-reviewed-dispatch-bs{batch}-off" / "timing.json"
            code, output, _ = run_remote(client, "cat -- " + shlex.quote(str(path)))
            if code:
                raise RuntimeError("saved timing unavailable")
            stats[batch] = summarize(batch, json.loads(output))
        return stats, "saved coordinator timing files"


if __name__ == "__main__":
    args = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
    stats, source = read_stats(args["host"])
    print(json.dumps(dict(revision=REVISION, statistics=stats, source=source), separators=(",", ":")))
