"""Read Memory SST receipts and verify deployment hashes on the SST host."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys


bind, container, revision = sys.argv[1:]
root = Path(bind)


def read(name):
    return json.loads((root / name).read_text())


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


baseline = dict(
    memory_ordered="6042267a5dcb4596e8b71020b64153b119773d2d692bc489c004f910454ec70b",
    memory_sentinel="982e895fa0aed1bdad97a5c818b2a62c09672228519e6f7b148a4b6b60707e42",
)
qualification = read("receipts/qualification.json")
assert qualification["revision"] == revision == read("source-lock.json")["revision"]
accepted = read("results/acceptance.json")
results = {}
for variant in accepted:
    previous_hash = baseline.get(variant)
    case = f"results/{variant}"
    report = read(f"{case}/result.json")
    assert report["status"] == report["operator_result"]["status"] == "passed"
    assert list(report["phases"]) == ["prepare", "run"]
    assert all(stage["status"] == "passed" for stage in report["phases"].values())
    evidence = report["tool_result"]["sst_result"]
    assert evidence["revision"] == revision
    artifact = accepted[variant]["kernel"]
    entry = f"tilexr_alltoall_{variant}"
    assert artifact == evidence["kernel"] and artifact["entry"] == entry
    object_hash = digest(root / f"suites/{variant}/kernel.o")
    assert object_hash == artifact["sha256"] == qualification["kernels"][variant]["sha256"]
    assert object_hash == digest(root / f"build/{entry}.o")
    ranks = read(f"{case}/run/ranks.json")
    rank_size = report["case"]["world_size"]
    assert [row["rank"] for row in ranks] == list(range(rank_size))
    for row in ranks:
        assert row["checked_bytes"] == 512 * rank_size and row["output_sha256"] == row["golden_sha256"]
        assert all(row[k] == "passed" for k in ("payload", "guards", "padding", "kernel_status"))
    process = read(f"{case}/run/process.json")
    assert process["runner_returncode"] == 0 and not process["remaining_processes"]
    libraries = evidence["loaded_libraries"]
    assert libraries["state"] == "sst_library_mappings_verified" and len(libraries["observed"]) == 4
    assert libraries["observed"] == process["observed_libraries"]
    results[variant] = dict(entry=entry, sha256=object_hash, previous_sha256_matches=(object_hash == previous_hash if previous_hash else None),
                            ranks=len(ranks), simulated_us=evidence["simulated_us"], actual_libraries=4)
cleanup = (root / "container-cleanup.log").read_text()
assert "CONTAINER_REMOVED " + qualification["container_id"] in cleanup
inspect = subprocess.run(["docker", "container", "inspect", container], capture_output=True)
assert inspect.returncode == 1
print(json.dumps(dict(stage="verified", revision=revision, bind=bind, container_removed=True, cases=results)))
