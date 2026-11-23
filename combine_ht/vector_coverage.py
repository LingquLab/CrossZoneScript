# Copyright (c) 2026 TileXR Project.
"""Inspect installed vector instruction coverage and matching configuration.

Reads model resources and Ascend C MicroAPI headers without modifying the model
or running events. Keeps full search results in the session report.
"""

import json
from pathlib import Path
import re
import shutil
import subprocess
from tempfile import mkdtemp

import sst


def main():
    case = Path(json.loads((sst.WORK / "progress.json").read_text())["case"])
    assert case.parent == sst.WORK / "cases"
    snapshot = json.loads((case / "send-only/environment.snapshot.json").read_text())
    sst.verify_environment(snapshot)
    report = Path(mkdtemp(prefix="vector-coverage-", dir=case))
    result = dict(report=str(report), config_matches=[], native_strings={}, api_paths=[])

    def collect(node, path, source):
        if isinstance(node, dict):
            for key, value in node.items():
                here = path + "/" + str(key)
                if re.search(r"scatter|gather", str(key), re.I):
                    result["config_matches"].append(dict(source=source, path=here, value=str(value)[:350]))
                collect(value, here, source)
        elif isinstance(node, list):
            for index, value in enumerate(node):
                collect(value, path + "/" + str(index), source)
        elif isinstance(node, str) and re.search(r"scatter|gather", node, re.I):
            result["config_matches"].append(dict(source=source, path=path, value=node[:350]))

    config_root = sst.ROOT / "simulator/v310_deployment/config"
    files = sorted(config_root.rglob("*.yml"))
    result["config_file_count"] = len(files)
    for path in files:
        collect(sst.yaml.safe_load(path.read_text()), "", str(path))
    for name in ("libDavinciSimulatorV310.so", "libDavinciSimulator.so"):
        path = Path(snapshot["settings"]["library_dir"]) / name
        data = path.read_bytes()
        result["native_strings"][name] = {term: data.count(term.encode()) for term in ("vectorVscatter", "vectorVgather", "vectorVst", "vectorScatter")}
    cann = Path(snapshot["settings"]["cann_root"])
    roots = [cann / "compiler/tikcpp/tikcfw", cann / "aarch64-linux/ascendc/include"]
    for root in roots:
        for path in root.rglob("*.h"):
            if re.search(r"scatter|gather|data_copy|datacopy|interleave|pack", path.name, re.I) and "micro" in str(path).lower():
                result["api_paths"].append(str(path))
    result["api_paths"] = result["api_paths"][:24]
    sst.write_json(report / "analysis.json", result)
    result["config_matches"] = result["config_matches"][:12]
    print(json.dumps(result, ensure_ascii=True))


if __name__ == "__main__":
    main()
