# Export validated profiling A/B measurements and trace locations for the user.
import csv,hashlib,json,statistics,subprocess,zipfile
from pathlib import Path
keys=[p for p in Path('C:/Users/l00826879/.ssh').glob('*deploy*') if p.is_file() and p.suffix not in ['.pub','.old','.bak'] and p.stat().st_size<65536 and b'PRIVATE KEY' in p.read_bytes()]
assert keys
ssh=['ssh','-o','BatchMode=yes','-o','IdentitiesOnly=yes','-o','ConnectTimeout=10','-o','StrictHostKeyChecking=no','-o','UserKnownHostsFile=NUL']
for key in keys:ssh+=['-i',str(key)]
out=Path('D:/work/UBX攻关/UBX_Control/control-vs-global-ep32-bs32-profiling')
out.mkdir(parents=True,exist_ok=True)
remote=r'''import json
from pathlib import Path
root=Path('/root/ubx-control-profile-ep32-d23ffa56');items=[];traces=[]
for variant,run in [('control','ablation-bs32-mt2-3a4696ed55a2'),('global','ablation-bs32-mt2-f942f6746eed')]:
 for d in sorted((root/variant/run).glob('rank-*')):
  for p in sorted((d/'records').glob('*.summary.json')):
   obj=json.loads(p.read_text());obj['variant']=variant;obj['run_id']=run;items.append(obj)
  for p in sorted((d/'records').glob('*.records.bin')):
   traces.append({'variant':variant,'run_id':run,'rank':int(d.name.split('-')[1]),'path':str(p),'bytes':p.stat().st_size})
print(json.dumps({'summaries':items,'trace_manifest':traces}))
'''
summaries=[];traces=[]
for host in ['90.90.93.81','90.90.93.83']:
 p=subprocess.run(ssh+['root@'+host,'python3 -'],input=remote,text=True,capture_output=True,timeout=90)
 assert p.returncode==0,'Read-only data collection failed'
 data=json.loads(p.stdout)
 for item in data['summaries']: item['host']=host;summaries.append(item)
 for item in data['trace_manifest']:item['host']=host;traces.append(item)
summaries.sort(key=lambda x:(x['variant'],x['round'],x['rank']))
assert len(summaries)==1600 and all(s['correctness'] for s in summaries)
for variant in ['control','global']:
 samples=[s for s in summaries if s['variant']==variant]
 assert {(s['rank'],s['round']) for s in samples}=={(rank,round_) for rank in range(32) for round_ in range(25)}
 assert sum(not s['warmup'] for s in samples)==640
fields=['variant','host','rank','round','warmup','correctness','dispatch_event_us','barrier_device_us','call_device_us','round_device_us','round_wall_us','prepare_once_us','batch','experts','max_tokens','rank_size','dtype','output_rows','rdma_data_bytes','data_wqes','mem_records','capture_lanes','dropped','run_id']
def csvfile(path,columns,rows):
 with path.open('w',encoding='utf-8-sig',newline='') as f:
  w=csv.DictWriter(f,fieldnames=columns,extrasaction='ignore');w.writeheader();w.writerows(rows)
csvfile(out/'all_rounds.csv',fields,summaries)
csvfile(out/'measured_rounds.csv',fields,[s for s in summaries if not s['warmup']])
(out/'all_summaries.json').write_text(json.dumps(summaries,ensure_ascii=False,indent=2),encoding='utf-8')
csvfile(out/'raw_profiling_locations.csv',['variant','host','rank','run_id','path','bytes'],traces)
def stats(values):
 values=sorted(values);n=len(values);z=(n-1)*.95;i=int(z)
 return {'count':n,'mean_us':statistics.mean(values),'median_us':statistics.median(values),'p95_us':values[i]+(values[min(i+1,n-1)]-values[i])*(z-i),'min_us':values[0],'max_us':values[-1]}
aggregates=[];round_rows=[]
for variant in ['control','global']:
 samples=[s for s in summaries if s['variant']==variant and not s['warmup']]
 aggregates.append({'variant':variant,'aggregation':'all_rank_samples',**stats([s['dispatch_event_us'] for s in samples])})
 maxima=[]
 for round_ in range(5,25):
  values=[s['dispatch_event_us'] for s in samples if s['round']==round_]
  maxima.append(max(values));round_rows.append({'variant':variant,'round':round_,'rank_count':len(values),'mean_rank_us':statistics.mean(values),'slowest_rank_us':max(values)})
 aggregates.append({'variant':variant,'aggregation':'per_round_slowest_rank',**stats(maxima)})
csvfile(out/'latency_summary.csv',['variant','aggregation','count','mean_us','median_us','p95_us','min_us','max_us'],aggregates)
csvfile(out/'per_round_summary.csv',['variant','round','rank_count','mean_rank_us','slowest_rank_us'],round_rows)
stagefile=Path('D:/code-project/harness/plugins/cross-zone-development/.state/d23ffa56-8956-47d1-b942-28b714bd37b8/runs/ubx-profile-stage-analysis-complete/1/execution/stdout.log')
stages=[]
if stagefile.is_file():
 for line in stagefile.read_text(encoding='utf-8').splitlines():
  d=json.loads(line)
  for v in d.get('profiling_analysis',[]):
   for name,values in v['stage_statistics'].items():stages.append({'host':d['host'],'variant':v['variant'],'stage':name,**values})
   if v.get('rank_topk_exchange_envelope'):stages.append({'host':d['host'],'variant':v['variant'],'stage':'rank_topk_exchange_envelope',**v['rank_topk_exchange_envelope']})
csvfile(out/'profiling_stages.csv',['variant','host','stage','count','mean_us','median_us','p95_us','max_us'],stages)
metadata={'hosts':['90.90.93.81','90.90.93.83'],'ep':32,'batch':32,'experts':384,'hidden':3584,'topk':8,'max_tokens':2,'dtype':'fp8e4m3','profiling':True,'native_msprof':False,'rounds':25,'warmup_rounds':5,'measured_rounds':20,'control_revision':'6cb5fceaf3a282a8d6dfb43acf7fb17b14651396','global_kernel_revision':'cddc120156074039f56683c4e5551012e7df75d9','global_fixture_revision':'48ab0bbf69672ef7faa673c4a6bbfad90f375b31','latency_unit':'microseconds','raw_records_included':False}
(out/'experiment.json').write_text(json.dumps(metadata,indent=2),encoding='utf-8')
(out/'README.txt').write_text('EP32 BS32 control/global profiling data\nAll timing values are microseconds.\nmeasured_rounds.csv: 1280 validated rank-round samples, excludes warmup rounds 0-4.\nall_rounds.csv and all_summaries.json: all 1600 rank-round samples.\nlatency_summary.csv: all-rank sample statistics and per-round slowest-rank statistics.\nprofiling_stages.csv: device trace stage statistics; stages overlap and must not be summed.\nraw_profiling_locations.csv: all 1600 binary trace paths on the two servers; binary traces are not copied into this archive.\ndispatch_event_us excludes sleep; call_device_us and round_device_us include sleep.\nProfiling is TileXR device capture, not native msprof.\nGlobal fixture checks immutable global IDs, output oracle and record counts; removed control inboxes are not checked.\n',encoding='utf-8')
archive=out.with_suffix('.zip')
with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED) as z:
 for p in sorted(out.iterdir()):
  if p.is_file():z.write(p,p.name)
print(json.dumps({'directory':str(out),'archive':str(archive),'archive_bytes':archive.stat().st_size,'archive_sha256':hashlib.sha256(archive.read_bytes()).hexdigest(),'files':[p.name for p in out.iterdir() if p.is_file()],'measured_samples':1280,'all_samples':1600,'trace_locations':len(traces),'latency_summary':aggregates},ensure_ascii=True))
