from pathlib import Path
import hashlib,json,os,re,shutil,subprocess,time
sim=Path('/opt/sim/simulator')
source=sim/'Testcases/dispatch_memory_64p_top6_profile'
case=source.with_name('dispatch_memory_64p_top6_profile_bridge_20261119_run04')
package=Path('/work/src/dispatch_memory_64p_handoff')
sha='7bcb77bf4be8f61cbc8172180e8ef877de40010ef3070943a5f88bd6cf09f625'
def digest(data):
    return hashlib.sha256(data).hexdigest()
def active_sst():
    count=0
    for record in Path('/proc').glob('[0-9]*/comm'):
        try:
            if record.read_text().strip() not in ('sst','sstsim.x'):
                continue
            stat=(record.parent/'stat').read_text()
            process_state=stat.rsplit(')',1)[1].split()[0]
            if process_state not in ('Z','X'):
                count+=1
        except FileNotFoundError:
            continue
    return count
started_at=time.monotonic()
state={'case':str(case),'source_case':str(source),'sst_started':False,'phase_wall_seconds':{}}
def record():
    if created:
        file=case/'bridge-run-receipt.json'
        temp=file.with_suffix('.tmp')
        temp.write_text(json.dumps(state,indent=2)+'\n')
        temp.replace(file)
def stage(name,argv,limit):
    state['phase']=name
    record()
    phase_start = time.monotonic()
    try:
        with (case/(name+'.log')).open('xb') as log:
            result=subprocess.run(argv,cwd=case,stdout=log,stderr=subprocess.STDOUT,timeout=limit)
    finally:
        state['phase_wall_seconds'][name] = round(time.monotonic()-phase_start, 3)
    state[name+'_exit_code']=result.returncode
    record()
    return result.returncode
created=False
try:
    state['phase']='source_identity'
    orig=json.loads((source/'manifest.json').read_text())
    names=['workload.yml','workload_config.yml','v310_config/workload_config.yml']
    workload=(source/names[0]).read_bytes()
    assert digest(workload)==orig['workload_sha256'] and all((source/name).read_bytes()==workload for name in names)
    assert orig['kernel_sha256']==sha and digest((source/'tilexr_ep_dispatch_memory_kernel.o').read_bytes())==sha
    scenario=orig['scenario']
    scalars=scenario['kernel']['scalars']
    assert scenario['world_size']==64 and scenario['block_dim_by_rank']==[64]*64
    assert [scalars[k] for k in ('bs','h','moeExpertNum','topK','dtype','magic')]==[128,7168,384,6,11,1]
    assert not (source/'run.log').exists(),'SOURCE_RUN_LOG_PRESENT'
    assert active_sst()==0,'SST_ALREADY_ACTIVE'
    assert (package/'generator/preflight_64p.py').is_file()
    case.mkdir()
    created=True
    stage_start=time.monotonic()
    state['phase_wall_seconds']['source_identity']=round(stage_start-started_at,3)
    state['phase']='stage_case'
    record()
    for directory in ('input','expected','UBusSim_config','v310_config'):
        shutil.copytree(source/directory,case/directory)
    allowed={'.py','.json','.yml','.yaml','.conf','.ini','.o'}
    for path in source.iterdir():
        if path.is_file() and path.suffix in allowed:
            shutil.copy2(path,case/path.name)
    staged=workload.replace(str(source).encode(),str(case).encode())
    for name in names:
        (case/name).write_bytes(staged)
    orig['kernel_file']=str(case/'tilexr_ep_dispatch_memory_kernel.o')
    orig['workload_sha256']=digest(staged)
    (case/'manifest.json').write_text(json.dumps(orig,indent=2)+'\n')
    state['phase_wall_seconds']['stage_case']=round(time.monotonic()-stage_start,3)
    ubus_config=case/'UBusSim_config/UBusSim_simulator_config.yml'
    simulator_cfg=ubus_config.read_bytes()
    patched, count=re.subn(rb'(?m)^([ \t]*stat_trace_flag:[ \t]*&stat_trace_flag[ \t]+)True(?=[ \t\r\n]*(?:#|$))',rb'\g<1>False',simulator_cfg)
    assert count==1,'UBUS_TRACE_CONFIG_NOT_IDENTIFIED'
    ubus_config.write_bytes(patched)
    state['ubus_trace_disabled']=True
    state['ubus_config_sha256_before']=digest(simulator_cfg)
    state['ubus_config_sha256_after']=digest(patched)
    assert (case/'UBusSim_config/UBusSim_simulator_config_topo.yml').is_file()
    assert stage('preflight',['python3','-B',str(package/'generator/preflight_64p.py'),'--testcase',str(case)],120)==0,'PREFLIGHT_FAILED'
    check=json.loads((case/'preflight.log').read_text())
    assert (check['status'], check['world_size'], check['input_golden_members'], check['profile_dump_regions'])==('PASS',64,448,128)
    state['preflight']=check
    assert active_sst()==0,'SST_ALREADY_ACTIVE'
    state['sst_threads']=min(20,len(os.sched_getaffinity(0)))
    state['sst_started']=True
    command='source /opt/sim/sst-env.sh && export SST_PATH=/opt/sim/local/sstcore SST_CONFIG_FILE_PATH=/opt/sim/simulator/config/sst-plugins.conf SST_CONFIG_FILE_PATH_SEPARATOR=: && ulimit -n 524288 && exec timeout -k 15s 1800s sst -n "$1" ./test_UBusSim.py'
    start=time.monotonic()
    rc=stage('run',['bash','-lc',command,'launch',str(state['sst_threads'])],1840)
    state['sst_wall_seconds']=round(time.monotonic()-start,3)
    finish=[]
    interesting=[]
    with (case/'run.log').open(errors='replace') as log:
        for line in log:
            hit=re.search(r'Simulation is complete, simulated time:\s*([0-9.]+)\s*(ps|ns|us|ms|s)',line)
            if hit: finish.append([float(hit[1]),hit[2]])
            if len(interesting)<8:
                for label in ('FATAL','Unknown low level','Unsupported instructions','Traceback','Out of memory','Killed'):
                    if label in line: interesting.append(label)
    state['completion_times']=finish[:8]
    state['log_error_kinds']=sorted(set(interesting))
    assert rc==0 and len(finish)==1 and not interesting,'SST_INCOMPLETE'
    assert stage('output-generator',['python3','-B','output_generator.py','v310_config'],300)==0,'OUTPUT_GENERATOR_FAILED'
    output=[]
    for folder in ('bin','OUTPUT','output'):
        path=case/folder
        if path.is_dir():
            files=[p for p in path.rglob('*') if p.is_file()]
            output.append({'directory':folder,'files':len(files),'bytes':sum(p.stat().st_size for p in files),'first_names':[p.relative_to(path).as_posix() for p in sorted(files)[:6]]})
    state['outputs']=output
    state['status']='SST_COMPLETE_PENDING_BUSINESS_AND_PROFILE_VALIDATION'
except (AssertionError,OSError,KeyError,ValueError,subprocess.TimeoutExpired) as error:
    state['status']='STOPPED'
    reasons={'UBUS_TRACE_CONFIG_NOT_IDENTIFIED','SOURCE_RUN_LOG_PRESENT','PREFLIGHT_FAILED','SST_ALREADY_ACTIVE','SST_INCOMPLETE','OUTPUT_GENERATOR_FAILED'}
    state['reason']=str(error) if str(error) in reasons else type(error).__name__
    if state['reason']=='PREFLIGHT_FAILED':
        text=(case/'preflight.log').read_text(errors='replace')
        state['preflight_error']=text.splitlines()[-1][:220] if text.splitlines() else ''
state['total_wall_seconds']=round(time.monotonic()-started_at,3)
record()
print(json.dumps(state,separators=(',',':')))
