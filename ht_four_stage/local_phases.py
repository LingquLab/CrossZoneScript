"""Run the compiled local sender and local reducer as sequential SST workloads."""
import struct

BUILD = "ht-build-8882b28a-07n31u_w"
LABEL = "/work/inter-server-native/t4-local-ht-fp32-data"
NATIVE = "tilexr_moonep_combine_ht_kernel"
EXPECTED = "97069d2af16a17694f6fc63b37d196d531b6d37fa8e54e3daff7e5e56affc2e9"
PADDED = "09b138b368c80227aa4d0a0a5dbf56d135f325044352f7608a0a8765191c9966"
IMAGE = "sha256:19e063d4eebab9785ad24b175ee1567dfb24c1180698f67fe0ab32a731a19cd5"
CHECKER_NAME = "source/tools/unitest/simulator/experiments/inter_server_send/fixture.py"

def phase_artifacts(case):
    if not (case / "actual").is_dir():
        return {}
    digests = {}
    for field in ("returned", "completed", "output", "status"):
        digest = hashlib.sha256()
        for rank in range(64):
            digest.update((case / "actual" / f"rank{rank:02d}" / f"{field}.bin").read_bytes())
        digests[field] = digest.hexdigest()
    return digests

def summarize(case):
    result = {"case": case.name}
    if (case / "run.json").is_file():
        run = json.loads((case / "run.json").read_text())
        result.update({key: run[key] for key in ("status", "returncode", "guards", "kernel_sha256") if key in run})
    if (case / "sim.log").is_file():
        raw = (case / "sim.log").read_bytes()
        result["cqe"] = raw.count(b"WrCQEResp")
        end = re.findall(rb"Simulation is complete, simulated time: ([0-9.]+) (ns|us|ms|s)", raw)
        if end:
            value, unit = end[-1]
            result["sim_us"] = float(value) * {b"ns": .001, b"us": 1, b"ms": 1000, b"s": 1000000}[unit]
        result["errors"] = [key for key in ("segmentation fault", "fatal", "mmio", "assertion")
                            if key.encode() in raw.lower()]
    if (case / "check.log").is_file():
        record = (case / "check.log").read_text(errors="replace")
        try:
            result["checked"] = json.loads(record)
        except ValueError:
            first = re.findall(r"^ValueError: (rank\d{2} [a-z]+ \d+ [a-f\d]+ != [a-f\d]+)$", record, re.M)
            if first:
                result["first_mismatch"] = first[-1]
    if result.get("status") == "sst_and_checker_passed":
        result["exports_sha256"] = phase_artifacts(case)
    return result

def rehash(case):
    manifest_path = case / "case-manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["fixture_files"] = {str(p.relative_to(case / "fixture-data")): sha(p)
                                 for p in (case / "fixture-data").rglob("*") if p.is_file()}
    manifest["files"] = {str(p.relative_to(case)): sha(p) for p in case.rglob("*")
                         if p.is_file() and p != manifest_path}
    write_json(manifest_path, manifest)

def generate_local(case, phase, previous):
    workload = json.loads((case / "workload.yml").read_text())
    layout = json.loads((case / "export-layout.json").read_text())["ranks"]
    for row in layout:
        rank, base = row["rank"], row["sys_va_base_addr"]
        group, local_rank = divmod(rank, 8)
        rank_name = f"rank{rank:02d}"
        chip = workload["workload_config"][f"chip{rank}"]
        params = chip["kernel_param"]
        args = case / "input_bin" / rank_name / "kernel_args.bin"
        packed = bytearray(args.read_bytes())
        assert len(packed) == 248 and struct.unpack_from("<I", packed, 156)[0] == 3
        struct.pack_into("<I", packed, 156, phase)
        args.write_bytes(packed)
        params["word19"]["value"] = struct.unpack_from("<Q", packed, 152)[0]
        images = case / "input_bin" / rank_name
        input_values = [group * 64 + local_rank * 8 + token + h / 64
                        for token in range(4) for h in range(128)]
        (images / "input.bin").write_bytes(struct.pack("<512f", *input_values))
        workspace = images / "workspace.bin"
        data = bytearray(workspace.read_bytes())
        gold = case / "fixture-data" / rank_name
        expected_gather = bytearray(32 * 512)
        for sender in range(8):
            if sender != local_rank:
                values = [group * 64 + sender * 8 + token + h / 64
                          for token in range(4) for h in range(128)]
                struct.pack_into("<512f", expected_gather, sender * 2048, *values)
        (gold / "golden-returned.bin").write_bytes(expected_gather)
        completed = bytearray(16 * 512)
        for sender in range(8):
            if sender != local_rank:
                struct.pack_into("<Q", completed, sender * 512, 1)
        if phase == 2:
            for reducer in range(8):
                struct.pack_into("<I", completed, (8 + reducer) * 512, 1)
            exported = previous / "actual" / rank_name
            for field in ("returned", "completed"):
                bounds = row["exports"][field]
                start = bounds["addr"] - params["load_workspace"]["value"]
                artifact = (exported / (field + ".bin")).read_bytes()
                assert len(artifact) == bounds["size"]
                data[start:start + len(artifact)] = artifact
        workspace.write_bytes(data)
        (gold / "golden-completed.bin").write_bytes(completed)
        values = [sum(group * 64 + sender * 8 + token + h / 64 for sender in range(8))
                  for token in range(4) for h in range(128)]
        (gold / "golden-output.bin").write_bytes(struct.pack("<512f", *values) if phase == 2 else bytes(2048))
    write_json(case / "workload.yml", workload)
    write_json(case / "v310_config/workload_config.yml", workload)
    fixture_manifest = case / "fixture-data/manifest.json"
    manifest = json.loads(fixture_manifest.read_text())
    manifest["phase"] = phase
    write_json(fixture_manifest, manifest)

def execute_phase(case):
    container_case = "/work/inter-server-native/" + case.name
    command = [
        "python3", container_case + "/adapter/run.py",
        "--case", container_case, "--fixture", container_case + "/fixture-data",
        "--checker", container_case + "/" + CHECKER_NAME,
        "--environment", container_case + "/sst-environment.json",
        "--threads", "8", "--timeout", "540",
    ]
    with (case / "native-runner.log").open("wb") as log:
        proc = subprocess.run([
            "docker", "run", "--rm", "--runtime=runc", "--network=none", "--cap-drop=ALL",
            "--security-opt=no-new-privileges", "--cpus=8", "--memory=32g", "--pids-limit=1024",
            "--shm-size=64m", "--ulimit", "nofile=524288:524288", "--mount",
            f"type=bind,src={MOUNT.parent},dst=/work", IMAGE,
            "/bin/bash", "-c", 'set -e; source /opt/sim/cann-env.sh; exec "$@"', "bash", *command,
        ], stdout=log, stderr=subprocess.STDOUT, timeout=600)
    result = summarize(case)
    result["runner_exit_code"] = proc.returncode
    write_json(case / "phase-summary.json", result)
    return result

def run_local_phases(request):
    path = MOUNT.parent / BUILD / "sst/src/moonep/combine_ht/tilexr_moonep_combine_ht_kernel.o"
    native = path.read_bytes()
    assert hashlib.sha256(native).hexdigest() == EXPECTED
    padded = native.ljust(10485760, b"\x00")
    assert hashlib.sha256(padded).hexdigest() == PADDED
    request["object"] = base64.b64encode(gzip.compress(padded)).decode()
    request["build"] = {
        "entry": NATIVE, "kernel_sha256": PADDED, "compiled_in": "blue",
        "candidate_commit": "8882b28a4cc586f91ddcea171170bf9e304adc70",
        "variant": "four-stage-local-fp32",
    }
    results = []
    previous = None
    for phase, purpose in ((1, "local-send"), (2, "local-reduce")):
        case = MOUNT / ("ht-four-stage-8882b28a-" + purpose)
        if case.exists():
            results.append(summarize(case))
            if results[-1].get("status") != "sst_and_checker_passed":
                break
        else:
            prepare_bulk(request, case, "/work/inter-server-native/" + case.name)
            generate_local(case, phase, previous)
            rehash(case)
            results.append(execute_phase(case))
            if results[-1].get("status") != "sst_and_checker_passed":
                break
        previous = case
    return {"unpadded_object_sha256": EXPECTED, "phases": results}
