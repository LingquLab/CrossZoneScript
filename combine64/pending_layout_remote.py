# Copyright (c) 2026 TileXR Project.
"""Summarize pending fault-core loads and debug type layouts without rerunning SST."""
import json,re,struct,subprocess
from pathlib import Path
ROOT=Path('/mnt/share/tilexr-sim-jobs/fac4ae28-1586-42fe-b55b-ff09982f17ec/run-40596e2-a1')
trace=ROOT/'cases/64p-t4-pipeline-trace-a1/dumps';pending={};registers={};field_access=[]
for line in (trace/'SU_David_V100_32_aicore3_aiv0_su.dump').open(errors='replace'):
    identity=re.search('InstrID: ([0-9]+)',line)
    pc=re.search('PC: ([0-9]+)',line)
    if not identity or not pc:continue
    index=int(identity[1])
    if 'Retire Instruction' in line:pending.pop(index,None)
    elif 'Popped Instruction' in line and 'scalarLd' in line:pending[index]=dict(instruction_id=index,pc=int(pc[1]),name=line.split('InstrName:')[1].split(']')[0].strip())
for line in (trace/'SU_David_V100_32_aicore3_aiv0_sudetail.dump').open(errors='replace'):
    match=re.search('xd = x([0-9]+), xdValue = ([-0-9]+)',line)
    if match:registers[int(match[1])]=int(match[2])
    if any(name in line for name in ('scalarStImm','scalarLdImm')):
        address=re.search('xnValue = ([-0-9]+)',line);imm=re.search('imm12 = ([-0-9]+)',line)
        if address and imm and int(address[1])+int(imm[1])==4398048964576+72:field_access.append(line.strip()[:650])
raw=(ROOT/'pipeline-build-40596e2-a1/kernel.o').read_bytes();offset=struct.unpack_from('<Q',raw,40)[0];size,count,names=struct.unpack_from('<HHH',raw,58)
sections=[struct.unpack_from('<IIQQQQIIQQ',raw,offset+i*size) for i in range(count)];s=sections[names];strings=raw[s[4]:s[4]+s[5]]
text=None
for s in sections:
    if strings[s[0]:strings.find(bytes([0]),s[0])]==b'.text':text=raw[s[4]:s[4]+s[5]]
assert text is not None
base=32*0x2000000000+0x40000000;loads=[]
for item in pending.values():
    at=item['pc']-base
    if not 0<=at<=len(text)-4:continue
    word=struct.unpack_from('<I',text,at)[0];item.update(offset=hex(at),raw=hex(word))
    if word>>24==0x1c:
        xn=(word>>12)&31;imm=word&4095;item.update(base_register=xn,immediate=imm,last_completed_base_value=registers.get(xn))
        if xn in registers:item['derived_address']=registers[xn]+imm
    loads.append(item)
p=subprocess.run(['readelf','--debug-dump=info',str(ROOT/'pipeline-debug-build-a1/kernel.o')],capture_output=True,text=True,timeout=35)
(ROOT/'pipeline-debug-type-info.txt').write_text(p.stdout+p.stderr)
dies=[];stack=[];current=None
for line in p.stdout.splitlines():
    match=re.search('[<]([0-9]+)[>][<]([0-9a-f]+)[>]:.*[(](DW_TAG_[^ )]+)[)]',line)
    if match:
        depth=int(match[1]);current={'depth':depth,'tag':match[3],'attributes':{},'children':[]}
        while stack and stack[-1]['depth']>=depth:stack.pop()
        if stack:stack[-1]['children'].append(current)
        stack.append(current);dies.append(current);continue
    match=re.search('DW_AT_(name|byte_size|data_member_location|type)[ ]*:[ ]*(.*)',line)
    if match and current is not None:
        value=match[2].strip()
        if '):' in value:value=value.rsplit('):',1)[1].strip()
        current['attributes'][match[1]]=value
layouts=[]
for die in dies:
    if die['tag'] not in ('DW_TAG_class_type','DW_TAG_structure_type'):continue
    name=die['attributes'].get('name','')
    if name not in ('DeviceTransport','QueueState','TBufType') and not name.startswith('TBuf<'):continue
    layouts.append(dict(name=name,byte_size=die['attributes'].get('byte_size'),members=[c['attributes'] for c in die['children'] if c['tag']=='DW_TAG_member'][:24]))
layouts.sort(key=lambda d:(d['name'] not in ('DeviceTransport','QueueState','TBufType'),d['name']))
print(json.dumps({'pending_loads':loads[-12:],'object_field72_last_accesses':field_access[-6:],'debug_type_readelf_exit':p.returncode,'type_layouts':layouts[:12]},separators=(',',':')))
