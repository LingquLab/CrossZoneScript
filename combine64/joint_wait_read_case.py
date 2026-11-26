# Copyright (c) 2026 TileXR Project.
"""Correlate pending scalar loads with completed register values in rank-0 traces."""
import json
from pathlib import Path
import re
import struct

ROOT=Path('/work')
CASE=ROOT/'cases/t32-joint-reuse-snapshot-a1'
CORES=((0,1),(10,0),(12,0),(0,0))


def main():
    raw=(ROOT/'profile-build/tilexr_ht_pipeline_sst.o').read_bytes()
    offset=struct.unpack_from('<Q',raw,40)[0]
    width,count,names=struct.unpack_from('<HHH',raw,58)
    sections=[struct.unpack_from('<IIQQQQIIQQ',raw,offset+i*width) for i in range(count)]
    string_section=sections[names]
    strings=raw[string_section[4]:string_section[4]+string_section[5]]
    text=None
    for section in sections:
        name=strings[section[0]:strings.find(bytes([0]),section[0])]
        if name==b'.text': text=raw[section[4]:section[4]+section[5]]
    assert text is not None
    result=[]
    for core,aiv in CORES:
        stem=f'SU_David_V100_0_aicore{core}_aiv{aiv}'
        paths=list((CASE/'dumps').rglob(stem+'_su.dump'))
        assert len(paths)==1
        pending={}
        for line in paths[0].open(errors='replace'):
            identity=re.search(r'InstrID: ([0-9]+)',line)
            pc=re.search(r'PC: ([0-9]+)',line)
            if not identity or not pc:continue
            index=int(identity[1])
            if 'Retire Instruction' in line:pending.pop(index,None)
            elif 'Popped Instruction' in line:
                name=line.split('InstrName:',1)[1].split(']',1)[0].strip()
                pending[index]=dict(instruction_id=index,pc=int(pc[1]),name=name)
        registers={}
        details=list((CASE/'dumps').rglob(stem+'_sudetail.dump'))
        assert len(details)==1
        for line in details[0].open(errors='replace'):
            match=re.search(r'xd = x([0-9]+), xdValue = ([-0-9]+)',line)
            if match:registers[int(match[1])]=int(match[2])
        loads=[]
        for item in sorted(pending.values(),key=lambda x:x['instruction_id']):
            if 'scalarLd' not in item['name']:continue
            entry=dict(item)
            at=entry['pc']-0x40000000
            if 0<=at<=len(text)-4:
                word=struct.unpack_from('<I',text,at)[0]
                entry['relative_pc']=hex(at)
                if word>>24==0x1c:
                    register=(word>>12)&31;imm=word&4095
                    entry.update(base_register=register,immediate=imm,last_completed_base=registers.get(register))
                    if register in registers:entry['derived_address']=registers[register]+imm
            loads.append(entry)
        result.append(dict(physical_core=core*2+aiv,pending_instruction_count=len(pending),
                           pending_load_count=len(loads),pending_loads=loads[:2],
                           oldest_pending=list(sorted(pending.values(),key=lambda x:x['instruction_id']))[:1]))
    full=dict(status='live_pending_scalar_reads',cores=result)
    (CASE/'pending-scalar-read.json').write_text(json.dumps(full,indent=2)+'\n')
    data=json.dumps(full,separators=(',',':'))
    assert len(data.encode())<=4096
    print(data)
    return 0


if __name__=='__main__':
    raise SystemExit(main())
