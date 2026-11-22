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
    assert identity["hidden"] == request["hidden"] == 16
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
    assert fixture_manifest["tokens"] == 257 and fixture_manifest["hidden"] == 16 and fixture_manifest["chunks"] == 5

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
    build = request["build"]
    name = "t257-" + request["variant"] + "-" + build["kernel_sha256"][:8]
    VARIANTS[build["kernel_sha256"]] = name
    if request["mode"] == "status":
        return case_status(MOUNT / name)
    return main(dict(request, tokens=257, hidden=16, forward_images=True))
