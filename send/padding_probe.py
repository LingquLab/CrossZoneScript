"""Replay retained machine code with only the binary image capacity changed."""
import hashlib
import json
from pathlib import Path
import socket
import subprocess
import sys

SESSION = "7626e689-38e9-468b-9a82-63673422e9d3"
NAME = "tilexr-combine-ht-" + SESSION[:8] + "-v11"
IMAGE = "sha256:c2cb1056c975f1e24805f7d1e21d6cdc60e9789fff1647fac5dae9b9d782af5f"
IMAGE_BYTES = 10485760
OLD = Path("/var/tmp/tilexr-combine-ht-05d1d635-d6d7-4dd3-af34-421e32bb209c-v11")
SOURCES = (("interleave", OLD / "mte-interleave-8", "b9728abbdad99b8984cfe9ed9e88b5c9201c471ba65e5aad56338504b836af40", None),
           ("vector", None, "b82fdcd6df454503d237d9cb64dcd2b23fe9db48b10b067844b8f5ec30a011e2", "store_post_runtime_vector"))


def prepare(root, source, label, expected_sha, probe_kind):
    record = json.loads((source / "trial-result.json").read_text())
    assert 0 < (source / "send.o").stat().st_size < IMAGE_BYTES
    raw = (source / "send.o").read_bytes()
    assert hashlib.sha256(raw).hexdigest() == record["object_sha256"] == expected_sha
    assert record["backward_record_bytes"] == 32 and record["mte_status"] is True
    assert record["stop_boundary"] == "before_sq_publish"
    assert 0 < len(raw) < IMAGE_BYTES
    folder = root / ("image-padding-1-" + label)
    folder.mkdir()
    padded = raw.ljust(IMAGE_BYTES, b"\0")
    (folder / "send.o").write_bytes(padded)
    assert padded[:len(raw)] == raw and not any(padded[len(raw):])
    metadata = {"case": folder.name, "source_case": source.name,
                "raw_object_sha256": expected_sha, "raw_object_bytes": len(raw),
                "padded_object_sha256": hashlib.sha256(padded).hexdigest(),
                "padded_object_bytes": len(padded), "machine_code_prefix_unchanged": True,
                "probe_kind": probe_kind}
    (folder / "padding-source.json").write_text(json.dumps(metadata, indent=2))
    return metadata


def save(root, value):
    packet = json.dumps(value, indent=2)
    (root / "image-padding-1-result.json").write_text(packet)
    (root / "padding-result.json").write_text(packet)


def inside_container():
    import yaml
    import trial
    sst, root = trial.sst, Path("/work")
    snapshot = json.loads(Path("/baseline/environment.snapshot.json").read_text())
    sst.verify_environment(snapshot)
    specs, routes = sst.fixtures.prepare(8, 4, 256, 16, 4, 40, 64)
    route_sha = hashlib.sha256(json.dumps(routes, separators=(",", ":")).encode()).hexdigest()
    assert route_sha == "f5eb786c6ca0a725607b16caef0ac8a8c0efc161576b185d9c74ffa2f4127881"
    prepared, _ = trial.repack_backward([spec.serialize() for spec in specs], 32)
    out = {"ok": False, "diagnostic_only": True, "simulation_only": True,
           "recompiled": False, "stage": "running", "route_sha256": route_sha, "cases": []}
    save(root, out)
    for label, _, _, _ in SOURCES:
        folder = root / ("image-padding-1-" + label)
        row = json.loads((folder / "padding-source.json").read_text())
        assert (folder / "send.o").stat().st_size == IMAGE_BYTES
        assert hashlib.sha256((folder / "send.o").read_bytes()).hexdigest() == row["padded_object_sha256"]
        sst.KERNEL = folder / "send.o"
        try:
            result = trial.capture(folder, specs, prepared, snapshot, "before_sq_publish", True, 32, row["probe_kind"])
            row.update(result)
            row["ok"] = result["wqe_verified"]
            workload = yaml.safe_load((folder / "capture/workload.yml").read_text())
            row["loaded_binary_bytes"] = workload["workload_config"]["chip0"]["bin_param"]["size"]
            assert row["loaded_binary_bytes"] == IMAGE_BYTES
        except Exception as error:
            row.update(ok=False, error=type(error).__name__)
        out["cases"].append(row)
        save(root, out)
        if row.get("returncode") not in (0, 255):
            break
    out.update(ok=len(out["cases"]) == 2 and all(row["ok"] for row in out["cases"]), stage="finished")
    save(root, out)
    print(json.dumps(out, separators=(",", ":")))
    return 0 if out["ok"] else 1


def host(args):
    assert socket.gethostname().split(".")[0] == args["hostname"] == "host-94-26"
    root = Path(__file__).resolve().parent
    assert root.name == "tilexr-combine-ht-" + SESSION + "-v11"
    assert not (root / "image-padding-1-result.json").exists(), "padding_probe_already_attempted"
    assert all(not (root / ("image-padding-1-" + label)).exists() for label, _, _, _ in SOURCES)
    info = subprocess.run(["docker", "inspect", NAME], check=True, capture_output=True, timeout=20)
    info = json.loads(info.stdout)[0]
    assert info["Image"] == IMAGE and info["Config"]["Labels"]["crosszone.session"] == SESSION
    assert info["State"]["Running"] and info["HostConfig"]["Runtime"] == "runc"
    assert info["HostConfig"]["NetworkMode"] == "none" and not info["HostConfig"].get("Devices")
    assert any(m["Source"] == str(root) and m["Destination"] == "/work" and m["RW"] for m in info["Mounts"])
    assert any(m["Destination"] == "/baseline" and not m["RW"] for m in info["Mounts"])
    top = subprocess.run(["docker", "top", NAME, "-eo", "pid,comm"], check=True, capture_output=True, timeout=15)
    assert b"sstsim" not in top.stdout, "simulator_already_running"
    rows = []
    for label, source, digest, probe_kind in SOURCES:
        source = source or root / "mte-scalar_store_post_runtime_vector-20"
        rows.append(prepare(root, source, label, digest, probe_kind))
    save(root, {"ok": False, "stage": "prepared", "diagnostic_only": True, "cases": rows})
    process = subprocess.run(["docker", "exec", NAME, "python3", "/work/padding_probe.py", "--container"],
                             capture_output=True, timeout=570)
    (root / "padding-host.log").write_bytes(process.stdout + b"\nSTDERR\n" + process.stderr)
    print(json.dumps(json.loads((root / "padding-result.json").read_text()), separators=(",", ":")))
    return process.returncode


if __name__ == "__main__":
    try:
        sys.exit(inside_container() if sys.argv[1] == "--container" else host(json.loads(sys.argv[1])))
    except Exception as error:
        print(json.dumps({"ok": False, "error": type(error).__name__, "detail": str(error)[:100]}))
        sys.exit(1)
