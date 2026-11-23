"""Compare two blue-built sender objects using the same retained T4 case."""
import struct

def baseline_name(build, tokens):
    assert tokens == 4
    return "t4-sst-mte-ready-combined-" + build["baseline_kernel_sha256"][:8]

def compare_outputs(case):
    differences = {}
    counters = {"ranks": 0, "nonzero_status": 0, "payload_values": 0, "payload_mismatches": 0,
                "so_slots": 0, "so_mismatches": 0}
    for rank in range(64):
        actual = case / "actual" / f"rank{rank:02d}"
        fixture = case / "fixture-data" / actual.name
        status = actual / "status.bin"
        if not status.is_file():
            continue
        counters["ranks"] += 1
        data = status.read_bytes()
        if len(data) == 512:
            for owner in range(16):
                value, = struct.unpack_from("<I", data, owner * 32)
                if value:
                    counters["nonzero_status"] += 1
                    differences.setdefault("status", dict(rank=rank, owner=owner, expected=0, actual=value))
        else:
            differences.setdefault("status", dict(rank=rank, expected_bytes=512, actual_bytes=len(data)))
        for field, kind, stride, code in (("returned","payload",2,"H"),("completed","so",512,"Q")):
            actual_file = actual / f"{field}.bin"
            if not actual_file.is_file():
                continue
            got = actual_file.read_bytes()
            want = (fixture / f"golden-{field}.bin").read_bytes()
            if len(got) != len(want):
                differences.setdefault(kind, dict(rank=rank, expected_bytes=len(want), actual_bytes=len(got)))
                continue
            count = "payload_values" if kind == "payload" else "so_slots"
            counters[count] += len(want) // stride
            for offset in range(0, len(want), stride):
                width = 2 if kind == "payload" else 8
                if got[offset:offset+width] == want[offset:offset+width]:
                    continue
                counters[kind+"_mismatches"] += 1
                parse = lambda values: hex(struct.unpack_from("<" + code, values, offset)[0])
                differences.setdefault(kind, dict(rank=rank, byte_offset=offset, expected=parse(want), actual=parse(got)))
    return dict(counters, first=differences)

def run_send(request):
    global NAME, ID
    image = "sha256:19e063d4eebab9785ad24b175ee1567dfb24c1180698f67fe0ab32a731a19cd5"
    results = {}
    for side, candidate in request.items():
        build = candidate["build"]
        digest = build["kernel_sha256"]
        variant = "t4-" + build["variant"] + "-" + digest[:8]
        VARIANTS[digest] = variant
        case = MOUNT / variant
        if case.exists():
            task_receipt = case / "comparison.json"
            results[side] = json.loads(task_receipt.read_text()) if task_receipt.is_file() else case_status(case)
            continue
        NAME = "tilexr-cpu-b4f4660a-ht-t4-send-ab-" + side
        ID = docker("create", "--name", NAME, "--runtime=runc", "--network=none", "--cap-drop=ALL",
                    "--security-opt=no-new-privileges", "--cpus=8", "--memory=16g", "--pids-limit=1024",
                    "--shm-size=64m", "--ulimit", "nofile=524288:524288",
                    "--mount", f"type=bind,src={MOUNT.parent},dst=/work",
                    image, "/bin/bash", "-lc", "exec sleep infinity").stdout.decode("ascii").strip()
        try:
            row = main(dict(candidate, tokens=4, forward_images=True))
            row["outputs"] = compare_outputs(case)
            if (case / "sim.log").is_file():
                row["cqe_log_records"] = (case / "sim.log").read_bytes().count(b"WrCQEResp")
        except (AssertionError, OSError, ValueError, subprocess.SubprocessError) as error:
            row = dict(kernel_sha256=digest, stage_error=type(error).__name__)
        finally:
            docker("rm", "-f", ID)
        row["private_container_removed"] = True
        if case.is_dir():
            write_json(case / "comparison.json", row)
        results[side] = row
    return results
