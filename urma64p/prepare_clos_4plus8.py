"""Prepare a 16P Full SST case using the candidate's native 7 KiB templates."""

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import yaml

RANKS = 16
ENTRY = "tilexr_alltoall_urma_jetty_peers_full_kernel"


def digest(path):
    checksum = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            checksum.update(block)
    return checksum.hexdigest()


def read_json(path):
    return json.loads(path.read_text())


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2) + "\n")


def preflight(root):
    subprocess.run([sys.executable, "-B", str(root / "case_tools.py"), "preflight",
                    "--case-dir", str(root)], check=True, cwd=root, timeout=80)


def rewrite_checker(path, senders, workers):
    source = path.read_text()
    replacements = {
        "23 * 64 * 12": f"{senders} * 64 * 12",
        "range(23)": f"range({senders})",
        "1472": str(senders * 64),
        "compute_layout(31, 256)": f"compute_layout({workers}, 256)",
        "parse_view(profile), 31, 256": f"parse_view(profile), {workers}, 256",
        "range(31)": f"range({workers})",
        "lane >= 23": f"lane >= {senders}",
        "31 streams": f"{workers} streams",
    }
    for old, new in replacements.items():
        if old not in source:
            raise ValueError(f"missing Full checker anchor {old}")
        source = source.replace(old, new)
    compile(source, str(path), "exec")
    path.write_text(source)


def patch_workload(path, records, candidate, workers):
    doc = yaml.safe_load(path.read_text())
    chips = doc["workload_config"]
    assert set(chips) == {f"chip{rank}" for rank in range(RANKS)}
    for rank, info in enumerate(records):
        chip = chips[f"chip{rank}"]
        assert chip["block_dim"] == 31 and Path(chip["kernel_file"]).stem == ENTRY
        chip["block_dim"] = workers
        chip["kernel_file"] = str(candidate)
        params = chip["kernel_param"].values()
        scalars = {int(str(p["param_offset"]), 0): p
                   for p in params if "param_offset" in p and "input_bin" not in p}
        for offset, key in ((96, "progress_offset"), (104, "tokens_offset")):
            field = scalars[offset]
            field["value"] = str(info[key]) if isinstance(field["value"], str) else info[key]
    path.write_text(yaml.safe_dump(doc, sort_keys=False))


def prepare(reference, case, kernel, revision, kind, die0):
    assert die0 in (4, 8)
    senders = 7 + die0 + 8
    workers = senders + 8
    manifest = read_json(reference / "manifest.json")
    scenario = read_json(reference / "scenario.json")
    assert scenario["rank_size"] == RANKS and scenario["local_rank_size"] == 8
    assert scenario["block_dim"] == 31 and scenario["copyout_enabled"] == (kind == "functional")
    preflight(reference)
    original = reference / "kernel" / (ENTRY + ".o")
    assert digest(original) == scenario["kernel"]["sha256"]
    paths = set(manifest["files"])
    paths.update(("scenario.json", "full_validation.py", "case_tools.py", "profiling_view.py",
                  "workload.yml", "v310_config/workload_config.yml", "test_UBusSim.py",
                  "environment.snapshot.json", str(original.relative_to(reference))))
    case.mkdir()
    for name in sorted(paths):
        source = reference / name
        if not source.is_file() or not source.resolve().is_relative_to(reference.resolve()):
            raise ValueError(f"invalid reference file {name}")
        target = case / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    candidate = case / original.relative_to(reference)
    shutil.copyfile(kernel, candidate)
    scenario = json.loads(json.dumps(scenario).replace(str(reference), str(case)))
    for path in [case / "workload.yml", case / "test_UBusSim.py",
                 *(case / "v310_config").rglob("*.yml"), *(case / "UBusSim_config").rglob("*.yml")]:
        source = path.read_text()
        modified = source.replace(scenario["kernel"]["original_path"], str(candidate))
        modified = modified.replace(str(reference), str(case))
        if source != modified:
            path.write_text(modified)

    workload = yaml.safe_load((case / "workload.yml").read_text())["workload_config"]
    images = []
    for rank, record in enumerate(scenario["ranks"]):
        rank_dir = case / "input_bin" / f"rank{rank}"
        eid = [p for p in workload[f"chip{rank}"]["kernel_param"].values()
               if Path(p.get("input_bin", "")).name == "eid_table.bin"]
        assert record["rank"] == rank and len(eid) == 1
        result = subprocess.check_output(["/output/prepare_images", str(rank_dir), str(rank),
                                          str(eid[0]["value"])], text=True, timeout=20)
        image = json.loads(result)
        workspace = next(region for region in record["regions"] if region["name"] == "workspace")
        assert image["workspace_bytes"] <= workspace["size"]
        workspace["size"] = image["workspace_bytes"]
        images.append(image)
    assert len(images) == RANKS and all(image == images[0] for image in images)
    image = images[0]
    assert image["status_bytes"] == senders * 64 * 12
    layout = scenario["layout"]
    assert layout["prefill"]["parts"] == 14 and layout["status_bytes"] == 23 * 64 * 12
    layout.update(status_bytes=image["status_bytes"], workspace_bytes=image["template_offset"])
    layout["prefill"].update(parts=1, template_offset=image["template_offset"],
                            template_bytes=image["template_bytes"],
                            wqes_per_rank=(RANKS - 1) * layout["repeat"],
                            workspace_bytes=image["prefill_end"])
    layout["prefill"]["progress"].update(
        **{key: image[key] for key in ("progress_offset", "tokens_offset", "workspace_bytes")})
    candidate_hash = digest(candidate)
    scenario.update(block_dim=workers, profiling_streams=workers,
                    profiling_bytes=image["profile_bytes"], candidate_revision=revision)
    scenario["kernel"].update(original_path=str(candidate), sha256=candidate_hash)
    scenario["kernel"]["manifest"].update(sha256=candidate_hash, copyout=scenario["copyout_enabled"])
    scenario["clos_routing"].update(destinations=2, fullmesh_blocks=7, clos_blocks=die0 + 8,
                                   source=f"rank_die_rotation_v2, Clos {die0}+8")
    scenario[f"clos_{die0}plus8"] = dict(die0=die0, die1=8, grain=32, wqe_bytes=7168)
    for name in ("workload.yml", "v310_config/workload_config.yml"):
        patch_workload(case / name, images, candidate, workers)
    rewrite_checker(case / "full_validation.py", senders, workers)
    write_json(case / "scenario.json", scenario)
    manifest.update(source_commit=revision, files={name: digest(case / name) for name in sorted(paths)})
    write_json(case / "manifest.json", manifest)
    preflight(case)
    print(json.dumps(dict(stage="prepared", rank_size=RANKS, senders=senders, block_dim=workers,
                          die0=die0, die1=8, destinations=2, wqe_bytes=7168, grain=32,
                          bytes_per_peer=layout["bytes_per_peer"], repeat=layout["repeat"],
                          parts=1, **image)), flush=True)


if __name__ == "__main__":
    reference, output, kernel_path, revision, kind, *geometry = sys.argv[1:]
    prepare(Path(reference), Path(output), Path(kernel_path), revision, kind,
            int(geometry[0]) if geometry else 4)
