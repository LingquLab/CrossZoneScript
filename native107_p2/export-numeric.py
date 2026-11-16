"""[AI] CPU-only projection and archive of the completed native107 P2 evidence."""
from pathlib import Path
import csv,hashlib,io,json,math,statistics,sys,zipfile
EXP=Path('/root/native-tlxr-p2-107-5c25e0f0-20261116-run01')
RUN=EXP/'p2-capture-20261116-02';COR=EXP/'p2-correctness-20261116-01';OUT=EXP/'numeric-export-01'
REV='5c25e0f03a585b6f07cc69608eb21b589c6b82f7'

def need(x,why):
 if not x:raise RuntimeError(why)
def sha(p):
 h=hashlib.sha256()
 with Path(p).open('rb') as f:
  for chunk in iter(lambda:f.read(1024*1024),b''):h.update(chunk)
 return h.hexdigest()
def save(p,v):Path(p).write_text(json.dumps(v,ensure_ascii=True,indent=2)+'\n')
def ticks(core,names):
 events=core['functional_events'];spans=sorted((events[n]['begin_tick'],events[n]['end_tick']) for n in names if n in events and events[n]['active'])
 need(all(isinstance(a,int) and isinstance(b,int) and 0<a<=b for a,b in spans),'TICK_INTERVAL')
 need(all(a[1]<=b[0] for a,b in zip(spans,spans[1:])),'OVERLAPPING_SUM')
 return sum(z-a for a,z in spans) if spans else None
def mask(ids):
 ids=sorted(ids);groups=[]
 for c in ids:
  if groups and c==groups[-1][-1]+1:groups[-1].append(c)
  else:groups.append([c])
 return ','.join(str(g[0]) if len(g)==1 else str(g[0])+'-'+str(g[-1]) for g in groups)
def stage_row(rnd,rank,cohort,name,cores,parts):
 vals={c['core']:ticks(c,parts) for c in cores};vals={c:v for c,v in vals.items() if v is not None};mx=max(vals.values()) if vals else None
 return dict(round=rnd,rank=rank,cohort=cohort,stage=name,group_cores=len(cores),participating_cores=len(vals),mask=mask(vals),mean_ticks=statistics.mean(vals.values()) if vals else None,max_ticks=mx,slowest_cores=[c for c,v in vals.items() if v==mx],status='MEASURED' if vals else 'NOT_EXECUTED')
def aggregate(rows):
 result=[]
 for cohort,name in sorted(set((r['cohort'],r['stage']) for r in rows)):
  group=[r for r in rows if (r['cohort'],r['stage'])==(cohort,name)];need(len(group)==20,'STAGE_COVERAGE');active=[r for r in group if r['status']=='MEASURED'];mx=max(r['max_ticks'] for r in active) if active else None
  result.append(dict(cohort=cohort,stage=name,mean_ticks=statistics.mean(r['mean_ticks'] for r in active) if active else None,max_ticks=mx,mean_us=statistics.mean(r['mean_ticks'] for r in active)/1000 if active else None,max_us=mx/1000 if mx is not None else None,masks=sorted(set(r['mask'] for r in group)),participating_cores=sorted(set(r['participating_cores'] for r in group)),slowest=[{'round':r['round'],'rank':r['rank'],'cores':r['slowest_cores']} for r in active if r['max_ticks']==mx],status='MEASURED' if active else 'NOT_EXECUTED'))
 return result
def csv_file(name,rows):
 with (OUT/name).open('w',newline='') as f:
  w=csv.DictWriter(f,fieldnames=list(rows[0]),lineterminator='\n');w.writeheader()
  for r in rows:w.writerow({k:json.dumps(v,separators=(',',':'),sort_keys=True) if isinstance(v,(dict,list)) else v for k,v in r.items()})
def checked_json(path,digest):
 need(sha(path)==digest,'SOURCE_SHA_'+str(path));return json.loads(path.read_text())
def archive(paths,target):
 members={}
 for prefix,root in paths:
  need(root.exists() and not root.is_symlink(),'ARCHIVE_SOURCE_MISSING_OR_SYMLINK')
  files=[root] if root.is_file() else sorted(root.rglob('*'))
  for p in files:
   need(not p.is_symlink(),'ARCHIVE_SYMLINK')
   if p.is_file() and '__pycache__' not in p.parts:
    name=prefix if root.is_file() else prefix+'/'+str(p.relative_to(root));need(name not in members and not name.startswith('/') and '..' not in Path(name).parts,'ARCHIVE_PATH');members[name]=p
 need(len(members)<15000,'ARCHIVE_BOUND')
 manifest={n:{'bytes':p.stat().st_size,'sha256':sha(p)} for n,p in members.items()};save(OUT/'raw-archive-manifest.json',manifest)
 with zipfile.ZipFile(target,'x',zipfile.ZIP_DEFLATED,compresslevel=6) as z:
  for name,p in members.items():z.write(p,name)
  z.write(OUT/'raw-archive-manifest.json','raw-archive-manifest.json')
 with zipfile.ZipFile(target) as z:need(z.testzip() is None and len(z.infolist())==len(members)+1,'ZIP_CRC')
 return {'path':str(target),'bytes':target.stat().st_size,'sha256':sha(target),'members':len(members)+1,'crc':'PASS','manifest_sha256':sha(OUT/'raw-archive-manifest.json')}
def main():
 need(not OUT.exists(),'SAVED_EXPORT_EXISTS_READ_ONLY_RECOVERY');OUT.mkdir()
 sys.path.insert(0,str(EXP/'profile1/TileXR/tools/ep'));import run_dispatch_pre_allreduce as native
 meta=json.loads(Path(__file__).with_name('export-identity.json').read_text())
 receipt=checked_json(RUN/'summary.json',meta['capture_summary_sha256']);correct=checked_json(COR/'summary.json','b544c1408554f3404571fe73972841ebb05682c0b3356cd54554c1a3b587e457')
 need(receipt['status']==correct['status']=='PASS' and receipt['revision']==correct['revision']==REV and receipt['capture_groups_passed']==10 and correct['correctness_groups_passed']==3,'RUN_COUNTS')
 need(receipt['namespace_exit']==correct['namespace_exit']==0 and not receipt['own_residual_children'] and not correct['own_residual_children'],'SAVED_EXIT')
 case=json.loads((EXP/'fixture/case.json').read_text());inputs=json.loads((EXP/'input-identity.json').read_text());need(len(inputs)==12,'INPUT_COUNT')
 for name,v in inputs.items():need(sha(EXP/'fixture'/name)==v['sha256'] and (EXP/'fixture'/name).stat().st_size==v['bytes'],'INPUT_SHA')
 rows=[];work=[];four=[];sources=[]
 for g in receipt['groups']:
  rnd=g['round'];d=RUN/g['directory'];need(g['status']=='PASS','GROUP_STATUS')
  report=checked_json(d/'functional.json',g['functional_sha256']);ranks=checked_json(d/'rank-validation.json',g['rank_validation_sha256'])
  need(native.validate_round(d,'allreduce',case)==ranks,'RAW_BUSINESS_REVALIDATION')
  need(report['schema']=='dispatch.functional.v1' and report['run_id']=='native107-p2-capture-r'+str(rnd)+'-20261116','REPORT_IDENTITY')
  sources.append({'round':rnd,'functional_sha256':g['functional_sha256'],'rank_validation_sha256':g['rank_validation_sha256']})
  functions=native.base.profile.TOTAL_PARTS;parts={f:list(v) for f,v in functions.items()}
  parts.update({name:[p for f in ids for p in functions[f]] for name,ids in native.MODULES.items()})
  for rank in report['ranks']:
   ri=rank['rank'];cores=rank['cores'];need(rank['block_dim']==64 and sorted(c['core'] for c in cores)==list(range(64)),'CORES')
   recv=[c for c in cores if c['diagnostics']['receive_tokens']>0];other=[c for c in cores if c['diagnostics']['receive_tokens']==0]
   need([c['core'] for c in recv]==[0,1,2,3] and len(other)==60 and all(c['diagnostics']['receive_tokens']==128 for c in recv),'COHORT_WORKLOAD')
   names=sorted(set(n for c in cores for n in c['functional_events'] if n.startswith('F')))
   stages=dict(parts);stages.update({n:[n] for n in names})
   for name,part in stages.items():
    row=stage_row(rnd,ri,'all',name,cores,part);rows.append(row)
    if name in rank['modules'] or name in rank['function_statistics']:
     expected=rank['modules'].get(name,rank['function_statistics'].get(name));need(row['participating_cores']==expected['active_cores'] and row['max_ticks']==expected['max_ticks'] and math.isclose(row['mean_ticks'],expected['mean_ticks'],abs_tol=1e-9),'STORED_STATS_REVALIDATION')
   for cohort,group in [('receive',recv),('nonreceive',other)]:
    for name in ('M5','F7','F8','F9','F7.layout_gate','F7.receive_plan','F7.layout_poll','F7.layout_clear','F8.receive_loop','F9.drain_dependency','F9.payload_clear','F9.output_completion'):
     row=stage_row(rnd,ri,cohort,name,group,stages[name]);rows.append(row)
     if cohort=='nonreceive' and name in ('F8','F8.receive_loop','F9.payload_clear','F9.drain_dependency','F7.receive_plan'):need(row['status']=='NOT_EXECUTED','UNEXPECTED_NONRECEIVE_EXECUTION')
   for c in cores:work.append({'round':rnd,'rank':ri,'core':c['core'],'role':c['role'],'diagnostics':c['diagnostics']})
   t=rank['pre_allreduce'];four.append({'round':rnd,'rank':ri,'acl_dispatch_us':t['dispatch_event_us'],'msprof_dispatch_us':rank['device_task_us'],'pre_dispatch_event_us':t['pre_dispatch_event_us'],'segment_event_us':t['segment_event_us']})
 need(len(sources)==10 and len(four)==20 and len(work)==1280,'PROJECTION_COUNTS')
 stats=aggregate(rows);external=[];round_max=[]
 for field in ('acl_dispatch_us','msprof_dispatch_us','pre_dispatch_event_us','segment_event_us'):
  vals=[]
  for rnd in range(1,11):
   r=[v for v in four if v['round']==rnd];mx=max(v[field] for v in r);vals.append(mx);round_max.append({'round':rnd,'metric':field,'rank_max_us':mx,'slowest_ranks':[v['rank'] for v in r if v[field]==mx]})
  external.append({'metric':field,'mean_us':statistics.mean(vals),'median_us':statistics.median(vals),'max_us':max(vals),'slowest_rounds':[i+1 for i,v in enumerate(vals) if v==max(vals)]})
 summary={'schema':'native107_p2_numeric.v1','status':'PASS','source':'107/native/5c25/20261116','reference':'6ead3f89dfafdb05a9a0594e3e1eb3ef6098b8c4','revision':REV,'devices':[0,1],'cores':[64,64],'shape':case['shape'],'correctness_groups':3,'capture_groups':10,'warmup_per_rank_per_group':20,'measured_per_rank_per_group':1,'sum2_buffers_per_rank':22,'clock':'NOT_VERIFIED','unit':'raw ticks; ticks/1000 us per 950DT specification, not measured calibration','mean_weight':'equal round/rank weight after actual participating-core mean','max_scope':'actual max over all cores/ranks/rounds in named cohort, all ties retained','boundary':'M5 disjoint F7+F8+F9 per same core; native F8 excludes F9.drain_dependency; F9 includes drain, clear and output_completion','cohorts':{'receive':{'mask':'0-3','cores':4,'records_per_core':128},'nonreceive':{'mask':'4-63','cores':60,'records_per_core':0}},'business_files':inputs,'aggregates':stats,'external_us':external,'sources':sources,'capture_summary_sha256':meta['capture_summary_sha256'],'qualification_reused':True,'qualification_sha256':receipt['qualification_sha256'],'own_residual_children':[],'device_end_snapshot':receipt['groups'][-1]['after'],'no_further_device_launches':True}
 save(OUT/'summary.json',summary);csv_file('per-round-stages.csv',rows);csv_file('per-core-work.csv',work);csv_file('four-us.csv',four);csv_file('round-rank-max-four-us.csv',round_max)
 wanted=[('M'+str(i),'all') for i in range(1,5)]+[('M5','receive'),('M5','nonreceive')];table=[r for name,c in wanted for r in stats if r['stage']==name and r['cohort']==c]
 numeric_names=['summary.json','per-round-stages.csv','per-core-work.csv','four-us.csv','round-rank-max-four-us.csv']
 numeric_manifest={n:{'bytes':(OUT/n).stat().st_size,'sha256':sha(OUT/n)} for n in numeric_names};save(OUT/'numeric-manifest.json',numeric_manifest)
 archive_paths=[('correctness',COR),('capture',RUN),('source',EXP/'source'),('fixture',EXP/'fixture'),('correctness-scripts',EXP/'correctness-script-01'),('capture-scripts',EXP/'capture-script-02')]
 for n in ('build-summary.json','provenance.json','artifact-hashes.json','source-hashes.json','input-identity.json'):archive_paths.append((n,EXP/n))
 for n in json.loads((EXP/'artifact-hashes.json').read_text()):archive_paths.append(('build/'+n,EXP/n))
 for n in numeric_names+['numeric-manifest.json']:archive_paths.append(('numeric/'+n,OUT/n))
 raw=archive(archive_paths,EXP/'native107-P2-T256-3correctness-10capture-evidence-20261116-01.zip')
 with zipfile.ZipFile(OUT/'numeric.zip','x',zipfile.ZIP_DEFLATED,compresslevel=9) as z:
  for n in numeric_names+['numeric-manifest.json']:z.write(OUT/n,n)
 with zipfile.ZipFile(OUT/'numeric.zip') as z:need(z.testzip() is None,'NUMERIC_ZIP_CRC')
 delivery={'schema':'native107_p2_export.v1','status':'PASS','ssh':'CPU_ONLY','device_execution':'NOT_RUN','replay':False,'raw_archive':raw,'numeric_zip':{'path':str(OUT/'numeric.zip'),'bytes':(OUT/'numeric.zip').stat().st_size,'sha256':sha(OUT/'numeric.zip')},'outputs':numeric_manifest,'table':table,'external_us':external,'stage_rows':len(rows),'work_rows':len(work),'source_groups':10,'clock':'NOT_VERIFIED'}
 save(OUT/'delivery.json',delivery);print(json.dumps(delivery,separators=(',',':')))
if __name__=='__main__':main()
