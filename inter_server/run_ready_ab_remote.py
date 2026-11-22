"""Use private CPU SST resources to test a single slow reducer."""
import struct

clone_retained = prepare_bulk
retained_baseline = baseline_name


def baseline_name(build, tokens):
    if build["variant"].startswith("sst-mte-ready-") or build["variant"] == "sst-mte-lane-balanced":
        reference = "range-cache" if build["variant"].endswith("-combined") else "drain-count-cache"
        return f"t{tokens}-sst-mte-{reference}-{build['baseline_kernel_sha256'][:8]}"
    return retained_baseline(build, tokens)


def prepare_bulk(request, case, container_case):
    build = clone_retained(request, case, container_case)
    if "stagger" not in build["variant"]:
        return build
    workload = json.loads((case / "workload.yml").read_text())["workload_config"]
    manifest_path = case / "case-manifest.json"
    manifest = json.loads(manifest_path.read_text())
    for rank in range(64):
        params = workload[f"chip{rank}"]["kernel_param"]
        ready = params["config.readyOffset"]["value"]
        reducers = params["config.reducerCount"]["value"]
        magic = params["config.magic"]["value"]
        assert reducers == 40 and params["config.chunks"]["value"] == 4
        relative = f"input_bin/rank{rank:02d}/workspace.bin"
        assert params["load_workspace"]["input_bin"] == f"{container_case}/{relative}"
        path = case / relative
        data = bytearray(path.read_bytes())
        position = ready + (reducers - 1) * 512
        assert struct.unpack_from("<qq", data, position) == (magic, 4)
        struct.pack_into("<q", data, position + 8, 1)
        path.write_bytes(data)
        manifest["files"][relative] = sha(path)
    manifest["synthetic_ready"] = {"initial_final_reducer_epoch": 1,
                                   "producer": "owner 0 per rank", "producer_delay_ticks": 8192}
    write_json(manifest_path, manifest)
    return build


def run_ready(request):
    global NAME, ID
    work = {}
    for variant, candidate in request["variants"].items():
        build = candidate["build"]
        prefix = build["variant"] + "-" + build["kernel_sha256"][:8]
        VARIANTS[build["kernel_sha256"]] = "t4-" + prefix
        for tokens in ((4, 8) if variant in ("ready_watermark", "ready_combined", "lane_balanced") else (4,)):
            work[f"{variant}_t{tokens}"] = (MOUNT / f"t{tokens}-{prefix}", tokens, candidate)
    if request["mode"] == "status":
        return {tag: case_status(path) for tag, (path, _, _) in work.items()}
    assert not any(path.exists() for path, _, _ in work.values())
    previous = NAME, ID
    NAME = "tilexr-cpu-b4f4660a-ht-ready"
    if request["combined"]:
        NAME += "-combined"
    if request["lane_only"]:
        NAME = "tilexr-cpu-b4f4660a-ht-small-lanes"
    image = "sha256:19e063d4eebab9785ad24b175ee1567dfb24c1180698f67fe0ab32a731a19cd5"
    ID = docker("create", "--name", NAME, "--runtime=runc", "--network=none",
                "--cap-drop=ALL", "--security-opt=no-new-privileges", "--cpus=8",
                "--memory=16g", "--pids-limit=1024", "--shm-size=64m",
                "--ulimit", "nofile=524288:524288",
                "--mount", f"type=bind,src={MOUNT.parent},dst=/work",
                image, "/bin/bash", "-lc", "exec sleep infinity").stdout.decode("ascii").strip()
    results = {}
    try:
        for tag, (_, tokens, candidate) in work.items():
            try:
                results[tag] = main(dict(candidate, tokens=tokens, forward_images=True))
            except (AssertionError, OSError, ValueError, subprocess.SubprocessError) as error:
                results[tag] = {"run": {"status": "failed"}, "exception_type": type(error).__name__}
    finally:
        docker("rm", ID)
        NAME, ID = previous
    for row in results.values():
        row["private_container_removed"] = True
    return results
