"""Stage a blue-prepared T8 fixture beside an independently verified T4 run."""

import tarfile
from pathlib import PurePosixPath


def prepare_t8(request, case, container_case):
    build = request["build"]
    t4 = case.with_name(case.name.replace("t8-", "t4-", 1))
    qualification = json.loads((t4 / "run.json").read_text())
    assert qualification["status"] == "sst_and_checker_passed"
    assert qualification["kernel_sha256"] == build["kernel_sha256"]
    manifest = json.loads((t4 / "case-manifest.json").read_text())
    identity = request["identity"]
    archive = MOUNT / ("t8-inputs-" + identity["archive_sha256"][:12] + ".tar.gz")
    assert sha(archive) == identity["archive_sha256"]
    assert identity["kernel_sha256"] == manifest["kernel_sha256"] == build["kernel_sha256"]

    case.mkdir(exist_ok=False)
    with tarfile.open(archive) as stream:
        members = stream.getmembers()
        assert len(members) <= 4096 and sum(row.size for row in members) <= 512 * 1024 * 1024
        for member in members:
            rel = PurePosixPath(member.name)
            assert member.isfile() and not rel.is_absolute() and ".." not in rel.parts
            assert rel.parts[0] in {"workload.yml", "export-layout.json", "input_bin", "fixture-data"}
            dest = case / member.name
            dest.parent.mkdir(parents=True, exist_ok=True)
            with dest.open("xb") as out:
                shutil.copyfileobj(stream.extractfile(member), out)

    fixture = case / "fixture-data"
    assert json.loads((fixture / "manifest.json").read_text()) == identity["fixture_manifest"]
    assert identity["fixture_manifest"]["tokens"] == 8
    for name, expected in manifest["files"].items():
        if name == "v310_config/workload_config.yml":
            dest = case / name
            dest.parent.mkdir(exist_ok=True)
            shutil.copyfile(case / "workload.yml", dest)
            continue
        if name in {"workload.yml", "export-layout.json"} or name.startswith(("input_bin/", "fixture-data/")):
            continue
        src, dest = t4 / name, case / name
        assert sha(src) == expected, name
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dest)

    workload = json.loads((case / "workload.yml").read_text())["workload_config"]
    assert len(workload) == 64
    assert all(chip["kernel_file"] == container_case + "/kernel/inter_server_send.o"
               and chip["vec_func"] == build["entry"] and chip["block_dim"] == 16
               for chip in workload.values())
    manifest.update(
        fixture_files={str(p.relative_to(fixture)): sha(p) for p in sorted(fixture.rglob("*")) if p.is_file()},
        files={str(p.relative_to(case)): sha(p) for p in sorted(case.rglob("*")) if p.is_file()},
    )
    write_json(case / "case-manifest.json", manifest)
    return build
