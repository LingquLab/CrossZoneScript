# Copyright (c) 2026 TileXR Project.
"""Record Python model connections with an SST construction stub.

Executes model setup in a fresh report directory without loading SST models or
running events. Stub results describe Python wiring, not simulator execution.
"""

from collections import Counter
from contextlib import redirect_stdout, redirect_stderr
import json
import os
from pathlib import Path
import re
import runpy
import shutil
import sys
from tempfile import mkdtemp
import types

import sst as runner


def main():
    case = Path(json.loads((runner.WORK / "progress.json").read_text())["case"])
    assert case.parent == runner.WORK / "cases"
    baseline = case / "send-only"
    snapshot = json.loads((baseline / "environment.snapshot.json").read_text())
    runner.verify_environment(snapshot)
    report = Path(mkdtemp(prefix="graph-focus-", dir=case))
    for path in baseline.iterdir():
        if path.name in ("v310_config", "UBusSim_config"):
            shutil.copytree(path, report / path.name)
        elif path.name in ("installed_model.py", "test_UBusSim.py", "workload.yml", "environment.snapshot.json"):
            shutil.copyfile(path, report / path.name)
    nodes, links = {}, {}

    class Node:
        def __init__(self, name, kind):
            self.name, self.kind, self.ports, self.params = name, kind, [], {}
            nodes[name] = self

        def addParams(self, *args):
            self.params.update(args[0])
            return None

        def addParam(self, *args):
            self.params[args[0]] = args[1]
            return None

        def setSubComponent(self, name, kind, *args):
            return Node(self.name + ":" + name, kind)

        def addLink(self, link, port, *args):
            self.ports.append(str(port))

        def __getattr__(self, name):
            return lambda *args, **kwargs: None

    class Link:
        def __init__(self, name, *args):
            self.name = name
            links[name] = self

        def connect(self, *endpoints):
            for endpoint in endpoints:
                endpoint[0].ports.append(str(endpoint[1]))

        def setNoCut(self):
            return None

    stub = types.ModuleType("sst")
    stub.Component, stub.Link = Node, Link
    stub.__getattr__ = lambda name: lambda *args, **kwargs: None
    previous = sys.modules["sst"]
    sys.modules["sst"] = stub
    result = dict(report=str(report), method="Python SST construction stub; no events executed")
    cwd = Path.cwd()
    try:
        os.chdir(report)
        with (report / "construction.log").open("w") as log, redirect_stdout(log), redirect_stderr(log):
            runpy.run_path(str(report / "test_UBusSim.py"), run_name="__main__")
        result["constructed"] = True
    except Exception as error:
        result.update(constructed=False, error_class=type(error).__name__, detail=str(error)[:300])
    finally:
        sys.modules["sst"] = previous
        os.chdir(cwd)
    bas = [node for node in nodes.values() if "ba_layer" in node.name.lower()]
    groups = Counter()
    examples = {}
    for node in bas:
        match = re.search(r"Host(\d+)_", node.name)
        if not match or int(match[1]) >= 8:
            continue
        signature = tuple(sorted(node.ports))
        groups[signature] += 1
        examples.setdefault(signature, [])
        if len(examples[signature]) < 3:
            examples[signature].append(node.name)
    result.update(component_count=len(nodes), link_count=len(links), ba_component_count=len(bas),
                  first8_ba_port_groups=[dict(ports=list(key), count=count, examples=examples[key]) for key, count in groups.items()])
    result["ba_params"] = []
    for name in ("Host0_Udie0_Body0_ba_layer", "Host0_Udie0_Body3_ba_layer", "Host0_Udie0_Body4_ba_layer"):
        if name in nodes:
            node = nodes[name]
            selected = {key: value for key, value in node.params.items()
                        if re.search(r"flag|mode|port|body|die|station|chip|host|ccum|ba_.*id", key, re.I)
                        and isinstance(value, (int, float, bool, str))}
            result["ba_params"].append(dict(name=name, kind=node.kind, params=selected))
    runner.write_json(report / "analysis.json", result)
    assert len(json.dumps(result).encode()) < 8000
    print(json.dumps(result, ensure_ascii=True))


if __name__ == "__main__":
    main()
