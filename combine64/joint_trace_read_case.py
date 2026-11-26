# Copyright (c) 2026 TileXR Project.
"""Map current rank-0 scalar trace PCs to functions in the exact simulated ELF."""
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import subprocess

ROOT=Path('/work')
CASE=ROOT/'cases/t32-joint-reuse-snapshot-a1'


def main():
    record=json.loads((CASE/'diagnostic.json').read_text())
    assert record['candidate_commit']=='b61cb530b0d25d1e9ad18748ee7aeb7a7962740e'
    obj=ROOT/'profile-build/tilexr_ht_pipeline_sst.o'
    assert hashlib.sha256(obj.read_bytes()).hexdigest()=='5f2cfe747b3f34c8696987219e0e8d161d87279d487a281d326651d94c669835'
    p=subprocess.run(['readelf','-W','-s','-C',str(obj)],capture_output=True,timeout=20)
    assert p.returncode==0
    functions=[]
    for line in p.stdout.decode().splitlines():
        fields=line.split(None,7)
        if len(fields)==8 and fields[3]=='FUNC' and fields[6]!='UND':
            start,size=int(fields[1],16),int(fields[2])
            if size:functions.append((start,start+size,fields[7]))
    samples=[]
    counts=Counter()
    for path in sorted((CASE/'dumps').rglob('*sudetail.dump')):
        with path.open('rb') as stream:
            stream.seek(max(0,path.stat().st_size-16384))
            tail=stream.read().decode(errors='replace')
        matches=list(re.finditer(r'PC:\s*(0x[0-9a-fA-F]+|[0-9]+)',tail))
        row=dict(file=path.name,bytes=path.stat().st_size)
        if matches:
            token=matches[-1][1]
            pc=int(token,16 if token.startswith('0x') else 10)
            row['pc']=pc
            found=[]
            for candidate in (pc,pc-0x40000000):
                found.extend(name for start,end,name in functions if start<=candidate<end)
            if found:
                row['function']=min(found,key=len).split('(',1)[0][:120]
                counts[row['function']]+=1
            else:counts['unmapped_pc']+=1
            line=tail[matches[-1].start():].splitlines()[0]
            op=re.search(r'InstrName:\s*([^],]+)',line)
            if op:row['instruction']=op[1].strip()[:80]
        else:
            counts['no_pc_in_tail']+=1
        samples.append(row)
    full=dict(status='live_scalar_trace_read',elf_sha256=hashlib.sha256(obj.read_bytes()).hexdigest(),
              function_histogram=dict(counts),samples=samples)
    (CASE/'live-trace-read.json').write_text(json.dumps(full,indent=2)+'\n')
    compact=dict(status=full['status'],trace_files=len(samples),function_histogram=dict(counts.most_common(12)),samples=samples[:8])
    raw=json.dumps(compact,separators=(',',':'))
    if len(raw.encode())>4096:
        compact['samples']=samples[:4]
        raw=json.dumps(compact,separators=(',',':'))
    assert len(raw.encode())<=4096
    print(raw)
    return 0


if __name__=='__main__':
    raise SystemExit(main())
