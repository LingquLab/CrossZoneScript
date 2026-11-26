"""Build the isolated Dispatch revision and rerun BS32 against the pinned runtime."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path, PurePosixPath
import tempfile

import benchmark
from build import HOSTS, ROOT, SSH, TILEXR, archive, pin, remote

REVISION = "c9c86235baed1451557ecb7ead987b875b36f905"
WORK = ROOT + "-disjoint"


def build_dispatch(host, source, sha256, script, work, revision):
    peer = "root@" + host
    remote(host, SSH, peer, f"mkdir {work}")
    remote(host, SSH.with_name("scp.exe"), str(source), f"{peer}:{work}/source.tar.gz", timeout=150)
    out = benchmark.ssh(host, f"bash -s -- {work} {ROOT} {sha256} {revision}",
                        payload=script, timeout=520, stage="disjoint route build")
    receipt = json.loads(out)
    assert receipt["built"] and receipt["revision"] == revision
    return receipt


def main(revision=REVISION, work=WORK, mode="replicate4", ports=(26000, 28000, 24500)):
    script = Path(__file__).with_name("build_disjoint_routes.sh").read_bytes().replace(b"\r\n", b"\n")
    with tempfile.TemporaryDirectory(prefix="ubx-disjoint-routes-", dir=Path.cwd()) as workspace:
        directory = Path(workspace).resolve()
        repo = directory / "tilexr"
        pin(repo, TILEXR, (revision,))
        source = archive(directory, repo, "dispatch-disjoint", revision, "dispatch/dispatch-release")
        sha256 = hashlib.sha256(source.read_bytes()).hexdigest()
        with ThreadPoolExecutor(max_workers=2) as pool:
            builds = list(pool.map(lambda host: build_dispatch(host, source, sha256, script, work, revision), HOSTS))
        assert builds[0]["runtime_sha256"] == builds[1]["runtime_sha256"]
    benchmark.ROOT = PurePosixPath(work)
    benchmark.HARDWARE = benchmark.ROOT / "source/dispatch/dispatch-release/tests/hardware"
    benchmark.OUTPUT = benchmark.ROOT / "full-bs32-f50"
    benchmark.RUNTIME_INSTALL = PurePosixPath(ROOT) / "runtime-install"
    benchmark.REVISION = revision
    benchmark.ROUTE_MODE = mode
    benchmark.main(ports=ports, cleanup=True)


if __name__ == "__main__":
    main()
