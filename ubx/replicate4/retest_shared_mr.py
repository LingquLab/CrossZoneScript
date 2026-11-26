"""Build the isolated TileXR runtime in green deployment and rerun EP32."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import tempfile

import benchmark
from build import HOSTS, ROOT, SSH, TILEXR, archive, pin, remote

RUNTIME_REVISION = "afe52e3932f7d95b3396d9590b1ae7d9be5e0c2e"
WORK = ROOT + "/runtime-shared-mr"


def build_runtime(host, source, sha256, script):
    peer = "root@" + host
    remote(host, SSH, peer, f"mkdir {WORK}")
    remote(host, SSH.with_name("scp.exe"), str(source), f"{peer}:{WORK}/source.tar.gz", timeout=150)
    text = benchmark.ssh(host, f"bash -s -- {sha256} {RUNTIME_REVISION}",
                         payload=script, timeout=500, stage="shared MR runtime build")
    receipt = json.loads(text)
    assert receipt["revision"] == RUNTIME_REVISION
    return receipt


def main():
    script = Path(__file__).with_name("build_shared_mr.sh").read_bytes().replace(b"\r\n", b"\n")
    with tempfile.TemporaryDirectory(prefix="ubx-shared-mr-", dir=Path.cwd()) as workspace:
        work = Path(workspace).resolve()
        repo = work / "tilexr"
        pin(repo, TILEXR, (RUNTIME_REVISION,))
        source = archive(work, repo, "shared-mr-runtime", RUNTIME_REVISION)
        sha256 = hashlib.sha256(source.read_bytes()).hexdigest()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda host: build_runtime(host, source, sha256, script), HOSTS))
        assert all(result["runtime_sha256"] for result in results)
    benchmark.OUTPUT = benchmark.ROOT / "full-bs32-f50-shared-mr"
    benchmark.RUNTIME_INSTALL = benchmark.ROOT / "runtime-shared-mr" / "install"
    benchmark.RUNTIME_REVISION = RUNTIME_REVISION
    benchmark.main(ports=(24500, 26000, 28000), cleanup=True)


if __name__ == "__main__":
    main()
