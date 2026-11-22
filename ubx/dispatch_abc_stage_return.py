"""Return the reviewed-scope numeric stage table through the current bridge."""

import csv
import hashlib
import io
import json
from math import isfinite
from pathlib import Path, PurePosixPath
import re
import sys

from dispatch_abc_setup import NAMESPACE
from stage_sources import connect


def export(raw):
    text = raw.decode("utf-8").replace("\r\n", "\n")
    rows = list(csv.reader(io.StringIO(text)))
    if rows[0] != ["bs", "variant", "role", "stage", "rounds", "min_us", "median_us", "max_us"]:
        raise ValueError("unexpected CSV header")
    indexes = set()
    for bs, variant, role, stage, rounds, minimum, median, maximum in rows[1:]:
        if (bs not in {"64", "128"} or variant not in {"A", "B", "C"}
                or role not in {"SEND", "RECV", "RELAY", "INTRA_SEND"}
                or not re.fullmatch(r"[a-z][a-z0-9_]+", stage) or not 1 <= int(rounds) <= 20):
            raise ValueError("unexpected stage identity")
        times = tuple(map(float, (minimum, median, maximum)))
        if not all(isfinite(value) for value in times) or not 0 <= times[0] <= times[1] <= times[2]:
            raise ValueError("unexpected stage timing")
        indexes.add((bs, variant, role, stage))
    if len(rows) != 307 or len(indexes) != 306:
        raise ValueError("stage table lost rows or contains duplicates")
    return text, hashlib.sha256(text.encode("utf-8")).hexdigest()


if __name__ == "__main__":
    args = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
    with connect(args["host"]) as client, client.open_sftp() as transfer:
        root = PurePosixPath(transfer.normalize(".")) / ".local/state/tilexr-ubx" / NAMESPACE
        with transfer.open(str(root / "stages-bs64-bs128-max2" / "stages.csv"), "rb") as handle:
            table, digest = export(handle.read(30000))
    print(json.dumps({"filename": "TileXR-UBX-ABC-BS64-BS128-max2-stages.csv",
                      "rows": 306, "sha256_lf": digest}, separators=(",", ":")))
    print(table, end="")
