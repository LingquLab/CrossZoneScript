# Copyright (c) 2026 TileXR Project.
"""Read the requested simulator host identity and bounded platform metadata."""
import json
import platform
import shutil
import subprocess
from pathlib import Path

IMAGE = "sha256:c2cb1056c975f1e24805f7d1e21d6cdc60e9789fff1647fac5dae9b9d782af5f"


def command(argv):
    return subprocess.run(argv, capture_output=True, text=True, timeout=15)


def main():
    hostname = command(["hostname", "-s"]).stdout.strip()
    assert hostname == "host-94-26", "unexpected_host"
    assert platform.machine() == "aarch64", "unexpected_architecture"
    version = command(["docker", "version", "--format", "{{.Server.Version}}"])
    assert version.returncode == 0, "docker_unavailable"
    image = command(["docker", "image", "inspect", "--format", "{{.Id}} {{.Os}}/{{.Architecture}}", IMAGE])
    image_present = image.returncode == 0
    if image_present:
        assert image.stdout.strip() == IMAGE + " linux/arm64", "image_identity_mismatch"
    archive = Path("/mnt/share/simulator/images/tilexr-sim-9.1-aarch64-v1.1.0.tar.gz")
    disks = []
    for name in ("/mnt/share", "/var/tmp"):
        if Path(name).is_dir():
            usage = shutil.disk_usage(name)
            disks.append(dict(path=name, free_bytes=usage.free, total_bytes=usage.total))
    templates = []
    root = Path("/mnt/share/tilexr-sim-jobs")
    if root.is_dir():
        for job in sorted(root.iterdir()):
            if not job.is_dir():
                continue
            base = job / "inter-server-native"
            case = base / "t4-sst-mte-bulk-forward-hotpath-1a2f3549"
            if not case.is_dir():
                continue
            row = dict(path=str(case), manifest=(case / "case-manifest.json").is_file(),
                       environment=(case / "sst-environment.json").is_file())
            run = case / "run.json"
            if run.is_file() and run.stat().st_size < 1024 * 1024:
                data = json.loads(run.read_text())
                row["status"] = data.get("status")
            templates.append(row)
            if len(templates) == 6:
                break
    print(json.dumps(dict(ok=True, hostname=hostname, architecture=platform.machine(),
                         docker=version.stdout.strip(), image=IMAGE, image_present=image_present,
                         archive_bytes=archive.stat().st_size if archive.is_file() else None,
                         disks=disks, templates=templates), separators=(",", ":")))


if __name__ == "__main__":
    main()
