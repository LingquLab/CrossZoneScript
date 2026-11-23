"""Preflight and run the same fused FP32 entry on all 64 SST chips."""
def run_e2e(request):
    global NAME, ID
    build = request["build"]
    digest = build["kernel_sha256"]
    tokens = request["identity"]["tokens"]
    assert tokens in (4, 32)
    variant = f"t{tokens}-combine-fp32-{digest[:8]}"
    suffix = "-spread" if request.get("spread_gateways") else ""
    if suffix:
        assert tokens == 32 and request["identity"]["runtime_root"].endswith("-spread-data")
    variant += suffix
    case = MOUNT / variant
    result = {"kernel_sha256": digest, "entry": build["entry"], "case": variant}
    if case.exists():
        prior = case / "e2e-summary.json"
        if prior.is_file():
            return json.loads(prior.read_text())
        result["retained_case"] = True
        for name in ("run.json", "task-result.json"):
            record = case / name
            if record.is_file():
                data = json.loads(record.read_text())
                keys = ("status", "returncode", "guards", "run", "sst_simulated_microseconds",
                        "runner_exit_code", "runner_exception_type", "exported_rank_count")
                result[name] = {key: data[key] for key in keys if key in data}
        return result
    NAME = f"tilexr-cpu-b4f4660a-ht-t{tokens}-e2e-{digest[:8]}{suffix}"
    VARIANTS[digest] = variant
    image = "sha256:19e063d4eebab9785ad24b175ee1567dfb24c1180698f67fe0ab32a731a19cd5"
    try:
        docker("info", "--format", "{{.ServerVersion}}", timeout=8)
        docker("image", "inspect", "--format", "{{.Id}}", image, timeout=8)
    except (OSError, subprocess.SubprocessError) as error:
        return dict(result, preflight="docker_unavailable", error_type=type(error).__name__)
    try:
        creation = docker(
            "create", "--name", NAME, "--runtime=runc", "--network=none", "--cap-drop=ALL",
            "--security-opt=no-new-privileges", "--cpus=8", "--memory=32g", "--pids-limit=1024",
            "--shm-size=64m", "--ulimit", "nofile=524288:524288",
            "--mount", f"type=bind,src={MOUNT.parent},dst=/work",
            image, "/bin/bash", "-lc", "exec sleep infinity",
        )
        ID = creation.stdout.decode("ascii").strip()
    except (OSError, subprocess.SubprocessError) as error:
        return dict(result, preflight="container_create_failed", error_type=type(error).__name__)
    try:
        result.update(main(dict(request, tokens=tokens, hidden=256, forward_images=True)))
    except (OSError, ValueError, AssertionError, subprocess.SubprocessError) as error:
        result["stage_error"] = type(error).__name__
        if case.is_dir():
            import traceback
            (case / "e2e-failure.log").write_text(traceback.format_exc())
    finally:
        try:
            docker("rm", "-f", ID)
            result["private_container_removed"] = True
        except (OSError, subprocess.SubprocessError) as error:
            result["cleanup_error"] = type(error).__name__
    if case.is_dir():
        timing = case / "sim.log"
        record_path = case / "run.json"
        result["cqe_log_records"] = 0
        if timing.is_file():
            with timing.open("rb") as stream:
                result["cqe_log_records"] = sum(line.count(b"WrCQEResp") for line in stream)
        if record_path.is_file():
            record = json.loads(record_path.read_text())
            result["run"] = {key: record[key] for key in ("status", "returncode", "guards") if key in record}
            result["executed_sst"] = "pid" in record
        if result.get("run", {}).get("status") in ("timed_out", "interrupted"):
            result.pop("sst_simulated_microseconds", None)
        check = case / "check.log"
        if check.is_file():
            summary = check.read_text(errors="replace").strip()
            try:
                stats = json.loads(summary)
                result["checked"] = {key: stats[key] for key in (
                    "ranks", "returned_values", "completion_slots", "output_values", "statuses"
                )}
            except (ValueError, KeyError, TypeError):
                matches = re.findall(r"(?m)^ValueError: (rank\d\d (?:returned|completed|output)\[\d+\] mismatch: [0-9a-f]+ != [0-9a-f]+)$",
                                     summary)
                if matches:
                    result["first_mismatch"] = matches[-1]
                elif "AssertionError" in summary:
                    result["checker_error"] = "AssertionError"
        write_json(case / "e2e-summary.json", result)
    return {key: result[key] for key in (
        "case", "kernel_sha256", "entry", "preflight", "error_type", "stage_error", "cleanup_error",
        "private_container_removed", "executed_sst", "run", "runner_exit_code", "runner_exception_type",
        "sst_simulated_microseconds", "runner_wall_seconds", "checked", "first_mismatch", "checker_error", "cqe_log_records"
    ) if key in result}
