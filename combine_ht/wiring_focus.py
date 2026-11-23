# Copyright (c) 2026 TileXR Project.
"""Report BA-to-bus connection guards from the installed model entry.

Reads the exact baseline configuration and model without starting SST. Complete
analysis stays in a fresh session report; stdout contains a bounded summary.
"""

import ast
import json
from pathlib import Path
from tempfile import mkdtemp

import sst


def main():
    case = Path(json.loads((sst.WORK / "progress.json").read_text())["case"])
    assert case.parent == sst.WORK / "cases"
    snapshot = json.loads((case / "send-only/environment.snapshot.json").read_text())
    sst.verify_environment(snapshot)
    source = Path(snapshot["resources"]["installed_model.py"]["path"])
    tree = ast.parse(source.read_text())
    parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
    records = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "connect"):
            continue
        identifiers = sorted({part.id for part in ast.walk(node) if isinstance(part, ast.Name)} |
                             {part.attr for part in ast.walk(node) if isinstance(part, ast.Attribute)})
        if "ba_layer_dict" not in identifiers:
            continue
        guards, loops = [], []
        parent = node
        while parent in parents:
            child, parent = parent, parents[parent]
            if isinstance(parent, ast.If):
                guards.append(dict(line=parent.lineno, test=ast.unparse(parent.test), branch="else" if child in parent.orelse else "then"))
            if isinstance(parent, ast.For):
                loops.append(dict(line=parent.lineno, variable=ast.unparse(parent.target), iterable=ast.unparse(parent.iter)))
        records.append(dict(line=node.lineno, identifiers=identifiers, guards=guards, loops=loops))
    report = Path(mkdtemp(prefix="wiring-focus-", dir=case))
    configs = sst.yaml.safe_load((case / "send-only/UBusSim_config/UBusSim_simulator_config.yml").read_text())
    result = dict(report=str(report), source=str(source), source_sha256=sst.checksum(source),
                  connections=records, flags=configs["simulator_info"], host_num=configs["architecture_config"]["host_num"])
    sst.write_json(report / "inspection.json", result)
    assert len(json.dumps(result).encode()) < 8000
    print(json.dumps(result, ensure_ascii=True))


if __name__ == "__main__":
    main()
