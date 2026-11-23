"""Install immutable blue-prepared multi-chunk input and reuse SST platform files."""

from io import BytesIO
from pathlib import PurePosixPath
import tarfile


def prepare_bulk(request, case, container_case):
    identity = request["identity"]
    build = request["build"]
    data = base64.b64decode(request["archive"], validate=True)
    assert hashlib.sha256(data).hexdigest() == identity["input_archive_sha256"]
    assert identity["tokens"] == request["tokens"] == 32
    assert identity["hidden"] == request["hidden"] and identity["hidden"] in (16, 256)
    template = MOUNT / "t4-sst-mte-bulk-forward-hotpath-1a2f3549"
    template_manifest = json.loads((template / "case-manifest.json").read_text())
    assert template_manifest["kernel_sha256"].startswith("1a2f3549")
    assert json.loads((template / "run.json").read_text())["status"] == "sst_and_checker_passed"
    binary = gzip.decompress(base64.b64decode(request["object"], validate=True))
    assert hashlib.sha256(binary).hexdigest() == build["kernel_sha256"]
    assert build["entry"] == identity["entry"] == "tilexr_ep_ht_combine_sst_fp32"
    assert build["compiled_in"] == "blue"

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
    assert identity["runtime_root"] + "/" in text
    workload.write_text(text.replace(identity["runtime_root"], container_case))
    chips = json.loads(workload.read_text())["workload_config"]
    assert len(chips) == 64 and all(chip["block_dim"] == 16 and chip["vec_func"] == build["entry"]
                                  and chip["kernel_file"] == container_case + "/kernel/inter_server_send.o"
                                  for chip in chips.values())
    fixture = case / "fixture-data"
    fixture_manifest = json.loads((fixture / "manifest.json").read_text())
    assert fixture_manifest["tokens"] == 32 and fixture_manifest["hidden"] == request["hidden"] and fixture_manifest["chunks"] == 4

    adapter = "adapter/run.py"
    exporter = "adapter/exports.py"
    checker = "source/tools/unitest/simulator/experiments/inter_server_send/fixture.py"
    replacements = {exporter: request["exports"], checker: request["checker"]}
    assert {adapter, *replacements}.issubset(template_manifest["files"])
    for name, expected in template_manifest["files"].items():
        rel = Path(name)
        assert not rel.is_absolute() and ".." not in rel.parts
        if rel.parts[0] in {"input_bin", "fixture-data"} or name in {
                "workload.yml", "export-layout.json", "blue-build-record.json"}:
            continue
        source, target = template / rel, case / rel
        assert sha(source) == expected, name
        target.parent.mkdir(parents=True, exist_ok=True)
        if name in replacements:
            target.write_text(replacements[name])
        elif name == adapter:
            original = source.read_text()
            old_entry = 'manifest["entry"] != "tilexr_ep_ht_inter_server_send_kernel"'
            assert original.count(old_entry) == 1
            target.write_text(original.replace(old_entry, 'manifest["entry"] != "tilexr_ep_ht_combine_sst_fp32"'))
        elif name == "v310_config/workload_config.yml":
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
        entry=build["entry"],
        checker_sha256=sha(case / checker),
        candidate_commit=build["candidate_commit"],
        kernel_sha256=build["kernel_sha256"],
        input_archive_sha256=identity["input_archive_sha256"],
        fixture_files={str(p.relative_to(fixture)): sha(p) for p in sorted(fixture.rglob("*")) if p.is_file()},
        files={str(p.relative_to(case)): sha(p) for p in sorted(case.rglob("*")) if p.is_file()},
    )
    write_json(case / "case-manifest.json", manifest)
    return build
