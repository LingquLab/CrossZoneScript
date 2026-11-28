# 中文：只读取已完成的原版控制Fanout矩阵，汇总Event、Device跨度和同AIV阶段；不重跑实验。
# English: Read completed original-control fanout runs and aggregate Event, device-span and same-AIV stages without rerunning tests.
import concurrent.futures,json,math,statistics,subprocess
from pathlib import Path
state=Path('D:/code-project/harness/plugins/cross-zone-development/.state/b212b5f4-f81d-4ec7-a77d-0288664f5d31')
log=state/'runs/ubx-fanout-ep32-matrix/1/execution/stdout.log'
events=[json.loads(line) for line in log.read_text(encoding='utf-8').splitlines() if line.startswith('{')]
assert any(e.get('event')=='DONE' and e.get('passed') for e in events)
runs=[{k:e[k] for k in ['profile','pipeline','run_id']} for e in events if e.get('event')=='experiment'];assert len(runs)==12
keys=[p for p in Path('C:/Users/l00826879/.ssh').glob('*deploy*') if p.is_file() and p.suffix not in ['.pub','.old','.bak'] and b'PRIVATE KEY' in p.read_bytes()];assert keys
ssh=['ssh','-o','BatchMode=yes','-o','IdentitiesOnly=yes','-o','ConnectTimeout=10','-o','StrictHostKeyChecking=no','-o','UserKnownHostsFile=NUL']
for key in keys:ssh+=['-i',str(key)]
remote=r'''
import json,struct
from pathlib import Path
from collections import defaultdict
root=Path('/root/ubx-fanout-b212b5f4');data=defaultdict(list);slow={};counts=defaultdict(list)
assert json.loads((root/'build-receipt.json').read_text())['revision']=='9302faf9490d7c55ebfbfefc6b830ae4a1b9cef7'
for run in runs:
 base=run['profile']+':'+str(run['pipeline'])
 for d in (root/run['run_id']).glob('rank-*'):
  for p in (d/'records').glob('*.summary.json'):
   s=json.loads(p.read_text());assert s['correctness'] and s['device_barrier_status']==0
   if s['warmup']:continue
   data[base+':ALL:event'].append(s['dispatch_event_us'])
   data[base+':ALL:dispatch_barrier'].append(s['dispatch_event_us']+s['barrier_device_us'])
   for k in ['rdma_data_bytes','data_wqes','mem_records']:counts[base+':'+k].append(s[k])
   roundkey=base+':'+run['run_id']+':'+str(s['round']);slow[roundkey]=max(slow.get(roundkey,0),s['dispatch_event_us'])
   stem=str(p)[:-13];scale=1e6/json.loads(Path(stem+'.clock.json').read_text())['cycles_per_second']
   raw=Path(stem+'.records.bin').read_bytes();h=struct.unpack_from('<8sHHHHHBBI16sQ',raw);pos=48;streams={}
   for _ in range(h[8]):
    lane,n,dropped,flags,size=struct.unpack_from('<IIIIQ',raw,pos);assert not dropped and flags==1
    streams[lane&65535]={e:t for t,e,_ in [struct.unpack_from('<QII',raw,pos+24+i*16) for i in range(n)]};pos+=size
   assert all(1 in streams[c] and 2 in streams[c] and 238 not in streams[c] for c in range(56))
   data[base+':ALL:span'].append((max(streams[c][2] for c in range(56))-min(streams[c][1] for c in range(56)))*scale)
   for core in range(56):
    role='SEND' if core<16 else 'RECV' if core<32 else 'RELAY' if core<48 else 'INTRA';e=streams[core];counter=streams[core+56]
    data[base+':'+role+':run'].append((e[2]-e[1])*scale)
    if role!='RELAY' or run['profile']!='on':continue
    pairs={'fanout_window':(300,309),'fanout_drain_observed':(307,308),'prepare_to_first_export':(310,311),'task_export':(312,313),'relay_planning':(33,34),'listen_after_plan':(34,36)}
    for name,(a,b) in pairs.items():
     if a in e and b in e:data[base+':RELAY:'+name].append((e[b]-e[a])*scale)
    for name,a in {'relay_load_counter':86,'relay_tasks_counter':88,'relay_positions_counter':90,'relay_export_counter':92,'fanout_drain_counter':424}.items():
     if a in counter and a+1 in counter:data[base+':RELAY:'+name].append((counter[a+1]-counter[a])*scale)
print(json.dumps({'data':data,'slow':slow,'counts':counts}))
'''
def one(host):
 p=subprocess.run(ssh+['root@'+host,'python3 -'],input='runs='+repr(runs)+'\n'+remote,text=True,capture_output=True,timeout=240)
 Path('fanout-collect-'+host+'.stderr.log').write_text(p.stderr)
 assert p.returncode==0,'Read-only retained evidence collection failed'
 return json.loads(p.stdout)
with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(one,['90.90.93.83','90.90.93.86']))
data={};counts={};slow={}
for result in results:
 for kind,out in [('data',data),('counts',counts)]:
  for k,v in result[kind].items():out.setdefault(k,[]).extend(v)
 for k,v in result['slow'].items():slow[k]=max(slow.get(k,0),v)
for k,v in slow.items():data.setdefault(':'.join(k.split(':')[:2])+':ALL:slowest',[]).append(v)
def stats(v):
 v=sorted(v);i=(len(v)-1)*.95;lo=int(i);hi=min(len(v)-1,lo+1)
 return {'count':len(v),'mean':statistics.mean(v),'median':statistics.median(v),'min':v[0],'p95':v[lo]+(v[hi]-v[lo])*(i-lo),'max':v[-1]}
full={'revision':'9302faf9490d7c55ebfbfefc6b830ae4a1b9cef7','runs':runs,'metrics':{k:stats(v) for k,v in data.items()},'counts':{k:stats(v) for k,v in counts.items()}}
Path('fanout-full-statistics.json').write_text(json.dumps(full,indent=2))
compact=[]
for profile in ['on','off']:
 for mode in range(3):
  base=profile+':'+str(mode);r={'p':profile,'v':mode}
  for metric in ['event','span','slowest']:
   m=full['metrics'][base+':ALL:'+metric];r[metric]=[round(m[k],4) for k in ['mean','median','p95','max']]
  if profile=='on':
   r['relay']={metric:round(full['metrics'][base+':RELAY:'+metric]['mean'],4) for metric in ['run','fanout_window','prepare_to_first_export','task_export','relay_planning','listen_after_plan'] if base+':RELAY:'+metric in full['metrics']}
   if mode:r['relay']['drain']=round(full['metrics'][base+':RELAY:fanout_drain_observed']['mean'],4)
  compact.append(r)
print(json.dumps({'columns':['mean','median','p95','max'],'rows':compact,'formal_rank_rounds_per_variant':640,'measured_rounds_per_variant':20,'v0_fanout_window_ends_at_dependency_issue':True}))
