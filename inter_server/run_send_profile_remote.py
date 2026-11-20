"""Initialize SDK capture memory and collect Perfetto after the SST checker."""



def stage_profile(request, case, container_case):
    resources = request["profile"]
    (case / "profile_sdk.zip").write_bytes(base64.b64decode(resources["sdk"]))
    adapter = case / "adapter" / "profile_trace.py"
    adapter.write_bytes(base64.b64decode(resources["adapter"]))
    sys.path[:0] = [str(case / "profile_sdk.zip"), str(adapter.parent)]
    from profile_trace import configure_workload
    workload = json.loads((case / "workload.yml").read_text())
    configure_workload(case, workload, container_case)
    for name in ("workload.yml", "v310_config/workload_config.yml"):
        write_json(case / name, workload)


def export_profile(case, container_case):
    sdk = container_case + "/profile_sdk.zip"
    export = docker("exec", "-e", "PYTHONPATH=" + sdk, NAME, "python3",
                    container_case + "/adapter/profile_trace.py", container_case,
                    "1650", timeout=120)
    frames = int(export.stdout)
    build = subprocess.run(
        ["docker", "exec", "-e", "PYTHONPATH=" + sdk, NAME, "python3", "-m", "tilexr_profiling",
         "--mapping", container_case + "/profile.mapping.json",
         "--records", container_case + "/profile.records.bin",
         "--output-dir", container_case + "/profile-report", "--artifacts", "perfetto", "json",
         "--attribute", 'clock_basis="SST AICore clock 1.65 GHz"'],
        capture_output=True, timeout=100,
    )
    (case / "profile-render.log").write_bytes(build.stdout + build.stderr)
    build.check_returncode()
    trace = case / "profile-report/perfetto_trace.json"
    trace_data = json.loads(trace.read_text())
    stages = {}
    for event in trace_data["traceEvents"]:
        if event.get("ph") == "X":
            stages[event["name"]] = stages.get(event["name"], 0) + 1
    assert frames == 1024 and stages.get("kernel_total") == frames
    return {"streams": frames, "zero_dropped_records": True, "stage_slices": stages,
            "trace_sha256": sha(trace), "trace_bytes": trace.stat().st_size}
