"""Build the pinned multi-jetty branch and run a single-host BF16 regression."""
import json
import os
from pathlib import Path
import re
import subprocess
import time

REVISION = "4d37ae67cf2f6baa06b3efbe65c71649a6f24203"
os.environ["TILEXR_VALIDATION_RUN_LABEL"] = "ht-4d37ae67-multi-jetty-ep8"
import remote
from matrix import SHELL, compile_fixture, run_group, timed_module

ROOT = remote.root
SOURCE = Path(__file__).resolve().parent
REPO = SOURCE / "source"
BUILD = ROOT / "build"
COMM = BUILD / "src/comm"
EP = BUILD / "src/ep/combine_ht"
RANKS = list(range(8))
SUMMARY = {"revision": REVISION, "log_dir": str(ROOT)}


def config(env, six, two, direct=0):
    return dict(env, TILEXR_UDMA_SHARED_QP_COUNT=str(six + two + direct),
                TILEXR_UDMA_SHARED_QP_SIX_PORT_COUNT=str(six))


def step(name, args, timeout):
    if not remote.command(name, SHELL + args, timeout):
        SUMMARY.update(stage=name, failure=remote.result.get("failure_kind"),
                       first_errors=remote.result.get("first_errors", []))
        return False
    return True


def run():
    if (SOURCE / "revision.txt").read_text().strip() != REVISION:
        raise ValueError("source revision mismatch")
    driver = ROOT / "driver-link"
    (driver / "lib64").mkdir(parents=True)
    (driver / "lib64/driver").symlink_to("/usr/local/Ascend/driver/lib64/driver", target_is_directory=True)
    flags = ["-DCMAKE_BUILD_TYPE=Release", "-DBUILD_TESTING=ON", "-DTILEXR_BUILD_COMBINE_HT=ON",
             "-DTILEXR_BUILD_UNITEST=OFF", "-DTILEXR_COMBINE_HT_SIM_SEND=OFF",
             "-DTILEXR_COMBINE_HT_SST_FP32=OFF", "-DASCEND_DRIVER_PATH=" + str(driver)]
    targets = ["tilexr_combine_ht_test", "tilexr-ep-ht-local-return", "tilexr-ep-ht-final-reduce"]
    if not step("configure", ["cmake", "-S", str(REPO), "-B", str(BUILD), *flags], 65):
        return
    if not step("build", ["cmake", "--build", str(BUILD), "--target", *targets, "-j8"], 390):
        return
    SUMMARY["native_build"] = "pass"

    qp = compile_fixture("multi_jetty_qp", "multi_jetty_qp.cpp", REPO, BUILD, COMM, "tile-comm")
    modules = compile_fixture("multi_jetty_modules", "multi_jetty_modules.cpp", REPO, BUILD, EP,
                              "tilexr-ep-ht-local-return",
                              ("-ltilexr-ep-ht-final-reduce", "-L" + str(COMM),
                               "-Wl,-rpath," + str(COMM), "-ltile-comm"))
    if not qp or not modules:
        from matrix import SUMMARY as fixture_summary
        SUMMARY["fixture_build_errors"] = fixture_summary.get("build_errors")
    rootinfo = remote.write_eid_rootinfo()
    env = dict(os.environ, TILEXR_UDMA_ROOTINFO_PATH=str(rootinfo))

    for attempt in range(13):
        occupancy = remote.device_status()
        if occupancy["idle_devices"] == RANKS:
            break
        if attempt < 12:
            time.sleep(5)
    SUMMARY["initial_devices"] = occupancy
    if qp:
        rows = SUMMARY["qp"] = {}
        for six, two, direct in [(4, 4, 0), (8, 8, 0), (16, 8, 0), (8, 16, 0),
                                 (16, 16, 0), (24, 24, 0), (16, 32, 0), (16, 16, 7), (32, 32, 0)]:
            label = f"{six}-{two}" + ("-direct" if direct else "")
            logs, row = run_group("qp-" + label, lambda rank: [str(qp), str(rank), str(six), str(two), str(direct)],
                                  RANKS, config(env, six, two, direct), 35)
            rows[label] = row
            if logs:
                values = [m.split() for log in logs for m in re.findall(r"^QP_RESULT (.*)$", log, re.M)]
                row["observed"] = sorted({tuple(map(int, value[9:13])) for value in values})
            if row["status"] == "devices_busy":
                break

    rows = SUMMARY["gather_gateway_reduce"] = {}
    test = str(BUILD / "src/moonep/combine_ht/tilexr_combine_ht_test")
    for world, tokens, six, two, direct in [(8, 32, 16, 16, 0), (64, 32, 16, 16, 7), (64, 256, 16, 32, 0)]:
        label = f"w{world}-t{tokens}-q{six + two + direct}"
        def args(rank):
            return [test, "--mode", "fused", "--world", str(world), "--rank", str(rank),
                    "--device", str(rank), "--local-comm", "1", "--tokens", str(tokens), "--hidden", "128",
                    "--topk", "4", "--chunk", "32", "--splits", "4", "--reduce-begin", "8",
                    "--reducers", "16", "--iterations", "4", "--domain", "0"]
        logs, row = run_group("gather-" + label, args, RANKS, config(env, six, two, direct), 45)
        rows[label] = row
        if logs:
            row["checked_rounds"] = sum(log.count("correctness=pass") for log in logs)
            if row["status"] == "pass" and row["checked_rounds"] != 32:
                row["status"] = "missing_checks"
        if row["status"] == "devices_busy":
            break

    if modules:
        rows = SUMMARY["local_return"] = {}
        for tokens, chunk, splits, direct in [(32, 32, 1, 0), (32, 32, 4, 7), (256, 64, 4, 7)]:
            label = f"t{tokens}-c{chunk}-s{splits}-d{direct}"
            rows[label] = timed_module("return-" + label,
                lambda rank: [str(modules), "return", str(rank), str(tokens), str(chunk), str(splits)],
                config(env, 16, 16, direct))
            if rows[label]["status"] == "devices_busy":
                break
        rows = SUMMARY["final_reduce"] = {}
        for tokens, splits in [(32, 1), (32, 4), (512, 1), (512, 4)]:
            label = f"t{tokens}-s{splits}"
            devices = remote.device_status()["idle_devices"][:1]
            if not devices:
                rows[label] = {"status": "devices_busy"}
                break
            logs, row = run_group("final-" + label, lambda rank:
                [str(modules), "final", str(rank), str(tokens), str(tokens), str(splits)], devices, env, 35)
            rows[label] = row
            if logs:
                matches = re.findall(r"^FINAL_RESULT (.*)$", logs[0], re.M)
                if matches:
                    row["checked"] = list(map(int, matches[-1].split()))
                elif row["status"] == "pass":
                    row["status"] = "missing_checks"


try:
    run()
except (ValueError, OSError, subprocess.SubprocessError) as exc:
    SUMMARY["failure_type"] = type(exc).__name__
(ROOT / "result.json").write_text(json.dumps(SUMMARY, indent=2) + "\n")
print("TILEXR_RESULT " + json.dumps(SUMMARY, ensure_ascii=True, separators=(",", ":")))
