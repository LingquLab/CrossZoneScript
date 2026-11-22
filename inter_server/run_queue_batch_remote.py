"""Reuse the passed baseline's model, input images, guard layout and checker."""


def prepare_bulk(request, case, container_case):
    tokens = request["tokens"]
    build = request["build"]
    baseline = case.with_name(f"t{tokens}-sst-mte-bulk-forward-hotpath-{build['baseline_kernel_sha256'][:8]}")
    manifest = json.loads((baseline / "case-manifest.json").read_text())
    assert json.loads((baseline / "run.json").read_text())["status"] == "sst_and_checker_passed"
    assert manifest["kernel_sha256"] == build["baseline_kernel_sha256"]
    assert build["entry"] == manifest["entry"]
    assert manifest["forward_record_stride"] == 32
    assert build["compiled_in"] == "blue"
    binary = gzip.decompress(base64.b64decode(request["object"], validate=True))
    assert hashlib.sha256(binary).hexdigest() == build["kernel_sha256"]
    source_case = str(Path(container_case).with_name(baseline.name))
    case.mkdir(exist_ok=False)
    for name, expected_sha in manifest["files"].items():
        relative = Path(name)
        assert not relative.is_absolute() and ".." not in relative.parts
        source, target = baseline / relative, case / relative
        assert sha(source) == expected_sha, name
        target.parent.mkdir(parents=True, exist_ok=True)
        if name == "kernel/inter_server_send.o":
            assert len(binary) == source.stat().st_size
            target.write_bytes(binary)
        elif name in {"workload.yml", "v310_config/workload_config.yml"}:
            text = source.read_text()
            assert source_case in text
            target.write_text(text.replace(source_case, container_case))
        elif name != "blue-build-record.json":
            shutil.copyfile(source, target)
    write_json(case / "blue-build-record.json", build)
    manifest.update(
        variant=build["variant"],
        candidate_commit=build["candidate_commit"],
        kernel_sha256=build["kernel_sha256"],
        baseline_kernel_sha256=build["baseline_kernel_sha256"],
        files={str(p.relative_to(case)): sha(p) for p in sorted(case.rglob("*")) if p.is_file()},
    )
    write_json(case / "case-manifest.json", manifest)
    return build


def case_status(path):
    result = {"case_exists": path.is_dir()}
    for name in ("run.json", "task-result.json"):
        artifact = path / name
        if artifact.is_file():
            try:
                record = json.loads(artifact.read_text())
            except ValueError:
                continue
            result[name] = {key: record[key] for key in
                            ("status", "kernel_sha256", "returncode", "guards", "runner_exit_code",
                             "runner_exception_type", "runner_wall_seconds", "sst_simulated_microseconds",
                             "exported_rank_count", "container_stopped", "run") if key in record}
    log = path / "sim.log"
    if log.is_file():
        result["log_bytes"] = log.stat().st_size
        with log.open("rb") as stream:
            stream.seek(max(0, log.stat().st_size - 65536))
            cycles = re.findall(rb"Sim: ([0-9]+)", stream.read())
        if cycles:
            result["latest_log_cycle"] = int(cycles[-1])
    return result


def run_queue_batch(request):
    build = request["build"]
    name = "sst-mte-queue-batch-" + build["kernel_sha256"][:8]
    VARIANTS[build["kernel_sha256"]] = "t4-" + name
    if request["mode"] == "status":
        return {f"t{tokens}": case_status(MOUNT / f"t{tokens}-{name}") for tokens in (4, 8)}
    result = {}
    for tokens in (4, 8):
        result[f"t{tokens}"] = row = main(dict(request, tokens=tokens, forward_images=True))
        if row.get("run", {}).get("status") != "sst_and_checker_passed":
            break
        baseline = case_status(MOUNT / f"t{tokens}-sst-mte-bulk-forward-hotpath-{build['baseline_kernel_sha256'][:8]}")
        baseline_receipt = baseline["task-result.json"]
        assert baseline_receipt["kernel_sha256"] == build["baseline_kernel_sha256"]
        row["baseline_simulated_microseconds"] = baseline_receipt["sst_simulated_microseconds"]
        row["baseline_runner_wall_seconds"] = baseline_receipt["runner_wall_seconds"]
        write_json(MOUNT / f"t{tokens}-{name}" / "task-result.json", row)
    return result
