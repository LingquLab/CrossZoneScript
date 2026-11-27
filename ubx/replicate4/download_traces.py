"""Copy the verified round-59 traces to a directory on the Green computer."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import shlex
import tarfile
import tempfile

from benchmark import HOSTS, ssh
import retest_disjoint_routes as replicate4
import retest_replicate16 as replicate16
import retest_same_lane as baseline

CASES = {"baseline": baseline, "replicate4": replicate4, "replicate16": replicate16}
TRACE_NAME = "dispatch.last.trace.json"


def fetch(name, case, host_index, staging):
    host = HOSTS[host_index]
    source = PurePosixPath(case.WORK) / "full-bs32-f50"
    ranks = range(16 * host_index, 16 * (host_index + 1))
    traces = {f"rank{rank}/{TRACE_NAME}": rank for rank in ranks}
    command = shlex.join(["tar", "-cz", "-C", str(source), "host-result.json", *traces])
    raw = ssh(host, command, timeout=180, stage=f"{name} trace download")
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as archive:
        members = archive.getmembers()
        expected_paths = set(traces) | {"host-result.json"}
        assert len(members) == len(expected_paths) and {m.name for m in members} == expected_paths
        assert all(m.isfile() for m in members)
        receipt = json.load(archive.extractfile("host-result.json"))
        assert receipt["passed"] and receipt["revision"] == case.REVISION
        assert receipt["profile_mode"] == "full" and receipt["measured"] == 50
        expected_hashes = {row["rank"]: row["trace_sha256"] for row in receipt["ranks"]}
        assert len(receipt["ranks"]) == 16 and set(expected_hashes) == set(ranks)
        downloaded = {}
        for filename, rank in traces.items():
            data = archive.extractfile(filename).read()
            digest = hashlib.sha256(data).hexdigest()
            assert digest == expected_hashes[rank], (name, host, rank, "trace hash mismatch")
            metadata = json.loads(data)["metadata"]
            assert metadata["revision"] == case.REVISION and metadata["round"] == 59
            assert metadata["logical_rank"] == rank
            relative = Path(name) / filename
            path = staging / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            downloaded[relative] = digest
    return downloaded


def download(destination):
    destination.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".trace-download-", dir=destination) as directory:
        staging = Path(directory)
        with ThreadPoolExecutor(max_workers=2) as pool:
            tasks = [pool.submit(fetch, name, case, host_index, staging)
                     for name, case in CASES.items() for host_index in range(len(HOSTS))]
            expected = {path: digest for task in tasks for path, digest in task.result().items()}
        assert len(expected) == 96
        for relative, digest in expected.items():
            target = destination / relative
            if target.exists() and hashlib.sha256(target.read_bytes()).hexdigest() != digest:
                raise FileExistsError(f"Different trace already exists: {target}")
        for relative, digest in sorted(expected.items()):
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            if not target.exists():
                (staging / relative).rename(target)
            assert hashlib.sha256(target.read_bytes()).hexdigest() == digest
    return {
        "destination": str(destination),
        "round": 59,
        "verified_files": len(expected),
        "cases": {name: sum(path.parts[0] == name for path in expected) for name in CASES},
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--args-file", required=True, type=Path)
    args = json.loads(parser.parse_args().args_file.read_text(encoding="utf-8"))
    destination = Path(args["destination"])
    assert destination.is_absolute()
    print(json.dumps(download(destination), separators=(",", ":")))
