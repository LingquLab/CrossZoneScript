"""Run the T96 baseline, then vary one scheduling parameter per run."""

def run_schedule(request):
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
