"""Qualify and run pinned LR8 cases using the checked-in UniTest entry."""
import argparse
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import tempfile


parser = argparse.ArgumentParser()
parser.add_argument("--args-file", type=Path, required=True)
args = json.loads(parser.parse_args().args_file.read_text())
revision = args["revision"]
matrix = args.get("full_cases", [])
staged_cases = {"jetty_peers", "jetty_peers_prefill", "jetty_peers_progress"}
for value in matrix:
    if value not in staged_cases | {"clos_4plus8", "clos_8plus8", "syncall", "clock", "nosync"}:
        raise SystemExit("invalid Full variant")
if not re.fullmatch(r"[a-f0-9]{40}", revision):
    raise SystemExit("invalid TileXR revision")
image = "sha256:19e063d4eebab9785ad24b175ee1567dfb24c1180698f67fe0ab32a731a19cd5"
input_revision = "285ac4e9e87048de2add81091f15fdbb20605aa7"
work = Path(tempfile.mkdtemp(prefix="urma-unitest-regression-"))
repo = work / "source"
key = Path.home() / ".ssh/id_ed25519_server"
host = "141.61.94.30"
ssh = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15", "-i", str(key), "root@" + host]
env = dict(os.environ)
env.setdefault("GIT_TERMINAL_PROMPT", "0")
bind = None
stage = "initialization"


def run(label, cmd, cwd=None, timeout=180, data=None):
    global stage
    stage = label
    record = subprocess.run(cmd, cwd=cwd, env=env, input=data, timeout=timeout, capture_output=True)
    (work / (label + ".stdout.log")).write_bytes(record.stdout)
    (work / (label + ".stderr.log")).write_bytes(record.stderr)
    record.check_returncode()
    return record.stdout


def remote(label, cmd, timeout=180, data=None):
    return run(label, [*ssh, cmd], timeout=timeout, data=data).decode().strip()


def save(path, data):
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


try:
    run("init", ["git", "init", str(repo)])
    run("fetch", ["git", "fetch", "--no-tags", "https://github.com/LingquLab/TileXR.git", revision,
                  input_revision], cwd=repo)
    run("checkout", ["git", "checkout", "--detach", revision], cwd=repo)
    assert run("revision", ["git", "rev-parse", "HEAD"], cwd=repo).decode().strip() == revision
    run("submodules", ["git", "submodule", "update", "--init", "--", "3rdparty/spdlog", "3rdparty/picojson"],
        cwd=repo, timeout=300)
    source_lock = work / "source-lock"
    lock_sha = run("source-lock", [sys.executable, "-B", "tools/collectives/sst/stage.py",
                                  str(repo), revision, str(source_lock)], cwd=repo).decode().strip()
    bind = remote("bind", "mktemp -d /opt/tilexr-urma-unitest.XXXXXXXX")
    assert re.fullmatch(r"/opt/tilexr-urma-unitest\.[A-Za-z0-9]{8}", bind)
    name = "tilexr-urma-unitest-" + bind.rsplit(".", 1)[1]
    print(json.dumps(dict(stage="staged", revision=revision, bind=bind)), flush=True)
    remote("transfer", f"tar -xf - -C {shlex.quote(bind)}", data=run(
        "archive", ["tar", "-cf", "-", "-C", str(work), "source-lock"],
        timeout=120))
    remote("source-root", f"mkdir {shlex.quote(bind + '/source')}")
    remote("transfer-source", f"tar -xf - -C {shlex.quote(bind + '/source')}",
           data=run("source-archive", ["git", "archive", revision], cwd=repo))
    for dep in ("spdlog", "picojson"):
        submodule = "3rdparty/" + dep
        expected = run(dep + "-pointer", ["git", "rev-parse", "HEAD:" + submodule], cwd=repo).decode().strip()
        assert run(dep + "-revision", ["git", "rev-parse", "HEAD"], cwd=repo / submodule).decode().strip() == expected
        target = shlex.quote(bind + "/source/" + submodule)
        remote(dep + "-stage", f"mkdir -p {target} && tar -xf - -C {target}",
               data=run(dep + "-archive", ["git", "archive", expected], cwd=repo / submodule))
    build = Path(__file__).with_name("urma_build.sh").read_bytes()
    targets = ([f"tilexr_alltoall_urma_{value if value in staged_cases else 'full_' + value}_kernel_sst" for value in matrix]
               if matrix else ["tilexr_alltoall_urma_lr8_kernel_sst"])
    remote("build", shlex.join(["bash", "-s", "--", bind, name, image, *targets]), timeout=900, data=build)
    container = remote("identity", f"cat {shlex.quote(bind + '/container-id.txt')}")
    hostname = remote("hostname", "hostname -s")
    cann, ubus = "/opt/sim/cann-env.sh", "/opt/sim/plugins/libUBusSimulator.so"
    hashes = remote("hashes", shlex.join(["docker", "exec", container, "sha256sum",
                                          "/work/source-lock/SOURCE_LOCK.json", cann, ubus]))
    lock, cann_sha, ubus_sha = [line.split()[0] for line in hashes.splitlines()]
    assert lock == lock_sha
    # tssh uses the system ssh binary. Pass the already authorized key through a
    # Python startup hook scoped to this controller and its child CLI processes.
    shim = work / "controller-ssh"
    shim.mkdir()
    (shim / "sitecustomize.py").write_text(
        "import os, subprocess\n"
        "_run = subprocess.run\n"
        "def run(args, *positional, **keywords):\n"
        "    if isinstance(args, list) and args and args[0] in ('ssh.exe', 'ssh'):\n"
        "        args = [args[0], '-i', os.environ['TILEXR_SST_SSH_KEY'], *args[1:]]\n"
        "    return _run(args, *positional, **keywords)\n"
        "subprocess.run = run\n", encoding="utf-8")
    env["PYTHONPATH"] = str(shim)
    env["TILEXR_SST_SSH_KEY"] = str(key)
    inventory = work / "inventory.json"
    save(inventory, dict(version=1, clusters=dict(sst=dict(user="root", hosts=[dict(name="host", address=host)]))))
    request = dict(cluster="sst", host="host", hostname=hostname, container=name,
                   container_id=container, image_id=image, sources="/work/source-lock", output="/work/lr8-qualified",
                   runtime_commit=revision, source_lock_sha256=lock, cann_root="/opt/sim/Ascend/cann-9.1.0",
                   driver_root="/usr/local/Ascend/driver", cann_env=cann, cann_env_sha256=cann_sha,
                   ubus_library=ubus, ubus_sha256=ubus_sha, kernel_name="urma_lr8", stage_profiling=0,
                   sim_variant="sim-split1")
    cases = ([(value, None) for value in matrix] if matrix else
             [("no_credit", 0), ("credit", 2**32-1), ("clock", 35863)])
    for mode, period in cases:
        kernel = f"urma_{mode}" if mode in staged_cases else f"urma_full_{mode}" if matrix else "urma_lr8"
        ranks = 8 if mode in staged_cases else 16 if mode.startswith("clos_") else 64 if matrix else 32
        elements = {8: 128, 16: 8192, 32: 128, 64: 2048}[ranks]
        qualification_name = kernel
        qualification = work / qualification_name
        if not qualification.exists():
            qualification.mkdir()
            request.update(kernel_name=kernel, output="/work/" + kernel + "-qualified")
            common = ["--inventory", str(inventory), "--rank-size", str(ranks), "--elements-per-peer", str(elements)]
            for field, value in request.items():
                common += ["--" + field.replace("_", "-"), str(value)]
            for phase, state, options in (
                    ("verify", "verified_not_simulated", ["--cmake-build-dir", "/work/build",
                                                         "--cmake-build-log", "/work/receipts/build.log"]),
                    ("abi", "host_abi_verified_not_simulated", []),
                    ("inspect", "resources_verified_not_simulated", ["--library-dir", "/opt/sim/plugins",
                                                                      "--inspection-output", "/work/" + kernel + "-environment"]),
            ):
                destination = qualification / phase
                run(kernel + "-" + phase, [sys.executable, "-B", "scripts/run_alltoall_sim_kernel.py",
                    phase, *common, *options, "--local-output", str(destination)], cwd=repo, timeout=1200)
                assert json.loads((destination / "result.json").read_text())["state"] == state
        case_request = dict(request, environment_report="/work/" + kernel + "-environment", kernel_variant=kernel)
        request_file = qualification / "request.json"
        save(request_file, case_request)
        config = work / (mode + ".json")
        output = work / mode
        save(config, dict(schema="tilexr.unitest.sst.v1", rank_size=ranks, threads=ranks, timeout=1500,
                          simulation_timeout=1200, tool_checkout=str(repo), tool_commit=revision, inventory=str(inventory),
                          request_file=str(request_file), suite_output="/work/" + mode + "-suite",
                          build_report=str(qualification / "verify"), abi_report=str(qualification / "abi")))
        if mode in staged_cases:
            case = f"alltoall_{mode}_sst.json"
        elif matrix:
            case = f"alltoall_full_{mode}_sst.json"
        else:
            case = f"alltoall_lr8_{mode}_32p_sst.json"
        run(mode, [sys.executable, "-B", "-m", "tools.unitest.controller", "--case",
                   "tools/unitest/cases/" + case, "--target", "sst", "--mode", "single", "--no-profiling",
                   "--sim-config", str(config), "--output-dir", str(output)], cwd=repo, timeout=1800)
        report = json.loads((output / "result.json").read_text())
        assert report["status"] == "passed" and list(report["phases"]) == ["prepare", "run"]
        result = report["tool_result"]["sst_result"]
        if not matrix:
            assert result["lr8_group_period_cycles"] == period
        assert result["validation"]["rank_count"] == ranks
        assert result["runner_returncode"] == 0 and not result["remaining_processes"]
        print(json.dumps(dict(stage="verified", revision=revision, bind=bind, mode=mode, period=period,
                              ranks=ranks, sha256=result["kernel"]["sha256"], entry=result["kernel"]["entry"],
                              simulated_us=result["simulated_us"])), flush=True)
except Exception as error:
    (work / "failure.log").write_text(repr(error), encoding="utf-8")
    print(json.dumps(dict(stage=stage, bind=bind, controller_logs=str(work), error_type=type(error).__name__)), flush=True)
    raise
finally:
    if bind is not None:
        command = f"if test -s {shlex.quote(bind + '/container-id.txt')}; then docker rm -f {shlex.quote(name)}; fi"
        remote("cleanup", command)
        print(json.dumps(dict(stage="cleanup", bind=bind, container_removed=True)), flush=True)
