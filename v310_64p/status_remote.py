from pathlib import Path
import json,os,re,time
case=Path('/opt/sim/simulator/Testcases/dispatch_memory_64p_top6_profile_bridge_20261119_run04')
r={'case_exists':case.is_dir(),'own_sst_processes':0}
receipt=case/'bridge-run-receipt.json'
if receipt.is_file():
    data=json.loads(receipt.read_text())
    for key in ('phase_wall_seconds','total_wall_seconds','phase','status','reason','preflight_error','sst_started','sst_threads','run_exit_code','sst_wall_seconds','completion_times','log_error_kinds','output-generator_exit_code','outputs'):
        if key in data: r[key]=data[key]
    r['receipt_age_s']=round(time.time()-receipt.stat().st_mtime)
log=case/'run.log'
if log.is_file():
    with log.open('rb') as f:
        size=f.seek(0,os.SEEK_END)
        f.seek(max(0,size-16384))
        tail=f.read().decode(errors='replace')
    r['run_log_bytes']=size
    r['run_log_age_s']=round(time.time()-log.stat().st_mtime)
    r['log_categories']=[x for x in ('FATAL','Unknown low level','Unsupported instructions','Traceback','Out of memory','Killed','Simulation is complete') if x in tail]
    r['completion_from_log_tail']=re.findall(r'Simulation is complete, simulated time:\s*([0-9.]+)\s*(ps|ns|us|ms|s)',tail)[:4]
for proc in Path('/proc').glob('[0-9]*/comm'):
    try:
        if proc.read_text().strip() in ('sst','sstsim.x') and (proc.parent/'cwd').resolve()==case:
            r['own_sst_processes']+=1
    except OSError:
        continue
print(json.dumps(r,separators=(',',':')))
