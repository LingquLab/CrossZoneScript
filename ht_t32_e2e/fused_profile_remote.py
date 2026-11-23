def configure_fused_profile(request, case, container_case, workload):
    for name, chip in workload["workload_config"].items():
        rank = int(name.removeprefix("chip"))
        target = chip["output_param"]["status"]["addr"] + 4096
        relative = f"input_bin/rank{rank:02d}/profile.bin"
        size = 16 * 16384
        (case / relative).write_bytes(bytes(size))
        params = chip["kernel_param"]
        offset = max(int(param["param_offset"]) for param in params.values()) + 8
        params["load_profile"] = dict(value=target, param_type="11", param_offset=str(offset),
                                      input_bin=f"{container_case}/{relative}")
        chip["output_param"]["profile"] = dict(addr=target, size=size)
    adapter = case / "adapter"
    adapter.mkdir(exist_ok=True)
    (adapter / "fused_profile.py").write_text(request["profile"])


def export_profile(case, container_case):
    result = subprocess.run(
        ["docker", "exec", NAME, "python3", container_case + "/adapter/fused_profile.py", container_case],
        timeout=180, capture_output=True,
    )
    (case / "profile-render.log").write_bytes(result.stdout + result.stderr)
    if result.returncode:
        return {"status": "render_failed", "render_exit": result.returncode}
    summary = json.loads(result.stdout)
    summary["trace_sha256"] = sha(case / "perfetto_trace.json")
    summary["summary_sha256"] = sha(case / "profile-summary.json")
    return summary
