# Copyright (c) 2026 TileXR Project.
"""Map the preserved fault through a debug build only when its text is byte-identical."""
import hashlib,json,re,shutil,struct,subprocess
from pathlib import Path
ROOT=Path('/work')
def sections(p):
    raw=p.read_bytes();offset=struct.unpack_from('<Q',raw,40)[0];size,count,names=struct.unpack_from('<HHH',raw,58)
    rows=[struct.unpack_from('<IIQQQQIIQQ',raw,offset+i*size) for i in range(count)]
    table=rows[names];strings=raw[table[4]:table[4]+table[5]]
    result={}
    for row in rows:
        end=strings.find(bytes([0]),row[0]);name=strings[row[0]:end].decode();result[name]=raw[row[4]:row[4]+row[5]]
    return result

def main(tools):
    tools=Path(tools);base=ROOT/'pipeline-build-40596e2-a1';dest=ROOT/'pipeline-debug-build-a1'
    assert not dest.exists()
    assert (base/'pipeline_kernel.cpp').read_bytes()==(tools/'pipeline_kernel.cpp').read_bytes(),'source_changed_since_fault'
    script=(tools/'pipeline_build.sh').read_text().replace('target=/work/pipeline-build-40596e2-a1','target=/work/pipeline-debug-build-a1')
    assert script.count('bisheng -xcce -O2')==1
    script=script.replace('bisheng -xcce -O2','bisheng -g -xcce -O2')
    builder=ROOT/'pipeline-debug-build-a1.sh';builder.write_text(script)
    with (ROOT/'pipeline-debug-compile.log').open('wb') as log:p=subprocess.run(['bash',str(builder),str(tools/'pipeline_kernel.cpp')],stdout=log,stderr=subprocess.STDOUT,timeout=300)
    result={'compile_exit':p.returncode,'debug_build':str(dest),'fault_pc_offset':106996,'diagnostic_only':True,'operator_qualified':False}
    if p.returncode:
        result['errors']=[s[:250] for s in (ROOT/'pipeline-debug-compile.log').read_text(errors='replace').splitlines() if 'error:' in s][-4:]
    else:
        a,b=sections(base/'kernel.o'),sections(dest/'kernel.o')
        result.update(text_identical=a['.text']==b['.text'],original_text_sha256=hashlib.sha256(a['.text']).hexdigest(),debug_text_sha256=hashlib.sha256(b['.text']).hexdigest())
        words={hex(pc):struct.unpack_from('<I',a['.text'],pc)[0] for pc in (0x1a1c4,0x1a1cc,0x1a1f0,0x1a1f4)}
        assert words['0x1a1c4']==0x1c8c0050 and words['0x1a1cc']==0x1cd40048
        raw=words['0x1a1f4'];result['fault_word']=hex(raw)
        if raw>>24==0x1c:result['fault_load_decoded_from_matching_trace_format']=dict(destination_register=(raw>>17)&31,base_register=(raw>>12)&31,immediate=raw&4095)
        tool=Path('/opt/sim/Ascend/cann-9.1.0/tools/bisheng_compiler/bin/llvm-addr2line')
        if result['text_identical'] and tool.is_file():
            p=subprocess.run([str(tool),'-e',str(dest/'kernel.o'),'-f','-C','-i','0x1a1f4'],capture_output=True,text=True,timeout=20)
            (dest/'fault-source-map.txt').write_text(p.stdout+p.stderr);result['addr2line_exit']=p.returncode;result['fault_source_map']=p.stdout.splitlines()[:24]
        else:result['addr2line_available']=tool.is_file()
    (ROOT/'pipeline-debug-result.json').write_text(json.dumps(result,indent=2));print(json.dumps(result,separators=(',',':')),flush=True)
    return 0
if __name__=='__main__':
    import sys
    raise SystemExit(main(sys.argv[1]))
