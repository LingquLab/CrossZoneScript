# Copyright (c) 2026 TileXR Project.
"""Read BA link setup guards and installed SST component-name interfaces.

Uses saved native analysis and installed headers only; simulator events are not
run and baseline input or model files are not changed.
"""

import json
from pathlib import Path
import re
from tempfile import mkdtemp

import sst


def main():
    case = Path(json.loads((sst.WORK / "progress.json").read_text())["case"])
    assert case.parent == sst.WORK / "cases"
    report = Path(mkdtemp(prefix="link-context-", dir=case))
    sources = sorted(case.glob("native-link-*/library.asm"), key=lambda path: path.stat().st_mtime)
    lines = sources[-1].read_text().splitlines()
    functions = {}
    current = None
    for line in lines:
        match = re.match(r"^[0-9a-f]+ <([^>]+)>:", line)
        if match:
            current = match[1]
            functions[current] = []
        if current and re.match(r"^\s*[0-9a-f]+:\s+[0-9a-f]{8}\s", line):
            functions[current].append(line.strip())
    build = functions["_ZN3SST7Davinci7BaLayer18buildBaAndBusLinksEv"]
    writes = [index for index, line in enumerate(build) if "#4120]" in line]
    result = dict(report=str(report), setup_head=build[:8], setup_writes=[build[max(0, index-5):index+1] for index in writes[:1]], headers=[])
    for path in (Path("/opt/sim/local/sstcore/include/sst/core/baseComponent.h"), Path("/opt/sim/local/sstcore/include/sst/core/component.h"), Path("/opt/sim/local/sstcore/include/sst/core/componentInfo.h")):
        if path.is_file():
            text = path.read_text()
            selected = [dict(line=number, text=line.strip()) for number, line in enumerate(text.splitlines(), 1)
                        if re.search(r"getName\(|ComponentInfo\*|ComponentInfo \*", line)]
            result["headers"].append(dict(path=str(path), matches=selected[:3]))
    ctor = functions.get("_ZN3SST7Davinci7BaLayerC1EmRNS_6ParamsE", [])
    calls = [index for index, line in enumerate(ctor) if "buildBaAndBusLinks" in line]
    result["constructor_setup_calls"] = [ctor[max(0,index-4):index+1] for index in calls[:1]]
    sst.write_json(report / "analysis.json", result)
    print(json.dumps(result, ensure_ascii=True))


if __name__ == "__main__":
    main()
