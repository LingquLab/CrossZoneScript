# Copyright (c) 2026 TileXR Project.
"""Read bounded live MTE request details and scalar pair-load operands."""
import json
from pathlib import Path

CASE=Path('/work/cases/t32-joint-reuse-snapshot-a1')


def tail(path,budget=16384):
    with path.open('rb') as f:
        f.seek(max(0,path.stat().st_size-budget))
        return f.read().decode(errors='replace').splitlines()


def main():
    result=dict(status='joint_mte_operand_read',cores=[])
    for core,aiv in ((12,0),(0,0),(0,1)):
        marker=f'_0_aicore{core}_aiv{aiv}_'
        paths=sorted(p for p in (CASE/'dumps').rglob('*') if p.is_file() and marker in p.name)
        row=dict(physical_core=2*core+aiv,files=[dict(name=p.name,bytes=p.stat().st_size) for p in paths])
        excerpts=[]
        for p in paths:
            lines=tail(p)
            if 'mte' in p.name.lower():
                excerpts.extend(dict(file=p.name,line=line[:500]) for line in lines[-2:])
            elif core==0 and aiv==1 and p.name.endswith('_sudetail.dump'):
                loads=[line for line in lines if 'scalarLdp' in line]
                if loads:excerpts.append(dict(file=p.name,line=loads[-1][:500]))
        row['excerpts']=excerpts[:2]
        result['cores'].append(row)
    path=CASE/'diagnostic-result.json'
    if path.is_file():
        data=json.loads(path.read_text())
        result['snapshot_status']={key:data[key] for key in ('status','stop_reason','sst_wall_seconds','role_status','ready','pending_queue_count','field_comparison') if key in data}
    (CASE/'mte-operand-read.json').write_text(json.dumps(result,indent=2)+'\n')
    raw=json.dumps(result,separators=(',',':'))
    if len(raw.encode())>4096:
        for row in result['cores']:row['files']=[entry['name'] for entry in row['files']]
        raw=json.dumps(result,separators=(',',':'))
    assert len(raw.encode())<=4096
    print(raw)
    return 0


if __name__=='__main__':
    raise SystemExit(main())
