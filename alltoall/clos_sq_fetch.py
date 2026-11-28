"""Stage and run the pinned 16P Clos 4+8 SQ fetch comparison."""

import argparse
import hashlib
import json
from pathlib import Path
import re
import shlex
import subprocess
import tempfile

from lr8 import ENV, IMAGE, RESULTS, SSH, UBUS, RunFailed, make_payload, qualify, run, run_case


REVISION = "5bb17b61920c0f3238c509c2368cbdde24df03ed"
REPO = Path("D:/workspace/TileXR-worktrees/alltoall-sst-08f501a0-clos-sq-fetch-compare")
LOCK = RESULTS / "alltoall-sst-green-clos-sq-fetch-emi_57n2/source-lock"
LOCK_DIGEST = "a785bb374a9a4e267aae1fd6aa320132db31ccb1ab7623d61c6bff2fbf8484f7"
CLOS_PAIR = "urma_full_clos_4plus8"
TARGETS = {
    CLOS_PAIR: ("full_clos_4plus8", 16, 8192, "clos-4plus8"),
    "urma_full_syncall": ("full_syncall", 64, 2048, "urma_full_syncall"),
    "urma_full_clock": ("full_clock", 64, 2048, "urma_full_clock"),
    "urma_full_nosync": ("full_nosync", 64, 2048, "urma_full_nosync"),
    "urma_full_clos_8plus8": ("full_clos_8plus8", 16, 8192, "urma_full_clos_8plus8"),
    "urma_jetty_peers_prefill": ("jetty_peers_prefill", 8, 128, "urma_jetty_peers_prefill"),
    "urma_jetty_peers_progress": ("jetty_peers_progress", 8, 128, "urma_jetty_peers_progress"),
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--args-file", required=True, type=Path)
    args = json.loads(parser.parse_args().args_file.read_text(encoding="utf-8"))
    enabled = args.get("split_sq_fetch", 1)
    kernel = args.get("kernel", CLOS_PAIR)
    case, ranks, elements, build_mode = TARGETS[kernel]
    assert type(enabled) is int and enabled in (0, 1) and (enabled or kernel == CLOS_PAIR)
    side = "sim-split1" if enabled else "sim-sq-fetch0"
    name_prefix = f"clos-sq-fetch{enabled}" if kernel == CLOS_PAIR else f"retained-{case}"
    work = Path(tempfile.mkdtemp(prefix=f"alltoall-sst-{name_prefix}-", dir=RESULTS))
    state = dict(variant=side, kernel=kernel, logs=str(work), revision=REVISION, split_sq_fetch=enabled)

    def remote(argv, label, timeout=45, data=None):
        state["stage"] = label
        return run([*SSH, shlex.join(argv)], work, work / (label + ".log"), timeout, data)

    container = None
    try:
        state["stage"] = "package-sources"
        assert hashlib.sha256((LOCK / "SOURCE_LOCK.json").read_bytes()).hexdigest() == LOCK_DIGEST
        archive = make_payload(REPO, REVISION, LOCK)
        state["archive_sha256"] = hashlib.sha256(archive).hexdigest()
        if args.get("plan_only"):
            (work / "source.tar.gz").write_bytes(archive)
            state["stage"] = "sources-verified"
        else:
            prefix = f"/opt/tilexr-sst-08f501a0-{name_prefix}."
            bind = remote(["mktemp", "-d", prefix + "XXXXXXXX"], "create-bind")
            assert re.fullmatch(re.escape(prefix) + r"[A-Za-z0-9]{8}", bind)
            state["bind"] = bind
            remote(["tar", "-xzf", "-", "-C", bind], "upload-sources", 180, archive)
            name = Path(bind).name
            container = remote(["docker", "run", "-dt", "--name", name, "--runtime=ascend", "--network", "none",
                                "--ipc=shareable", "--shm-size=64m", "--ulimit", "nofile=524288:524288",
                                "--mount", f"type=bind,src={bind},dst=/work", IMAGE, "bash", "-l"], "start-container")
            observed = remote(["docker", "inspect", "--format", "{{.Id}} {{.Image}}", name], "image-identity")
            assert observed == f"{container} {IMAGE}"
            state.update(image_id=IMAGE, container_id=container)
            builder = Path(__file__).with_name("lr8-build.sh").read_bytes()
            remote(["docker", "exec", "-i", container, "bash", "-s", "--", str(enabled), build_mode],
                   "build-c310", 400, builder)
            fingerprints = remote(["docker", "exec", container, "sha256sum", "/work/source-lock/SOURCE_LOCK.json",
                                   ENV, UBUS], "fingerprints")
            source, cann, ubus = (line.split()[0] for line in fingerprints.splitlines())
            assert source == LOCK_DIGEST
            host = remote(["hostname", "-s"], "hostname")
            state["stage"] = f"{ranks}p-qualification"
            stage, config = qualify(work, side, name, container, host, (source, cann, ubus), ranks,
                                    repo=REPO, revision=REVISION, kernel=kernel, elements=elements,
                                    diagnostics=None if kernel == CLOS_PAIR else {},
                                    simulation_timeout=1200 if ranks == 64 else 600)
            identity, = (json.loads(line.removeprefix("KERNEL_VERIFIED="))
                         for line in (stage / "verify.log").read_text(encoding="utf-8").splitlines()
                         if line.startswith("KERNEL_VERIFIED="))
            assert identity["variant"] == side
            state["object"] = identity
            state["stage"] = f"{ranks}p-unitest"
            state["case"] = run_case(stage, config, ranks, repo=REPO, case=f"alltoall_{case}_sst.json")
            state["stage"] = "finished"
    except (AssertionError, OSError, ValueError, RunFailed, subprocess.SubprocessError) as error:
        state["error_class"] = type(error).__name__
        if isinstance(error, RunFailed):
            state["phase_exit"] = error.args[1]
    finally:
        if container and re.fullmatch(r"[0-9a-f]{64}", container):
            previous = state["stage"]
            try:
                state["container_removed"] = remote(["docker", "rm", "-f", container], "cleanup", 60) == container
            except (RunFailed, OSError, subprocess.SubprocessError):
                state["container_removed"] = False
            state["stage"] = previous
    (work / "handoff.json").write_text(json.dumps(state, indent=2), encoding="utf-8")
    print(json.dumps(state, separators=(",", ":")))


if __name__ == "__main__":
    main()
