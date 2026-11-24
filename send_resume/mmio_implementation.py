# Copyright (c) 2026 TileXR Project.
"""Save model MMIO disassembly locally and export only bounded operation evidence."""

import json
from pathlib import Path
import re
import shutil
import subprocess

folder = Path('/work/resume-7de1/mmio-implementation-a1')
folder.mkdir(exist_ok=False)
library = Path('/opt/sim/plugins/libDavinciSimulatorV310.so')
proc = subprocess.run(['nm','-D','-S','-C',str(library)],capture_output=True,text=True,timeout=25)
assert proc.returncode==0
methods=[]
for line in proc.stdout.splitlines():
    if 'SST::Davinci::V310::ScalarStDev::' in line and any(name+'(' in line for name in ('decode','execute','issue1Rd')):
        words=line.split(maxsplit=3)
        methods.append(dict(address=int(words[0],16),size=int(words[1],16),name=words[3]))
tool=shutil.which('aarch64-linux-gnu-objdump') or shutil.which('objdump')
assert tool and len(methods)==3
result=dict(ok=True,read_only=True,diagnostic_only=True,report=str(folder),methods=[])
for index,method in enumerate(methods):
    proc=subprocess.run([tool,'-d','-C','--start-address='+str(method['address']),
        '--stop-address='+str(method['address']+method['size']),str(library)],capture_output=True,text=True,timeout=20)
    assert proc.returncode==0,'model_disassembly_failed'
    (folder/('method'+str(index)+'.asm')).write_text(proc.stdout)
    lines=proc.stdout.splitlines()
    calls=[line.split('<',1)[1].rsplit('>',1)[0][:250] for line in lines if re.search(r'\bbl\b',line) and '<' in line]
    shifts=[line.strip()[:300] for line in lines if re.search(r'\blsl\b|\blsr\b|\bubfiz\b|\bubfx\b|\bsbfx\b',line)]
    loads=[line.strip()[:300] for line in lines if re.search(r'\bldr\b',line)]
    row=dict(name=method['name'],address=hex(method['address']),bytes=method['size'],call_targets=calls[:12],
             shift_instructions=shifts[:4],first_loads=loads[:2])
    result['methods'].append(row)
print(json.dumps(result,ensure_ascii=True))
