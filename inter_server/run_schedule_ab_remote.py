"""Run the T96 baseline, then vary one scheduling parameter per run."""

def wait_for_metadata(candidates):
    deadline = time.monotonic() + 4100
    while time.monotonic() < deadline:
        pending = 0
        for build in candidates:
            suffix = build["variant"] + "-" + build["kernel_sha256"][:8]
            t4 = case_status(MOUNT / ("t4-" + suffix)).get("task-result.json", {})
            if not t4.get("container_stopped"):
                pending += 1
            elif t4.get("run", {}).get("status") == "sst_and_checker_passed":
                t8 = case_status(MOUNT / ("t8-" + suffix)).get("task-result.json", {})
                if not t8.get("container_stopped"):
                    pending += 1
        if not pending:
            return True
        time.sleep(10)
    return False


def run_schedule(request):
    if request["mode"] == "run" and not wait_for_metadata(request["metadata_variants"]):
        return {"prerequisite": {"run": {"status": "metadata_receipt_missing"}}}
    result = {}
    for tag, candidate in request["variants"].items():
        build = candidate["build"]
        name = "t96-" + build["variant"] + "-" + build["kernel_sha256"][:8]
        VARIANTS[build["kernel_sha256"]] = name
        if request["mode"] == "status":
            result[tag] = case_status(MOUNT / name)
            continue
        try:
            result[tag] = main(dict(request, **candidate, tokens=96, hidden=256, forward_images=True))
        except (AssertionError, OSError, ValueError, subprocess.SubprocessError) as error:
            result[tag] = {"run": {"status": "failed"}, "exception_type": type(error).__name__}
    return result
