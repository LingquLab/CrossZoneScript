"""Compile real TileXR preparation components and check allocation/proof contracts on CPU."""

import argparse
import json
from pathlib import Path
import shutil
import subprocess
import tempfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True)
    args = parser.parse_args()
    repo = Path(args.repo).resolve()
    compiler = shutil.which("g++") or shutil.which("clang++")
    assert compiler, "C++_compiler_unavailable"
    stems = ["direct_orchestrator", "install_provider", "provider", "launch_package", "repository",
             "driver_adapter", "specs", "resource_allocator", "producer_plan", "barrier_program",
             "microcode", "aiv_fanout_program", "memory_program", "ms_relay_program", "alltoall_program",
             "signal_wait_program", "lower_layer_plan_builder", "lower_layer_payloads"]
    with tempfile.TemporaryDirectory(prefix="tilexr-aiv-prepare-") as directory:
        binary = Path(directory) / "aiv_prepare"
        command = [compiler, "-std=c++14", "-ffunction-sections", "-fdata-sections",
                   "-I", str(repo / "src/include"), "-I", str(repo / "src/comm"),
                   str(Path(__file__).with_name("aiv_prepare_regression.cpp"))]
        command += [str(repo / ("src/comm/ccu/tilexr_ccu_" + stem + ".cpp")) for stem in stems]
        # Discard the unused runtime submit entry points; no runtime implementation is linked.
        command += ["-Wl,--gc-sections", "-o", str(binary)]
        subprocess.run(command, check=True, capture_output=True, text=True, timeout=120)
        result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=20)
        print(json.dumps({"exit_code": result.returncode, "stdout": result.stdout.strip(),
                          "stderr": result.stderr.strip()[:1500], "npu_workload_run": False}))
        raise SystemExit(result.returncode)


if __name__ == "__main__":
    main()
