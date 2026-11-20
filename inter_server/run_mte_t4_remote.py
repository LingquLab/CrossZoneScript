"""Clone pinned T4 inputs into a fresh case and execute only CPU SST."""

import base64
import gzip
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import shutil

NAME = "tilexr-cpu-b4f4660a-lr8"
ID = "1f98e70b00ed8b171fea3b06152a7fe9507a2270e9dee05c72015af5b995c2f4"
BASELINE = "t4-native-639f2fb3"
VARIANTS = {
    "8720cf344d4bcbb4b6eea2e4f1c3d01579180d8d4e483341d7f32a73707f2b98": "t4-sst-mte-state-8720cf34",
    "0d3c8694fe8919d1270c97dd6e3c4b39dc521ef9c06266ad91fe0cd8aec7d4ee": "t4-sst-mte-split-forward-0d3c8694",
    "5a6c7c8cac0b13c99d824725df3e73d91bacbeff576693b4823e2705ab50ffbd": "t4-sst-mte-scalar-forward-5a6c7c8c",
}
MOUNT = Path("/mnt/share/tilexr-sim-jobs/b4f4660a-6144-453c-99f7-339b40f56c54/inter-server-native")
old = MOUNT / BASELINE


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path, data):
    path.write_text(json.dumps(data, indent=2) + "\n")


def docker(*args, timeout=30):
    return subprocess.run(["docker", *args], capture_output=True, check=True, timeout=timeout)


def prepare(request, case, container_case):
    build = request["build"]
    binary = gzip.decompress(base64.b64decode(request["object"], validate=True))
    manifest = json.loads((old / "case-manifest.json").read_text())
    assert json.loads((old / "run.json").read_text())["status"] == "timed_out"
    assert manifest["kernel_sha256"] == "9fa520e775c4586cfe529174bef00975b49342f3ef541bcb658ecaff8273f1c2"
    assert build["candidate_commit"] == manifest["candidate_commit"]
    assert build["entry"] == manifest["entry"] == "tilexr_ep_ht_inter_server_send_kernel"
    assert hashlib.sha256(binary).hexdigest() == build["kernel_sha256"]
    case.mkdir(exist_ok=False)
    old_path = str(Path(container_case).with_name(BASELINE))
    for name, expected in manifest["files"].items():
        rel = Path(name)
        assert not rel.is_absolute() and ".." not in rel.parts
        source, target = old / rel, case / rel
        assert sha(source) == expected, name
        target.parent.mkdir(parents=True, exist_ok=True)
        if name == "kernel/inter_server_send.o":
            assert len(binary) == source.stat().st_size
            target.write_bytes(binary)
        elif name in {"workload.yml", "v310_config/workload_config.yml"}:
            text = source.read_text()
            assert old_path in text
            target.write_text(text.replace(old_path, container_case))
        elif name in {"loader-manifest.json", "blue-case-identity.json", "runtime-layout.json"}:
            continue
        else:
            shutil.copyfile(source, target)
    write_json(case / "blue-build-record.json", build)
    manifest.update(
        variant=build["variant"],
        kernel_sha256=build["kernel_sha256"],
        original_kernel_sha256=manifest["kernel_sha256"],
        files={str(p.relative_to(case)): sha(p) for p in sorted(case.rglob("*")) if p.is_file()},
    )
    write_json(case / "case-manifest.json", manifest)
    return build


def main(request):
    info, = json.loads(docker("inspect", NAME, timeout=15).stdout)
    assert info["Id"] == ID
    assert not info["State"]["Running"]
    assert info["HostConfig"]["Runtime"] == "runc" and not info["HostConfig"]["Devices"]
    variant = VARIANTS[request["build"]["kernel_sha256"]]
    tokens = request.get("tokens", 4)
    if tokens == 8:
        variant = variant.replace("t4-", "t8-", 1)
    else:
        assert tokens == 4
    case = MOUNT / variant
    container_case = "/work/inter-server-native/" + variant
    build = (
        prepare_t8(request, case, container_case)
        if tokens == 8
        else prepare(request, case, container_case)
    )
    result = {
        "case": container_case,
        "variant": build["variant"],
        "candidate_commit": build["candidate_commit"],
        "kernel_sha256": build["kernel_sha256"],
        "world": 64,
        "tokens": tokens,
        "hidden": 256,
        "compiled_in_green": False,
    }
    docker("start", NAME)
    try:
        code = 'import glob; assert not any(glob.glob(p) for p in ("/dev/davinci*", "/dev/devmm*", "/dev/hisi_hdc*"))'
        docker("exec", NAME, "python3", "-c", code, timeout=15)
        result["cpu_only"] = True
        command = [
            "python3", container_case + "/adapter/run.py", "--case", container_case,
            "--fixture", container_case + "/fixture-data",
            "--checker", container_case + "/source/tools/unitest/simulator/experiments/inter_server_send/fixture.py",
            "--environment", container_case + "/sst-environment.json",
            "--threads", "8", "--timeout", "1800",
        ]
        with (case / "native-runner.log").open("wb") as log:
            proc = subprocess.run(
                ["docker", "exec", NAME, "bash", "-c", 'set -e; source /opt/sim/cann-env.sh; exec "$@"', "bash", *command],
                stdout=log, stderr=subprocess.STDOUT, timeout=1950,
            )
        result["runner_exit_code"] = proc.returncode
    except Exception as error:
        result["runner_exception_type"] = type(error).__name__
    finally:
        try:
            if (case / "run.json").is_file():
                record = json.loads((case / "run.json").read_text())
                result["run"] = {key: record[key] for key in ("status", "returncode", "guards") if key in record}
            result["checker_log_present"] = (case / "check.log").is_file()
            result["exported_rank_count"] = len(list((case / "actual").glob("rank*")))
        finally:
            docker("stop", "--time", "10", NAME, timeout=25)
            result["container_stopped"] = True
            write_json(case / "task-result.json", result)
    print(json.dumps(result, ensure_ascii=True))


if __name__ == "__main__":
    main(json.load(sys.stdin))
