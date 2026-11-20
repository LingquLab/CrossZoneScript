from collections import Counter
from pathlib import Path
import struct

# Reuse the report validator, replacing only the simulator's byte-level readback.
CASE = Path('/opt/sim/simulator/Testcases/dispatch_tilelang_64p_top6_68d32608_f1scalar01')
namespace = {}
code = base_report.split(b"result={'case':", 1)[0]
exec(compile(code,'report_remote.py','exec'),namespace)
read_original = namespace['read_output']
import json
plan=json.loads((CASE/'tilelang-scenario.json').read_text())
offsets=plan['profile_offsets']
selected={}
ownership=Counter()

def die_region(rank, die, name, size):
    rank_plan=plan['plans'][rank]
    output=rank_plan['profile_outputs'][name]['addr']
    candidates={output,output-rank_plan['gm_base']}
    base=bytearray(size)
    raw=(CASE/'bin'/f'david_{rank}_die{die}_{name}.bin').read_bytes()
    assert len(raw)%520==0
    first,=struct.unpack_from('<Q',raw)
    bases=[addr for addr in candidates if 0<=first-addr<size and (first-addr)%512==0]
    assert len(bases)==1,(rank,die,name,first,sorted(candidates))
    output=bases[0]
    covered=set()
    for pos in range(0,len(raw),520):
        address,=struct.unpack_from('<Q',raw,pos)
        start=address-output
        assert start%512==0 and 0<=start and start+512<=size and start not in covered,(rank,die,name,address,output,size)
        covered.add(start)
        base[start:start+512]=raw[pos+8:pos+520]
    assert len(covered)==size//512,(rank,die,name,len(covered),size//512)
    return base

def read_output(rank, name, size, allow_short=False):
    if name=='diagnostics':
        dias=[die_region(rank,die,name,size) for die in (1,2)]
        merged=bytearray(size)
        for core in range(64):
            lo=64*core
            hi=lo+64
            live=[i for i in range(2) if dias[i][lo:hi]!=b'\xa5'*64]
            assert len(live)==1,(rank,core,live)
            die=live[0]
            selected[rank,core]=die
            ownership[f'die{die+1}']+=1
            merged[lo:hi]=dias[die][lo:hi]
        return merged
    if name=='profile':
        read_size=size//512*512
        dies=[die_region(rank,die,name,read_size) for die in (1,2)]
        merged=bytearray(read_size)
        merged[:128]=dies[0][:128]
        for core in range(64):
            die=selected[rank,core]
            for ofs,stride in zip(offsets,(256,128,64,64,64)):
                lo=ofs+stride*core
                hi=min(lo+stride,read_size)
                if lo<hi: merged[lo:hi]=dies[die][lo:hi]
        return merged
    return read_original(rank,name,size,allow_short)

namespace['read_output']=read_output
state={'case':str(CASE),'phase':'analysis','status':'FAIL'}
out=CASE/'analysis-die-02'
try:
    out.mkdir()
    namespace['report'](out,state)
except Exception as error:
    import traceback
    if out.is_dir(): (out/'error.log').write_text(traceback.format_exc())
    state.update(reason=type(error).__name__+': '+str(error)[:200])
state['diagnostic_die_ownership']=dict(ownership)
if out.is_dir(): (out/'summary.json').write_text(json.dumps(state,indent=2)+'\n')
print(json.dumps(state,separators=(',',':')))
