"""Install immutable blue-prepared multi-chunk input and reuse SST platform files."""

from io import BytesIO
from pathlib import PurePosixPath
import tarfile


def prepare_bulk(request, case, container_case):
    identity = request["identity"]
    build = request["build"]
    data = base64.b64decode(request["archive"], validate=True)
    assert hashlib.sha256(data).hexdigest() == identity["input_archive_sha256"]
    assert identity["tokens"] == request["tokens"] == 257
    assert identity["hidden"] == request["hidden"] and identity["hidden"] in (16, 256)
    template = MOUNT / "t4-sst-mte-bulk-forward-hotpath-1a2f3549"
    template_manifest = json.loads((template / "case-manifest.json").read_text())
    assert template_manifest["kernel_sha256"].startswith("1a2f3549")
    assert json.loads((template / "run.json").read_text())["status"] == "sst_and_checker_passed"
    binary = gzip.decompress(base64.b64decode(request["object"], validate=True))
    assert hashlib.sha256(binary).hexdigest() == build["kernel_sha256"]
    assert build["entry"] == template_manifest["entry"] and build["compiled_in"] == "blue"

    case.mkdir(exist_ok=False)
    with tarfile.open(fileobj=BytesIO(data), mode="r:gz") as archive:
        members = list(archive)
        assert len(members) < 4096 and sum(member.size for member in members) < 512 * 1024 * 1024
        for member in members:
            rel = PurePosixPath(member.name)
            assert member.isfile() and not rel.is_absolute() and ".." not in rel.parts
            assert rel.parts[0] in {"workload.yml", "export-layout.json", "input_bin", "fixture-data"}
            target = case / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("xb") as dest:
                shutil.copyfileobj(archive.extractfile(member), dest)
    workload = case / "workload.yml"
    text = workload.read_text()
    assert "/work/inter-server-native/t257-queue-batch-data/" in text
    workload.write_text(text.replace("/work/inter-server-native/t257-queue-batch-data", container_case))
    chips = json.loads(workload.read_text())["workload_config"]
    assert len(chips) == 64 and all(chip["block_dim"] == 16 and chip["vec_func"] == build["entry"]
                                  and chip["kernel_file"] == container_case + "/kernel/inter_server_send.o"
                                  for chip in chips.values())
    fixture = case / "fixture-data"
    fixture_manifest = json.loads((fixture / "manifest.json").read_text())
    assert fixture_manifest["tokens"] == 257 and fixture_manifest["hidden"] == request["hidden"] and fixture_manifest["chunks"] == 5

    for name, expected in template_manifest["files"].items():
        rel = Path(name)
        assert not rel.is_absolute() and ".." not in rel.parts
        if rel.parts[0] in {"input_bin", "fixture-data"} or name in {
                "workload.yml", "export-layout.json", "blue-build-record.json"}:
            continue
        source, target = template / rel, case / rel
        assert sha(source) == expected, name
        target.parent.mkdir(parents=True, exist_ok=True)
        if name == "v310_config/workload_config.yml":
            shutil.copyfile(workload, target)
        elif name == "kernel/inter_server_send.o":
            assert source.stat().st_size == len(binary)
            target.write_bytes(binary)
        else:
            shutil.copyfile(source, target)

    write_json(case / "blue-build-record.json", build)
    manifest = dict(template_manifest)
    manifest.update(
        variant=build["variant"],
        candidate_commit=build["candidate_commit"],
        kernel_sha256=build["kernel_sha256"],
        input_archive_sha256=identity["input_archive_sha256"],
        fixture_files={str(p.relative_to(fixture)): sha(p) for p in sorted(fixture.rglob("*")) if p.is_file()},
        files={str(p.relative_to(case)): sha(p) for p in sorted(case.rglob("*")) if p.is_file()},
    )
    write_json(case / "case-manifest.json", manifest)
    return build


def run_multichunk(request):
    global NAME, ID
    if request["mode"] == "matrix":
        small_hash = request["variants"]["queue_batch"]["build"]["kernel_sha256"][:8]
        small = MOUNT / ("t8-sst-mte-queue-batch-" + small_hash) / "task-result.json"
        deadline = time.monotonic() + 2100
        while not small.is_file() and time.monotonic() < deadline:
            time.sleep(10)
        if not small.is_file():
            return {"prerequisite": {"run": {"status": "small_t8_receipt_missing"}}}
        try:
            small_result = json.loads(small.read_text())
        except ValueError:
            time.sleep(1)
            small_result = json.loads(small.read_text())
        if small_result.get("run", {}).get("status") != "sst_and_checker_passed":
            return {"prerequisite": {"run": {"status": "small_t8_failed"}}}
        result = {}
        previous = NAME, ID
        if request["memory_retry"]:
            NAME = "tilexr-cpu-b4f4660a-ht-t257"
            image = "sha256:19e063d4eebab9785ad24b175ee1567dfb24c1180698f67fe0ab32a731a19cd5"
            ID = docker("create", "--name", NAME, "--runtime=runc", "--network=none",
                        "--cap-drop=ALL", "--security-opt=no-new-privileges", "--cpus=8",
                        "--memory=32g", "--pids-limit=1024", "--shm-size=64m",
                        "--ulimit", "nofile=524288:524288",
                        "--mount", f"type=bind,src={MOUNT.parent},dst=/work",
                        image, "/bin/bash", "-lc", "exec sleep infinity").stdout.decode("ascii").strip()
        try:
            for tag in ("queue_batch", "batch_cqe", "hotpath"):
                result[tag] = run_multichunk(dict(request, **request["variants"][tag], variant=tag, mode="run"))
        finally:
            if request["memory_retry"]:
                docker("rm", ID)
                NAME, ID = previous
        return result
    build = request["build"]
    name = "t257-" + request["variant"] + "-" + build["kernel_sha256"][:8]
    if request["focused"]:
        name += "-focused"
    elif request["memory_retry"]:
        name += "-32g"
    VARIANTS[build["kernel_sha256"]] = name
    if request["mode"] == "status":
        return case_status(MOUNT / name)
    return main(dict(request, tokens=257, hidden=request["identity"]["hidden"], forward_images=True))
