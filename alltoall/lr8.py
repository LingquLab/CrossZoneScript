"""Build and run the pinned LR8 split-write comparison on the approved SST host."""

import argparse
import io
import json
import re
import shlex
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

from memory import IMAGE


REVISION = "163fa57f8f8363d774096b57a5c17aa36d11cb93"
REPO = Path("D:/workspace/TileXR-worktrees/alltoall-sst-08f501a0-lr8-split-compare")
LOCK = Path("D:/workspace/TileXR-build-results/alltoall-sst-green-lr8-8n87jpmq/source-lock")
RESULTS = Path("D:/workspace/TileXR-build-results")
QUEUE_REVISION = "ce4456229720bdc01a2e744ed3c31573504860e8"
QUEUE_REPO = REPO.with_name("alltoall-sst-08f501a0-lr8-queue-compare")
QUEUE_LOCK = RESULTS / "alltoall-sst-green-lr8-queue-d3wmmyue/source-lock"
TOPOLOGY_CASES = {
    "urma_cw": ("cw", "cw_16p"),
    "urma_lr8_p16_s16_local7": ("local7_16_16", "local7_16_16_16p"),
    "urma_lr8_p16_s8_local7": ("local7_16_8", "local7_16_8_16p"),
}
SSH = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15", "green-sst-30"]
CANN = "/opt/sim/Ascend/cann-9.1.0"
ENV = "/opt/sim/cann-env.sh"
UBUS = "/opt/sim/plugins/libUBusSimulator.so"


class RunFailed(RuntimeError):
    """A named validation phase failed with details retained in its log."""


def run(argv, cwd, log, timeout, data=None):
    result = subprocess.run(argv, cwd=cwd, input=data, capture_output=True, timeout=timeout)
    log.write_bytes(result.stdout + result.stderr)
    if result.returncode:
        raise RunFailed(log.stem, result.returncode)
    return result.stdout.decode("utf-8", "replace").strip()


def git_output(args, cwd=REPO):
    return subprocess.check_output(["git", *args], cwd=cwd, timeout=45)


def make_payload(repo=REPO, revision=REVISION, lock=LOCK):
    assert git_output(["rev-parse", "HEAD"], repo).decode().strip() == revision
    assert not git_output(["status", "--porcelain"], repo).strip()
    source_lock = json.loads((lock / "SOURCE_LOCK.json").read_text(encoding="utf-8"))
    assert source_lock["runtime_commit"] == revision
    backups = [(repo, revision, "source/")]
    for submodule in ("spdlog", "picojson"):
        path = "3rdparty/" + submodule
        ref = git_output(["rev-parse", "HEAD:" + path], repo).decode().strip()
        assert git_output(["rev-parse", "HEAD"], repo / path).decode().strip() == ref
        backups.append((repo / path, ref, "source/" + path + "/"))
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w:gz") as archive:
        for repo, commit, prefix in backups:
            raw = git_output(["archive", "--prefix=" + prefix, commit], repo)
            with tarfile.open(fileobj=io.BytesIO(raw), mode="r:") as source:
                for member in source:
                    archive.addfile(member, source.extractfile(member) if member.isfile() else None)
        archive.add(lock, arcname="source-lock")
    return output.getvalue()


def qualify(work, side, name, container_id, hostname, fingerprints, ranks, *, repo=REPO,
            revision=REVISION, kernel="urma_lr8", elements=129, diagnostics=None):
    stage = work / f"{ranks}p"
    stage.mkdir()
    inventory = repo / "tools/tssh/clusters.json"
    common = {
        "inventory": str(inventory), "cluster": "c01-b03", "host": "C01-B03-5",
        "hostname": hostname, "container": name, "container_id": container_id,
        "image_id": IMAGE, "sources": "/work/source-lock", "output": f"/work/{ranks}p-qualified",
        "runtime_commit": revision, "source_lock_sha256": fingerprints[0],
        "cann_root": CANN, "driver_root": "/usr/local/Ascend/driver", "cann_env": ENV,
        "cann_env_sha256": fingerprints[1], "ubus_library": UBUS, "ubus_sha256": fingerprints[2],
        "sim_variant": side, "kernel_name": kernel, "stage_profiling": "0",
        "rank_size": str(ranks), "elements_per_peer": str(elements),
    }
    environment = f"/work/{ranks}p-environment"
    cli = [sys.executable, "-B", "scripts/run_alltoall_sim_kernel.py"]
    for phase, extras in (
        ("verify", ["--cmake-build-dir", "/work/build", "--cmake-build-log", "/work/receipts/build.log"]),
        ("abi", []),
        ("inspect", ["--library-dir", "/opt/sim/plugins", "--inspection-output", environment]),
    ):
        options = [piece for key, value in common.items() for piece in ("--" + key.replace("_", "-"), value)]
        argv = [*cli, phase, *options, *extras, "--local-output", str(stage / (phase + "-report"))]
        run(argv, repo, stage / (phase + ".log"), 180)

    request = json.loads((stage / "verify-report/request.json").read_text(encoding="utf-8"))
    request["environment_report"] = environment
    request_path = stage / "current-request.json"
    request_path.write_text(json.dumps(request), encoding="utf-8")
    if diagnostics is None:
        diagnostics = {"queue_state_dumps": True} if kernel == "urma_lr8" else {"instruction_dump_chips": [0]}
    config = {
        "schema": "tilexr.unitest.sst.v1", "tool_checkout": str(repo), "tool_commit": revision,
        "build_report": str(stage / "verify-report"), "abi_report": str(stage / "abi-report"),
        "request_file": str(request_path), "inventory": str(inventory),
        "suite_output": f"/work/{ranks}p-suite", "rank_size": ranks, "threads": 32,
        "simulation_timeout": 600, "timeout": 900,
        "diagnostics": diagnostics,
    }
    config_path = stage / "sst.json"
    config_path.write_text(json.dumps(config), encoding="utf-8")
    return stage, config_path


def run_case(stage, config, ranks, *, repo=REPO, case=None):
    controller = repo / "tools/unitest"
    case = case or f"alltoall_lr8_{ranks}p_516b_sst.json"
    argv = [sys.executable, "-B", "-m", "controller", "--case", f"cases/{case}",
            "--mode", "single", "--target", "sst", "--sim-config", str(config),
            "--no-profiling", "--output-dir", str(stage / "unitest")]
    code = 0
    try:
        run(argv, controller, stage / "controller.log", 1100)
    except RunFailed as error:
        code = error.args[1]
    report_path = stage / "unitest" / "result.json"
    report = json.loads(report_path.read_text(encoding="utf-8")) if report_path.is_file() else {}
    return {"rank_count": ranks, "controller_exit": code, "controller_status": report.get("status"),
            "phases": {key: state.get("status") for key, state in report.get("phases", {}).items()}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--args-file", type=Path, required=True)
    args = json.loads(parser.parse_args().args_file.read_text(encoding="utf-8"))
    side = args["side"]
    queue_comparison = args.get("queue_comparison", False)
    kernel = args.get("kernel", "urma_lr8")
    topology = TOPOLOGY_CASES.get(kernel)
    if topology:
        assert not queue_comparison
        repo, revision, lock = REPO, REVISION, LOCK
        build_recipe = {"sim-split1": ("1", kernel)}
        name_prefix = f"topology-{kernel}"
    elif queue_comparison:
        assert kernel == "urma_lr8"
        repo, revision, lock = QUEUE_REPO, QUEUE_REVISION, QUEUE_LOCK
        build_recipe = {"sim-split1": ("1", "lr8"), "sim-native-queue": ("0", "lr8-queue-state"),
                        "sim-cq-gm": ("1", "lr8-cq-gm")}
        name_prefix = f"lr8-queue-{side}"
    else:
        assert kernel == "urma_lr8"
        repo, revision, lock = REPO, REVISION, LOCK
        build_recipe = {"sim-split0": ("0", "lr8"), "sim-split1": ("1", "lr8")}
        name_prefix = f"lr8-{side}"
    assert side in build_recipe
    work = Path(tempfile.mkdtemp(prefix=f"alltoall-sst-{name_prefix}-", dir=RESULTS))
    state = {"variant": side, "logs": str(work), "revision": revision, "cases": []}

    def remote(args, label, timeout=45, data=None):
        state["stage"] = label
        return run([*SSH, shlex.join(args)], work, work / (label + ".log"), timeout, data)

    container_id = None
    try:
        state["stage"] = "verify-worktree-and-package"
        payload = make_payload(repo, revision, lock)
        prefix = f"/opt/tilexr-sst-08f501a0-{name_prefix}."
        bind = remote(["mktemp", "-d", prefix + "XXXXXXXX"], "create-bind")
        assert re.fullmatch(re.escape(prefix) + r"[A-Za-z0-9]{8}", bind)
        state["bind"] = bind
        remote(["tar", "-xzf", "-", "-C", bind], "upload-sources", 180, payload)
        name = Path(bind).name
        container_id = remote(["docker", "run", "-dt", "--name", name, "--runtime=ascend", "--network", "none",
                               "--ipc=shareable", "--shm-size=64m", "--ulimit", "nofile=524288:524288",
                               "--mount", f"type=bind,src={bind},dst=/work", IMAGE, "bash", "-l"], "start-container")
        observed = remote(["docker", "inspect", "--format", "{{.Id}} {{.Image}}", name], "image-identity")
        assert observed == f"{container_id} {IMAGE}"
        state["image_id"] = IMAGE
        state["container_id"] = container_id
        builder = Path(__file__).with_name("lr8-build.sh").read_bytes()
        remote(["docker", "exec", "-i", container_id, "bash", "-s", "--", *build_recipe[side]],
               "build-c310", 400, builder)
        fingerprint = remote(["docker", "exec", container_id, "sha256sum", "/work/source-lock/SOURCE_LOCK.json",
                              ENV, UBUS], "fingerprints")
        hashes = [line.split()[0] for line in fingerprint.splitlines()]
        assert len(hashes) == 3 and all(re.fullmatch("[0-9a-f]{64}", digest) for digest in hashes)
        hostname = remote(["hostname", "-s"], "hostname")
        for index, ranks in enumerate((8, 16)):
            state["stage"] = f"{ranks}p-qualification"
            stage, config = qualify(work, side, name, container_id, hostname, hashes, ranks,
                                    repo=repo, revision=revision, kernel=kernel, elements=128 if topology else 129,
                                    diagnostics={} if topology else None)
            state["stage"] = f"{ranks}p-unitest"
            case = f"alltoall_{topology[index]}_sst.json" if topology else None
            receipt = run_case(stage, config, ranks, repo=repo, case=case)
            evidence_script = Path(__file__).with_name("lr8_summary.py").read_bytes()
            evidence = remote(["docker", "exec", "-i", container_id, "python3", "-B", "-", "/work", str(ranks)],
                              f"{ranks}p-summary", 80, evidence_script)
            receipt["remote"] = json.loads(evidence)
            state["cases"].append(receipt)
        state["stage"] = "finished"
    except (RunFailed, OSError, ValueError, AssertionError, subprocess.SubprocessError) as error:
        state["error_class"] = type(error).__name__
        if isinstance(error, RunFailed):
            state["phase_exit"] = error.args[1]
    finally:
        if container_id and re.fullmatch("[0-9a-f]{64}", container_id):
            prior_stage = state["stage"]
            try:
                removed = remote(["docker", "rm", "-f", container_id], "cleanup", 60)
                state["container_removed"] = removed == container_id
            except (RunFailed, subprocess.SubprocessError, OSError):
                state["container_removed"] = False
            state["stage"] = prior_stage
    (work / "handoff.json").write_text(json.dumps(state, indent=2), encoding="utf-8")
    print(json.dumps(state, separators=(",", ":")))


if __name__ == "__main__":
    main()

