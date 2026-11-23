"""Run the immutable EP64 sender and single-chip reduction SST cases."""
import base64
from concurrent.futures import ThreadPoolExecutor
import gzip
import hashlib
import io
import json
from pathlib import Path
import subprocess
import tarfile

from coverage import coverage

root = Path(__file__).resolve().parent
repo = root.parent
support = repo / "inter_server"
ssh = ["ssh", "-T", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes",
       "-o", "ConnectTimeout=10", "root@141.61.94.58", "python3", "-"]


def run_remote(source, timeout):
    compile(source, "<t32_sst_remote>", "exec")
    try:
        proc = subprocess.run(ssh, input=source.encode(), capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"transport_timeout": True}
    if proc.returncode:
        return {"ssh_exit": proc.returncode, "stderr_bytes": len(proc.stderr)}
    try:
        return json.loads(proc.stdout)
    except ValueError:
        return {"invalid_output": True, "stdout_bytes": len(proc.stdout)}


def run_send():
    folder = root / "send"
    identity = json.loads((folder / "fixture-identity.json").read_text())
    build = json.loads((folder / "build-record.json").read_text())
    fixture = (folder / "fixture-images.tar.gz").read_bytes()
    obj = (folder / "inter_server_send.o.gz").read_bytes()
    assert hashlib.sha256(fixture).hexdigest() == identity["input_archive_sha256"]
    assert hashlib.sha256(gzip.decompress(obj)).hexdigest() == build["kernel_sha256"]
    with tarfile.open(fileobj=io.BytesIO(fixture)) as archive:
        stats, rings = coverage(archive)
    assert json.loads((folder / "coverage.json").read_text()) == json.loads(json.dumps(stats))
    build.update(binary_source_commit=build["candidate_commit"],
                 candidate_commit="b13556eb4f84afc103ba993f4e9151dc104d2b95",
                 variant="sst-mte-t32-send")
    req = dict(build=build, identity=identity, coverage=stats, rings=rings,
               archive=base64.b64encode(fixture).decode(), object=base64.b64encode(obj).decode())
    native = (support / "run_mte_t4_remote.py").read_text().rsplit("if __name__ == ", 1)[0]
    assert native.count("assert tokens == 4") == 1
    native = native.replace("assert tokens == 4", "assert tokens == 32")
    compare = (repo / "ht_t4_send_ab/remote.py").read_text().split("def run_send(", 1)[0]
    trace = (support / "owner_trace_remote.py").read_text().split("def doorbell_coordinates(", 1)[0]
    observe = "def read_observed(" + (support / "diagnose_t96_sq_remote.py").read_text().split(
        "def read_observed(", 1)[1].split("def diagnose_t96(", 1)[0]
    script = "\n".join((native, (root / "prepare.py").read_text(), compare, trace, observe,
                        (root / "send_remote.py").read_text()))
    return run_remote(script + f"\nprint(json.dumps(run_t32({req!r}), separators=(',', ':')))\n", 2250)


def run_reduce(tag):
    folder = root / tag
    identity = json.loads((folder / "opt.json").read_text())
    archive = (folder / "opt.tar.gz").read_bytes()
    assert hashlib.sha256(archive).hexdigest() == identity["archive_sha256"]
    runner = (repo / "ht_t4_reduce_gt/remote.py").read_text()
    return run_remote(f"IDENTITY = {identity!r}\nPACKAGE = {base64.b64encode(archive).decode()!r}\n" + runner, 175)


def run_reduce_pair():
    return {kind: run_reduce(kind) for kind in ("bf16", "fp32")}


with ThreadPoolExecutor(max_workers=2) as pool:
    send = pool.submit(run_send)
    reduce = pool.submit(run_reduce_pair)
    result = {"send": send.result(), "reduce": reduce.result()}
print(json.dumps(result, separators=(",", ":")))
