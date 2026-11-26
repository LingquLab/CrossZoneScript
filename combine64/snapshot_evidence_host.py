# Copyright (c) 2026 TileXR Project.
"""Read saved terminal diagnostics and bounded activity fields without running SST."""
import json
import re
from pathlib import Path


def main():
    root=Path('/mnt/share/tilexr-sim-jobs/59af0a93-b62f-4211-927e-a44f047667cf/run-b61cb53-a1')
    identity=json.loads((root/'identity.json').read_text())
    assert identity['revision']=='b61cb530b0d25d1e9ad18748ee7aeb7a7962740e'
    case=root/'cases/t32-joint-reuse-state-snapshot-a1'
    result={}
    for name in ('diagnostic.json','diagnostic-result.json'):
        path=case/name
        if path.is_file():
            raw=json.loads(path.read_text())
            result[name]={key:raw[key] for key in ('status','stop_reason','sst_wall_seconds','returncode','started_unix','finished_unix','stop_at_us','pid') if key in raw}
    log=case/'sim.log'
    if log.is_file():
        with log.open('rb') as f:
            f.seek(max(0,log.stat().st_size-262144))
            tail=f.read().decode(errors='replace')
        ticks=re.findall(r'Sim: ([0-9]+)[(]cycle[)]',tail)
        result.update(last_logged_sim_cycle=int(ticks[-1]) if ticks else None,
                      completion_marker='Simulation is complete' in tail,
                      error_classes=[s for s in ('AssertionError','Traceback','Segmentation fault','FATAL') if s in tail])
    result['export_files']=sum(1 for p in (case/'bin').rglob('*') if p.is_file())
    errors=[]
    for path in root.glob('snapshot-joint-state-*.stderr'):
        errors.extend(path.read_text(errors='replace').splitlines()[-6:])
    result['helper_error_tail']=[line[:300] for line in errors][-6:]
    raw=json.dumps(result,separators=(',',':'))
    assert len(raw.encode())<=4096
    print(raw)
    return 0
