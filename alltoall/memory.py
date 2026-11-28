"""Stage pinned Memory AllToAll sources and run their existing SST acceptance."""

import argparse
import json
from pathlib import Path
import re
import shlex
import subprocess
import sys
import tempfile


REVISIONS = {
    "baseline": "6f0ae50e5116b7dc0b1917f6b93992756187cdcb",
    "candidate": "6224be01a0f057e82987a8bf3ef842b0898a9f78",
    "diagnostic-baseline": "e4473930dc11a4314a165a817fc3ff7f824aab35",
    "diagnostic-candidate": "a519d3560722e877573fcc228a760e3f70c8605b",
}
CASES = (
    "memory_ordered", "memory_direct_multi_peer", "memory_direct_schedule_local_cross",
)
IMAGE = "sha256:19e063d4eebab9785ad24b175ee1567dfb24c1180698f67fe0ab32a731a19cd5"
WORKTREES = Path("D:/workspace/TileXR-worktrees")


def case_inputs(repo, cases, diagnostic):
    if (not isinstance(cases, (list, tuple)) or not cases
            or not all(isinstance(case, str) for case in cases) or len(cases) != len(set(cases))):
        raise ValueError("Expected distinct Memory SST cases")
    if diagnostic and tuple(cases) != ("memory_ordered",):
        raise ValueError("Status injection is qualified only for Ordered")
    ranks = {}
    for case in cases:
        if not re.fullmatch(r"memory_[a-z0-9_]+", case):
            raise ValueError("Invalid Memory SST case")
        config = json.loads((repo / "tools/unitest/cases" / f"alltoall_{case}_sst.json").read_text(encoding="utf-8"))
        if config["variant"] != case or config["world_size"] not in {8, 32, 64}:
            raise ValueError("Unexpected Memory SST case contract")
        ranks[case] = config["world_size"]
    return ranks


def execute(argv, log, cwd, timeout, data=None):
    with log.open("wb") as stream:
        result = subprocess.run(argv, cwd=cwd, timeout=timeout, input=data,
                                stdout=stream, stderr=subprocess.STDOUT)
    return result.returncode


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--args-file", type=Path, required=True)
    args = json.loads(parser.parse_args().args_file.read_text(encoding="utf-8"))
    side = args["side"]
    revision = REVISIONS[side]
    diagnostic = side.startswith("diagnostic-")
    cases = args.get("cases", ("memory_ordered",) if diagnostic else CASES)
    target = args.get("ssh_target", "root@141.61.94.30")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.@-]*", target):
        raise ValueError("Invalid SST SSH destination")
    ssh = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15",
           "-o", "ServerAliveInterval=20", "-o", "ServerAliveCountMax=3", target]
    results = Path("D:/workspace/TileXR-build-results")
    results.mkdir(exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix=f"alltoall-sst-memory-{side}-", dir=results))
    state = dict(side=side, revision=revision, image=IMAGE, local_results=str(work))
    repo = WORKTREES / f"alltoall-sst-08f501a0-memory-{side}"

    def remote(command, log, timeout, data=None):
        return execute(ssh + [shlex.join(command)], work / log, work, timeout, data)

    def step(name, status):
        state.update(stage=name, exit_code=status)
        (work / "handoff.json").write_text(json.dumps(state, indent=2), encoding="utf-8")
        if status:
            raise RuntimeError(name)

    try:
        revision_check = work / "revision.log"
        step("worktree-revision", execute(["git", "rev-parse", "--show-toplevel", "HEAD"], revision_check, repo, 20))
        top, head = revision_check.read_text(encoding="utf-8").splitlines()
        if Path(top).resolve() != repo.resolve() or head != revision:
            raise ValueError("Unexpected green worktree or revision")
        status_log = work / "worktree-status.log"
        step("worktree-status", execute(["git", "status", "--porcelain"], status_log, repo, 20))
        if status_log.stat().st_size:
            raise ValueError("Green worktree is dirty")
        state["worktree"] = str(repo)
        ranks = case_inputs(repo, cases, diagnostic)
        state["case_ranks"] = ranks
        # The acceptance wrapper runs one case at a time in each container.
        acceptance_timeout = 400 + sum(1020 if value == 64 else 900 for value in ranks.values())
        state["acceptance_timeout"] = acceptance_timeout
        if args.get("plan_only"):
            step("cases-verified", 0)
            print(json.dumps(state, separators=(",", ":")))
            return 0
        source = work / "staged"
        step("source-lock", execute([sys.executable, "-B", "tools/collectives/memory_sst/memory_sst.py",
                                     "stage", "--revision", revision, "--output", str(source)],
                                    work / "source-lock.log", repo, 90))
        step("image-preflight", remote(
            ["docker", "image", "inspect", "--format", "{{.Id}} {{.Architecture}}", IMAGE], "image.log", 30))
        if IMAGE + " arm64" not in (work / "image.log").read_text(encoding="utf-8").splitlines():
            raise ValueError("Unexpected SST image identity")
        step("create-bind", remote(["mktemp", "-d", f"/opt/tilexr-sst-08f501a0-{side}.XXXXXXXX"],
                                   "bind.log", 30))
        bind = (work / "bind.log").read_text(encoding="utf-8").splitlines()[-1]
        if not re.fullmatch(r"/opt/tilexr-sst-08f501a0-" + side + r"\.[A-Za-z0-9]{8}", bind):
            raise ValueError("Unexpected SST bind path")
        state["bind"] = bind
        step("transfer-source", remote(["tar", "-xf", "-", "-C", bind], "transfer-source.log",
                                       180, (source / "source.tar").read_bytes()))
        with (source / "source-lock.json").open("rb") as lock:
            lock_command = ssh + ["cat > " + shlex.quote(bind + "/source-lock.json")]
            step("transfer-lock", execute(lock_command, work / "transfer-lock.log", work, 30, lock.read()))
        command = ["bash", bind + "/source/tools/collectives/memory_sst/accept-host.sh",
                   bind, Path(bind).name, IMAGE, *cases]
        state["acceptance_argv"] = command
        step("before-acceptance", 0)
        status = remote(command, "acceptance.log", acceptance_timeout)
        state["acceptance_exit"] = status
        step("after-acceptance", 0)
        summary = Path(__file__).with_name("memory_summary.py").read_bytes()
        summary_log = work / "result.log"
        step("summarize", remote(["python3", "-B", "-", bind, revision, str(status), side, *cases],
                                 summary_log.name, 60, summary))
        state["result"] = json.loads(summary_log.read_text(encoding="utf-8").splitlines()[-1])
        if diagnostic:
            step("acceptance", int(not state["result"].get("expected_failure_verified")))
        else:
            step("acceptance", status)
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as error:
        state["error_class"] = type(error).__name__
        print(json.dumps(state))
        return 1
    print(json.dumps(state))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
