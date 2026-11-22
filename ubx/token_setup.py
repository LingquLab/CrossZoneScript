"""Stage the RDMA packet-cap matrix and reuse the verified maxTokens=2 read runs."""

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path, PurePosixPath
import re
import shlex
import sys
import tempfile

from deploy import deploy
from install_keepalive import install as enable_recovery
from stage_sources import connect, download, run_remote, stage

CASES = [(branch, op, bs, mode, limit)
         for branch, op in (("stages", "read"), ("sleep", "write"))
         for bs in (32, 64, 128) for limit in (1, 2) for mode in ("off", "on")]

SEED = r'''
import json
from pathlib import Path
import shutil
import sys

root = Path(sys.argv[1])
prior = root / "prior"
old_sleep_limits = set()
for bs in (32, 64, 128):
    for mode in ("off", "on"):
        old = prior / "runs" / f"sleep-write-bs{bs}-{mode}" / "node-0.json"
        rows = json.loads(old.read_text())
        old_sleep_limits.update((row["max_tokens"], row["effective_max_tokens"])
                                for rank in rows.values() for row in rank.values())
        source = prior / "runs" / f"stages-read-bs{bs}-{mode}"
        target = root / "runs" / f"stages-read-bs{bs}-max2-{mode}"
        target.mkdir(parents=True, exist_ok=True)
        filename = "timing.json"
        summary = json.loads((source / filename).read_text())
        if summary["samples"] != 640 or not summary["native"]:
            raise RuntimeError("incomplete prior read measurement")
        reference = str(source / filename)
        summary.update(case=target.name, max_tokens=2, effective_max_tokens=2,
                       reused_from=reference)
        for kind, round_id in summary["typical_rounds"].items() if mode == "on" else ():
            trace = f"{kind}-round-{round_id}.perfetto.json"
            shutil.copy2(source / trace, target / trace)
        (target / filename).write_text(json.dumps(summary, indent=2), encoding="utf-8")
        benchmark = {key: value for key, value in summary.items()
                     if key not in ("stages", "native_task_us", "native_task_round_slowest_rank_us")}
        (target / "report.json").write_text(json.dumps(benchmark), encoding="utf-8")
print(json.dumps({"reused_read_max2": 6, "previous_sleep_requested_effective": sorted(old_sleep_limits)}))
'''


def start(args):
    namespace, previous = args["namespace"], args["previous"]
    if any(not re.fullmatch(r"[a-zA-Z0-9_-]+", key) for key in (namespace, previous)):
        raise ValueError("invalid packet campaign directory")
    coordinator, peer = args["hosts"]
    with tempfile.TemporaryDirectory(prefix="tilexr-ubx-token-") as tmp:
        archive, digest = download(Path(tmp), args["revisions"])
        with ThreadPoolExecutor(max_workers=2) as pool:
            directories = list(pool.map(
                lambda host: stage(host, namespace, archive, digest), args["hosts"]))
        print(json.dumps({"source_sha256": digest, "nodes": directories}), flush=True)

    for index, host in enumerate(args["hosts"]):
        with connect(host) as client, client.open_sftp() as transfer:
            directory = PurePosixPath(directories[index])
            root = shlex.quote(str(directory))
            past = shlex.quote(str(directory.parent / previous))
            command = (f"set -eu; umask 077; cd {root}; test -d {past}/installs; "
                       f"ln -sfn {past} prior; mkdir -p trees installs builds ready runs")
            if run_remote(client, command)[0]:
                raise RuntimeError("prior benchmark packages unavailable")
            with transfer.file(str(directory / "matrix.json.part"), "w") as file:
                file.write(json.dumps(CASES))
            transfer.posix_rename(str(directory / "matrix.json.part"), str(directory / "matrix.json"))
            if index == 0:
                rc, out, _ = run_remote(client, "/usr/bin/python3 - " + root, input_text=SEED)
                if rc:
                    raise RuntimeError("could not confirm prior Combine passes")
                print(out, flush=True)

    builder = {"namespace": namespace, "files": ["build_tokens.sh"], "program": "build_tokens.sh",
               "unit": "build", "interpreter": "/bin/bash"}
    with ThreadPoolExecutor(max_workers=2) as pool:
        for host, snapshot in zip(args["hosts"], pool.map(lambda host: deploy(host, builder), args["hosts"])):
            print(json.dumps({"host": host, "build": snapshot}), flush=True)

    enable_recovery(args)


if __name__ == "__main__":
    start(json.loads(Path(sys.argv[2]).read_text(encoding="utf-8")))
