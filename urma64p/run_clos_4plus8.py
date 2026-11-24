"""Run the compiled 16P Clos 4+8 objects in isolated SST containers."""

import argparse
import base64
import json
import re
import subprocess
from pathlib import Path


REMOTE = r'''
import base64
import hashlib
import json
import re
import subprocess
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

build = Path(args["build"])
origin = Path("/home/h30059441/tilexr_sim_cc9_hapi_work")
campaign = "/work/coord_runner/full-matrix-fdcf9d52-20261119"
image = "sha256:19e063d4eebab9785ad24b175ee1567dfb24c1180698f67fe0ab32a731a19cd5"
objects = json.loads((build / "objects.json").read_text())
assert objects["passed"] and objects["revision"] == args["revision"]
assert subprocess.check_output(["docker", "image", "inspect", "--format", "{{.Id}}", image],
                               text=True, timeout=15).strip() == image
work = Path(tempfile.mkdtemp(prefix="urma16p-4plus8.", dir="/opt"))
for name, blob in sources.items():
    (work / name).write_bytes(base64.b64decode(blob, validate=True))
print(json.dumps(dict(stage="campaign_start", work=str(work), size=args["size"], revision=args["revision"])), flush=True)


def command(argv, log, timeout):
    with log.open("wb") as out:
        return subprocess.run(argv, stdout=out, stderr=subprocess.STDOUT, timeout=timeout).returncode


def diagnostics(log):
    content = log.read_text(errors="replace")[-16000:]
    lines = [line.strip()[:230] for line in content.splitlines()
             if re.search(r"(?:Error:|error:|returned non-zero|could not|not found|mismatch|^FAILED)", line)]
    return lines[-5:]


common = ["docker", "run", "--rm", "--network", "none",
          "-v", f"{build}:/candidate:ro", "-v", f"{origin}:/work:ro"]
compiler = common + ["-v", f"{work}:/output", "--entrypoint", "g++", image,
           "-std=c++14", "-O2", "-DTILEXR_JETTY_PEERS_CLOS_4PLUS8=1",
           "-I/candidate/src/include", "-I/candidate/src/collectives/kernels",
           "/output/prepare_16p_images.cpp", "-o", "/output/prepare_images"]
compile_log = work / "prepare-build.log"
code = command(compiler, compile_log, 60)
if code:
    print(json.dumps(dict(stage="preparer_compile", exit=code, errors=diagnostics(compile_log))), flush=True)
    raise SystemExit(1)


def run_case(kind):
    kernel = f"clos-4plus8/{kind}/tilexr_alltoall_urma_jetty_peers_full_kernel.o"
    assert hashlib.sha256((build / kernel).read_bytes()).hexdigest() == objects["objects"][kernel]
    reference = f"{campaign}/reference-16p-{args['size']}-{kind}"
    leaf = work / kind
    leaf.mkdir()
    container_case = "/case-root/case"
    docker = common + ["--runtime", "ascend", "--ipc", "shareable", "--shm-size", "64m",
            "--ulimit", "nofile=524288:524288", "-e", f"PYTHONPATH={campaign}/generator",
            "-v", f"{work}:/output:ro", "-v", f"{leaf}:/case-root", "--entrypoint", "python3", image, "-Bu"]
    prepare_log = leaf / "prepare.log"
    code = command(docker + ["/output/prepare_clos_4plus8.py", reference, container_case,
                            f"/candidate/{kernel}", args["revision"], kind], prepare_log, 180)
    if code:
        return dict(stage="prepare", kind=kind, exit=code, errors=diagnostics(prepare_log))
    prepare_rows = prepare_log.read_text().splitlines()
    prep = next(json.loads(line) for line in reversed(prepare_rows) if '"stage": "prepared"' in line)
    print(json.dumps(dict(prep, kind=kind)), flush=True)
    log = leaf / "sst.log"
    start = time.monotonic()
    code = command(docker + [f"{container_case}/case_tools.py", "run", "--case-dir", container_case,
                            "--threads", "32", "--timeout", str(args["timeout"])], log, args["timeout"] + 90)
    row = dict(stage="sst", kind=kind, exit=code, wall_seconds=round(time.monotonic() - start, 3))
    case = leaf / "case"
    reports = sorted(case.glob("runs/*/run.json"))
    if reports:
        report = json.loads(reports[-1].read_text())
        row.update(status=report.get("status"), validation=report.get("validation"), error=report.get("error"))
    if code:
        row["errors"] = diagnostics(log)
    elif row.get("status") == "simulation_outputs_verified":
        sim_log = sorted(case.glob("runs/*/sim.log"))[-1]
        with sim_log.open("rb") as stream:
            stream.seek(max(0, sim_log.stat().st_size - 65536))
            match = re.findall(rb"simulated time:\s*(\d+(?:\.\d+)?)\s*(ps|ns|us|ms|s)\b", stream.read(), re.I)
        assert match
        amount, unit = match[-1]
        row["simulated_us"] = float(amount) * {b"ps": 1e-6, b"ns": 1e-3, b"us": 1, b"ms": 1000, b"s": 1e6}[unit.lower()]
    else:
        row["exit"] = 1
    return row


with ThreadPoolExecutor(max_workers=len(args["modes"])) as pool:
    results = list(pool.map(run_case, args["modes"]))
for result in results:
    print(json.dumps(result), flush=True)
'''


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--args-file", type=Path, required=True)
    args = json.loads(parser.parse_args().args_file.read_text())
    if not re.fullmatch(r"/opt/urma64p-full\.[A-Za-z0-9]{6}", args["build"]):
        parser.error("invalid build path")
    if not re.fullmatch("[0-9a-f]{40}", args["revision"]):
        parser.error("invalid source revision")
    if args["size"] not in ("512kib-per-rank", "4mib-per-rank", "16mib-per-rank"):
        parser.error("unsupported seed")
    if (not args["modes"] or len(args["modes"]) != len(set(args["modes"])) or
            set(args["modes"]) - {"functional", "performance"}):
        parser.error("invalid modes")
    if not 60 <= args["timeout"] <= 9000:
        parser.error("invalid SST timeout")
    root = Path(__file__).resolve().parent
    sources = {name: base64.b64encode((root / name).read_bytes()).decode("ascii")
               for name in ("prepare_16p_images.cpp", "prepare_clos_4plus8.py")}
    ssh = ["ssh", "-T", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
           "-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=3",
           "-o", "IdentitiesOnly=yes", "-i", str(Path.home() / ".ssh/id_ed25519_server"),
           "root@141.61.94.30", "python3 -u -"]
    with Path("urma16p-ssh.stderr.log").open("wb") as errors:
        program = f"args = {args!r}\nsources = {sources!r}\n" + REMOTE
        remote = subprocess.run(ssh, input=program.encode(), stderr=errors, timeout=args["timeout"] + 400)
    raise SystemExit(remote.returncode)


if __name__ == "__main__":
    main()
