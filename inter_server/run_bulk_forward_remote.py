"""Install blue-built aligned inputs in clean cases and run the T4/T8 matrix."""

from io import BytesIO
import tarfile


def prepare_bulk(request, case, container_case):
    tokens = request["tokens"]
    baseline = case.with_name(f"t{tokens}-sst-mte-scalar-forward-5a6c7c8c")
    manifest = json.loads((baseline / "case-manifest.json").read_text())
    assert json.loads((baseline / "run.json").read_text())["status"] == "sst_and_checker_passed"
    build = request["build"]
    assert build["candidate_commit"] == manifest["candidate_commit"]
    assert build["entry"] == manifest["entry"]
    binary = gzip.decompress(base64.b64decode(request["object"], validate=True))
    assert hashlib.sha256(binary).hexdigest() == build["kernel_sha256"]

    source_case = str(Path(container_case).with_name(baseline.name))
    case.mkdir(exist_ok=False)
    files = request["layout"]["files"]
    forward_images = request["forward_images"]
    changed_forward = set()
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
        elif relative.parts[0] == "input_bin" and relative.name == "forward.bin":
            key = f"t{tokens}/{relative.parent.name}/forward.bin"
            assert expected_sha == files[key]["packed_sha256"], name
            aligned = forward_images[key]
            assert len(aligned) * 3 == source.stat().st_size * 4
            target.write_bytes(aligned)
            changed_forward.add(key)
        elif name != "blue-build-record.json":
            shutil.copyfile(source, target)
    assert len(changed_forward) == 64
    if "profile" in request:
        stage_profile(request, case, container_case)
    write_json(case / "blue-build-record.json", build)
    manifest.update(
        variant=build["variant"],
        kernel_sha256=build["kernel_sha256"],
        scalar_baseline_kernel_sha256=manifest["kernel_sha256"],
        forward_record_stride=32,
        files={str(p.relative_to(case)): sha(p) for p in sorted(case.rglob("*")) if p.is_file()},
    )
    write_json(case / "case-manifest.json", manifest)
    return build


def run_bulk(request):
    data = base64.b64decode(request["forward_images"], validate=True)
    layout = request["layout"]
    assert hashlib.sha256(data).hexdigest() == layout["archive_sha256"]
    images = {}
    with tarfile.open(fileobj=BytesIO(data), mode="r:gz") as archive:
        for member in archive:
            assert member.isfile() and member.name not in images
            assert member.name in layout["files"] and member.size <= 128 * 1024
            image = archive.extractfile(member).read()
            assert hashlib.sha256(image).hexdigest() == layout["files"][member.name]["aligned_sha256"]
            images[member.name] = image
    assert len(images) == 128 and images.keys() == layout["files"].keys()
    request["forward_images"] = images
    result = {}
    for tokens in ((4,) if "profile" in request else (4, 8)):
        row = main(dict(request, tokens=tokens))
        result[f"t{tokens}"] = row
        if row.get("run", {}).get("status") != "sst_and_checker_passed":
            break
    return result
