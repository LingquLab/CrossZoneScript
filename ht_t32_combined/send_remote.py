"""Run the T32 sender and retain comparable counters from the SST output."""
def transport_evidence(case, rings):
    totals = collections.Counter()
    gaps = []
    for host, expected in rings.items():
        trace = case / "OUTPUT/stat_trace" / f"Host{host}_ba_layer_trace.json"
        if not trace.is_file():
            continue
        observed, truncated = read_observed(trace, host)
        totals["hosts_sampled"] += 1
        totals["truncated_hosts"] += int(truncated)
        for ring, count in expected.items():
            label = "payload" if count > 1 else "so_only"
            totals[label + "_slots_expected"] += count
            for kind, event in (("sq", "RD_REQ"), ("cq", "WR_REQ")):
                missed = sorted(set(range(count)) - observed[event].get(ring, set()))
                totals[label + "_" + kind + "_slots_observed"] += count - len(missed)
                if missed and label == "payload" and len(gaps) < 4:
                    gaps.append({"host": host, "ring": ring, "kind": kind,
                                 "first_missing_slot": missed[0]})
    return {"coverage": dict(totals), "sample_missing_payload_slots": gaps}


def run_t32(request):
    global NAME, ID
    build = request["build"]
    suffix = "-distributed" if request.get("distributed") else ""
    variant = "t32-sst-mte-send-" + build["kernel_sha256"][:8] + suffix
    VARIANTS[build["kernel_sha256"]] = variant
    case = MOUNT / variant
    NAME = "tilexr-cpu-b4f4660a-ht-t32-send" + suffix
    image = "sha256:19e063d4eebab9785ad24b175ee1567dfb24c1180698f67fe0ab32a731a19cd5"
    ID = docker("create", "--name", NAME, "--runtime=runc", "--network=none",
                "--cap-drop=ALL", "--security-opt=no-new-privileges", "--cpus=8",
                "--memory=16g", "--pids-limit=1024", "--shm-size=64m",
                "--ulimit", "nofile=524288:524288",
                "--mount", f"type=bind,src={MOUNT.parent},dst=/work",
                image, "/bin/bash", "-lc", "exec sleep infinity").stdout.decode("ascii").strip()
    try:
        result = main(dict(request, tokens=32, hidden=256, forward_images=True))
        state = result.get("run", {}).get("status")
        if state == "sst_and_checker_passed":
            result["outputs"] = compare_outputs(case)
        else:
            result.pop("sst_simulated_microseconds", None)
        if (case / "sim.log").is_file():
            with (case / "sim.log").open("rb") as stream:
                result["cqe_log_records"] = sum(line.count(b"WrCQEResp") for line in stream)
        if state in ("timed_out", "failed"):
            result["transport_evidence"] = transport_evidence(case, request["rings"])
    except (AssertionError, OSError, ValueError, subprocess.SubprocessError) as error:
        result = {"stage_error": type(error).__name__}
        if case.is_dir():
            import traceback
            (case / "t32-failure.log").write_text(traceback.format_exc())
    finally:
        docker("rm", "-f", ID)
    result["private_container_removed"] = True
    result["kernel_sha256"] = build["kernel_sha256"]
    result["fixture_coverage"] = request["coverage"]
    if case.is_dir():
        write_json(case / "t32-summary.json", result)
    return result
