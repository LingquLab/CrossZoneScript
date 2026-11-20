import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import time


CASES = Path("/opt/sim/simulator/Testcases")
ROOT = CASES / "dispatch_64p_top6_profile_ab_20261120_01"
WORK = Path("/work/src")
NATIVE_PACKAGE = WORK / "dispatch_memory_64p_handoff"
TL_BUILD = WORK / "dispatch_tilelang_64p_68d32608_ub01"
SOURCES = {
    "native": (CASES / "dispatch_memory_64p_top6_profile_bridge_20261120_sst_tune01_full",
               "tilexr_ep_dispatch_memory_kernel.o",
               "7bcb77bf4be8f61cbc8172180e8ef877de40010ef3070943a5f88bd6cf09f625"),
    "tl": (CASES / "dispatch_tilelang_64p_top6_68d32608_ub01", "dispatch.o",
           "8c5fcf1de1c333d3294aeb6475348e340cd88e8694d2f2e80dd25566ad2a9ee0"),
}
ORDER = ("native-off", "tl-off", "tl-on", "native-on")
CONF_DIRS = ("v310_config", "UBusSim_config")
WORKLOADS = ("workload.yml", "workload_config.yml", "v310_config/workload_config.yml")
COPIED = ("audited_launcher.py", "expected-v310-runtime.json", "output_generator.py",
          "test_UBusSim.py", *WORKLOADS[:2])


def sha(path):
    with path.open("rb") as source:
        value = hashlib.sha256()
        for block in iter(lambda: source.read(1024 * 1024), b""):
            value.update(block)
        return value.hexdigest()


def write_json(path, data):
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=True, indent=2) + "\n")
    temp.replace(path)


def inventory(root, directory):
    return {str(path.relative_to(root / directory)): sha(path)
            for path in sorted((root / directory).rglob("*.bin"))}


def comparable_inputs(root):
    out = {}
    for path in (root / "input").rglob("*.bin"):
        if path.name not in {"x.bin", "ids.bin"}:
            continue
        rank = re.fullmatch(r"rank_?(\d+)", path.parent.name)
        if rank is None:
            raise ValueError("unexpected rank input path " + str(path.relative_to(root)))
        out[f"{rank[1]}/{path.name}"] = sha(path)
    if set(out) != {f"{rank}/{filename}" for rank in range(64)
                    for filename in ("x.bin", "ids.bin")}:
        raise ValueError("missing per-rank X or routing inputs")
    return out


def fixed_config(case):
    return {str(path.relative_to(case)): sha(path)
            for directory in CONF_DIRS for path in sorted((case / directory).rglob("*.yml"))
            if path.name != "workload_config.yml"}


def run_build(command, directory, logfile):
    with logfile.open("xb") as stream:
        result = subprocess.run(command, cwd=directory, stdout=stream,
                                stderr=subprocess.STDOUT, timeout=240)
    if result.returncode:
        errors = [line.strip()[:200] for line in logfile.read_text(errors="replace").splitlines()
                  if "error:" in line.lower() or "not found" in line.lower() or "FAILED" in line]
        raise ValueError(f"compiler exit {result.returncode} in {logfile}: {errors[:5]}")


def elf_identity(path, entry):
    text = subprocess.check_output(["readelf", "-h", "-sW", str(path)], text=True, timeout=15)
    if not re.search(r"Type:\s+EXEC\b", text) or "0x990000" not in text:
        raise ValueError(f"ELF is not linked C310: {path}")
    if not re.search(r"(?m)\bFUNC\s+GLOBAL\s+DEFAULT\s+\d+\s+" + re.escape(entry) + r"$", text):
        raise ValueError(f"ELF entry missing: {entry}")
    return sha(path)


def build_native_off():
    destination = ROOT / "build-native"
    shutil.copytree(NATIVE_PACKAGE, destination)
    scripts = destination / "kernel"
    build = (scripts / "build.sh").read_text()
    flag = "    -DTILEXR_EP_DISPATCH_MEMORY_ENABLE_PROFILING=1\n"
    if build.count(flag) != 1:
        raise ValueError("expected exactly one original native profiling flag")
    switch = scripts / "build-off.sh"
    switch.write_text(build.replace(flag, ""))
    entry = "tilexr_ep_dispatch_memory_kernel"
    out = destination / (entry + ".o")
    run_build(["bash", "-c", 'source /opt/sim/cann-env.sh && exec bash "$1" --out "$2" --format linked',
               "dispatch-build", str(switch), str(out)], destination, destination / "compile-off.log")
    return out, {"entry": entry, "elf_sha256": elf_identity(out, entry),
                 "source_commit": "6ead3f89dfafdb05a9a0594e3e1eb3ef6098b8c4",
                 "source_manifest_sha256": sha(destination / "SHA256SUMS"),
                 "profile_switch": "removed TILEXR_EP_DISPATCH_MEMORY_ENABLE_PROFILING"}


def build_tl_off():
    destination = ROOT / "build-tl"
    shutil.copytree(TL_BUILD, destination, ignore=shutil.ignore_patterns(
        "dispatch.o", "dispatch.rel.o", "dispatch.elf.txt", "build.log", "build-receipt.json",
        "direct-compile.log",
    ))
    source = destination / "dispatch.profile.cpp"
    if sha(source) != sha(TL_BUILD / "dispatch.profile.cpp"):
        raise ValueError("TileLang paired source changed")
    compile_script = destination / "compile.sh"
    build = compile_script.read_text()
    flag = "-DTILEXR_ENABLE_PROFILING=1"
    if build.count(flag) != 1:
        raise ValueError("expected exactly one original TileLang profiling flag")
    compile_script.write_text(build.replace(str(TL_BUILD), str(destination)).replace(
        flag, "-DTILEXR_ENABLE_PROFILING=0"))
    run_build(["bash", str(compile_script)], destination, destination / "compile-off.log")
    out = destination / "dispatch.o"
    entry = "tilexr_tl_dispatch_runtime_c64_profile"
    return out, {"entry": entry, "elf_sha256": elf_identity(out, entry),
                 "source_commit": "68d326088bde493f5188cc8b9b2e4fdd9c290d23",
                 "generated_source_sha256": sha(source),
                 "recorder_sha256": sha(destination / "include/tilexr/profiling/device.h"),
                 "profile_switch": "TILEXR_ENABLE_PROFILING=0; identical reserved UB and DeviceView ABI"}


def stage_case(label, kernel, identity):
    implementation = label.split("-")[0]
    base, kernel_name, base_sha = SOURCES[implementation]
    directory = ROOT.with_name(ROOT.name + "_" + label.replace("-", "_"))
    directory.mkdir()
    for name in ("input", "expected"):
        shutil.copytree(base / name, directory / name, copy_function=os.link)
    for name in CONF_DIRS:
        shutil.copytree(base / name, directory / name)
    metadata_file = "manifest.json" if implementation == "native" else "tilelang-scenario.json"
    for name in (*COPIED, metadata_file):
        shutil.copy2(base / name, directory / name)
    shutil.copy2(kernel, directory / kernel_name)
    base_size = (base / kernel_name).stat().st_size
    case_size = (directory / kernel_name).stat().st_size
    for name in (*WORKLOADS, *COPIED[0:4], metadata_file):
        file = directory / name
        initial = file.read_text()
        text = initial.replace(str(base), str(directory)).replace(base_sha, identity["elf_sha256"])
        if name == metadata_file:
            metadata = json.loads(text)
            fallback = "kernel_size_bytes"
            if fallback in metadata and metadata[fallback] == base_size:
                metadata[fallback] = case_size
            if "build_path" in metadata and label == "tl-off":
                metadata["build_path"] = str(ROOT / "build-tl")
            text = json.dumps(metadata, ensure_ascii=True, indent=2) + "\n"
        if text != initial:
            file.write_text(text)
    hashes = {sha(directory / name) for name in WORKLOADS}
    if len(hashes) != 1 or sha(directory / kernel_name) != identity["elf_sha256"]:
        raise ValueError("rebased workload or candidate ELF does not match")
    workload_hash = hashes.pop()
    metadata_path = directory / metadata_file
    metadata = json.loads(metadata_path.read_text())
    metadata["workload_sha256" if implementation == "native" else "workload_sha"] = workload_hash
    write_json(metadata_path, metadata)
    return {**identity, "directory": str(directory), "kernel_name": kernel_name,
            "workload_sha256": workload_hash, "fixed_config_sha256": fixed_config(directory)}


def main():
    started = time.monotonic()
    ROOT.mkdir(exist_ok=False)
    native, native_name, native_sha = SOURCES["native"]
    tilelang, tl_name, tl_sha = SOURCES["tl"]
    if sha(native / native_name) != native_sha or sha(tilelang / tl_name) != tl_sha:
        raise ValueError("qualified baseline ELF differs")
    input_sha = comparable_inputs(native)
    golden_sha = inventory(native, "expected")
    if input_sha != comparable_inputs(tilelang):
        raise ValueError("input hash mismatch across implementations")
    if len(golden_sha) != 256 or golden_sha != inventory(tilelang, "expected"):
        raise ValueError("golden hash mismatch across implementations")
    settings = fixed_config(native)
    if settings != fixed_config(tilelang):
        raise ValueError(f"fixed model/NoC differences: {sorted(set(settings.items()) ^ set(fixed_config(tilelang).items()))}")
    native_off, n_off = build_native_off()
    tl_off, t_off = build_tl_off()
    n_on = {**n_off, "elf_sha256": native_sha, "profile_switch": "TILEXR_EP_DISPATCH_MEMORY_ENABLE_PROFILING=1"}
    t_on = {**t_off, "elf_sha256": tl_sha, "profile_switch": "TILEXR_ENABLE_PROFILING=1; ub01 recorder"}
    pairs = ((native_off, n_off), (tl_off, t_off), (tilelang / tl_name, t_on), (native / native_name, n_on))
    cases = {}
    for label, (kernel, evidence) in zip(ORDER, pairs):
        cases[label] = stage_case(label, kernel, evidence)
        if cases[label]["fixed_config_sha256"] != settings:
            raise ValueError(f"conflicting model configuration for {label}")
    result = {"status": "READY", "directory": str(ROOT), "order": ORDER, "cases": cases,
              "input_hash_count": len(input_sha), "golden_hash_count": len(golden_sha),
              "environment_sha256": {
                  str(path): sha(path) for path in (
                      Path("/opt/sim/cann-env.sh"), Path("/opt/sim/sst-env.sh"),
                      Path("/opt/sim/simulator/config/sst-plugins.conf"),
                      Path("/opt/sim/local/sstcore/libexec/sstsim.x"),
                  )
              },
              "preparation_wall_seconds": round(time.monotonic() - started, 3)}
    write_json(ROOT / "inputs-and-golden.json", {"shared_inputs": input_sha, "golden": golden_sha})
    write_json(ROOT / "preflight.json", result)
    return result


if __name__ == "__main__":
    try:
        print(json.dumps(main()))
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print(json.dumps({"status": "FAILED", "directory": str(ROOT), "error": str(error)[:620]}))
