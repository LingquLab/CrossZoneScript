"""[AI] COORD009 ten fresh profile1/msprof groups after pinned correctness PASS."""
from pathlib import Path
from types import SimpleNamespace
import importlib.util,json,os,shlex,shutil,subprocess,sys,time,hashlib
BASE_SHA='bccc9295a13b93a14f8517d1d19a051fe9698c80eb12a31e768c074442be27a8'
p=Path(__file__).with_name('runtime-correctness.py')
assert hashlib.sha256(p.read_bytes()).hexdigest()==BASE_SHA,'FROZEN_GUARDS_SHA'
spec=importlib.util.spec_from_file_location('guards',p);b=importlib.util.module_from_spec(spec);spec.loader.exec_module(b)
b.TASK='xr-native107-p2-capture-20261116-01';b.RUN=b.EXP/'p2-capture-20261116-01';b.SOURCE=b.EXP/'profile1/TileXR'
CORRECT=b.EXP/'p2-correctness-20261116-01'
CORRECT_SHA='b544c1408554f3404571fe73972841ebb05682c0b3356cd54554c1a3b587e457'
VALIDATIONS=['e8a91667ba15b4d9f8a81d54017516bc940030b646da015de91d473dbfe69608','00cd50eda23a975ac64f4a7826d9b7a798273d2a1d078cccff3bbcd9bd7fdc5b','883d1175b6484a7bf7dc2229deed7b0e1a1ddffdcee23073d28814a5bbd2f37d']
need,save,sha,now=b.need,b.save,b.sha,b.now

def collect(pid,records):
    s=b.state(pid)
    if s is None:return
    old=records.setdefault(pid,s);need(old['start_ticks']==s['start_ticks'],'OWN_PID_REUSED')
    try:children=Path('/proc/'+str(pid)+'/task/'+str(pid)+'/children').read_text().split()
    except (FileNotFoundError,ProcessLookupError):children=[]
    for c in children:collect(int(c),records)

def correctness_gate(native,case):
    need(sha(CORRECT/'summary.json')==CORRECT_SHA,'CORRECTNESS_SUMMARY_SHA')
    v=json.loads((CORRECT/'summary.json').read_text())
    need(v['status']=='PASS' and v['phase']=='correctness' and v['revision']==b.REV and v['correctness_groups_passed']==3,'CORRECTNESS_STATUS')
    need(v['actual_aiv_cores']==[64,64] and v['devices']==[0,1] and v['namespace_exit']==0 and not v['own_residual_children'] and not v['cleanup_actions'] and not v['monitor_error'] and not v['timed_out'],'CORRECTNESS_EXIT')
    old_source=b.EXP/'profile0/TileXR';candidate=native.candidate_identity(old_source)
    for name,digest in json.loads((b.EXP/'source-hashes.json').read_text()).items():need(sha(old_source/name)==digest,'PROFILE0_SOURCE_SHA')
    for i,digest in enumerate(VALIDATIONS,1):
        d=CORRECT/('group-'+str(i));need(sha(d/'rank-validation.json')==digest,'CORRECTNESS_RANK_VALIDATION_SHA')
        ranks=native.validate_round(d,'allreduce',case)
        need(ranks==json.loads((d/'rank-validation.json').read_text()),'CORRECTNESS_REVALIDATION')
        for rank in ranks:native.check_loaded_candidate(rank,candidate)
    need((CORRECT/'group-1/core-query-run.stdout.log').read_text().splitlines()==['init 0','core 0 0 64','core 1 0 64','final 0'],'SAVED_ACTUAL_CORES')
    return {'status':'PASS','summary_sha256':CORRECT_SHA,'rank_validation_sha256':VALIDATIONS,'actual_aiv_cores':[64,64],'revalidation':'CPU_ONLY_NO_REPLAY'}

def launch(args,d,native,profiler):
    processes=[];logs=[];commands=[];records={};actions=[]
    try:
        for rank in range(2):
            evidence=d/('evidence-rank-'+str(rank));evidence.mkdir()
            env,settings=native.env_for(args,rank,args.ports,evidence)
            env['LD_LIBRARY_PATH']=str(b.SOURCE/'install/lib64')+':'+env.get('LD_LIBRARY_PATH','')
            binary=b.SOURCE/'tests/ep/install/bin/tilexr_ep_dispatch_demo'
            command=[str(profiler),'--application='+shlex.join([str(binary),'2',str(rank),'2','0']),'--output='+str(d/('prof-rank-'+str(rank))),'--task-time=on']
            commands.append({'rank':rank,'argv':command,'settings':settings})
            log=(d/('rank_'+str(rank)+'.log')).open('xb');logs.append(log)
            p=subprocess.Popen(command,env=env,stdout=log,stderr=subprocess.STDOUT,start_new_session=True);processes.append(p)
            s=b.state(p.pid);need(s is not None,'PROFILER_PID_UNKNOWN');records[p.pid]=s
            save(d/'commands.json',commands);save(d/'owned-processes.json',list(records.values()))
        until=time.monotonic()+300
        while any(p.poll() is None for p in processes):
            for p in processes:
                if p.poll() is None:collect(p.pid,records)
            save(d/'owned-processes.json',list(records.values()))
            need(all(p.poll() in (None,0) for p in processes),'PROFILER_OR_RANK_FAILED')
            need(time.monotonic()<until,'PROFILE_GROUP_TIMEOUT');time.sleep(.1)
        need(all(p.returncode==0 for p in processes),'PROFILER_EXIT')
        need(not any(b.live(r) for r in records.values()),'PROFILER_LEFT_LIVE_CHILD')
    finally:
        actions=b.reap_owned(records)
        for p in processes:p.wait(timeout=5)
        for f in logs:f.close()
        remaining=any(b.live(r) for r in records.values())
        save(d/'process-exits.json',[{'pid':p.pid,'exit':p.returncode,'process_group_remaining':remaining} for p in processes])
        save(d/'rank-cleanup.json',actions)
    need(not actions and not remaining,'PROFILE_REQUIRED_CLEANUP')

def validate_lifecycle(d,ranks,candidate,native):
    for rank in ranks:
        native.check_loaded_candidate(rank,candidate)
        log=(d/('rank_'+str(rank['rank'])+'.log')).read_text(errors='replace')
        marks=['NATIVE107 lifecycle HCCL_READY SUM2 CONFIG4','NATIVE107 lifecycle TILEXR_READY','NATIVE107 lifecycle TILEXR_CLOSED','NATIVE107 lifecycle HCCL_CLOSED']
        pos=[log.index(m) for m in marks]
        need(all(log.count(m)==1 for m in marks) and pos==sorted(pos),'LIFECYCLE_ORDER')
        need(log.count('TileXR UDMA attaching to existing RA initialization')==1 and 'InitUDMA success' in log and 'teardown UNKNOWN' not in log,'RA_ATTACHMENT_OR_TEARDOWN')
        for name,digest in b.LIBS.items():
            loaded=[v['sha256'] for p,v in rank['loaded_libraries'].items() if Path(p).name==name]
            need(loaded==[digest],'ACTUAL_LOADED_HCCL_IDENTITY')

def validate_report(report):
    need(len(report['ranks'])==2 and [r['rank'] for r in report['ranks']]==[0,1],'REPORT_RANKS')
    for r in report['ranks']:
        need(r['block_dim']==64 and len(r['cores'])==64,'CAPTURE_CORE_COVERAGE')
        need(sorted(c['core'] for c in r['cores'])==list(range(64)),'CAPTURE_CORE_IDS')
        need(r['profiler_match']['dispatch_tasks']==21 and r['profiler_match']['generation']==21,'PROFILE_GENERATION')
        need(r['device_task_us']>0,'PROFILE_DURATION')
        need(set(r['modules'])=={'M1','M2','M3','M4','M5'} and set(r['function_statistics'])=={'F'+str(i) for i in range(10)},'CAPTURE_STAGES')
        for c in r['cores']:
            for e in c['functional_events'].values():
                if e['active']:need(e['end_tick']>=e['begin_tick'],'NEGATIVE_INTERVAL')
    return True

def worker():
    results={'schema':'native107_p2_capture.v1','task_id':b.TASK,'revision':b.REV,'status':'FAIL_OR_UNVERIFIED','phase':'capture','devices':[0,1],'cores':[64,64],'groups':[],'npu_launch':False,'clock':'NOT_VERIFIED','official_hccl_launch':False}
    try:
        need(os.environ.get('TILEXR_UDMA_ATTACH_EXISTING_RA')=='1','RA_ATTACH_ENV')
        sys.path.insert(0,str(b.SOURCE/'tools/ep'));import run_dispatch_pre_allreduce as native
        case=b.integrity(True);results['correctness_gate']=correctness_gate(native,case)
        profiler=Path(shutil.which('msprof') or '').resolve(strict=True)
        need(profiler.is_file() and profiler.is_relative_to(b.CANN.resolve()),'MSPROF_INSTALLATION')
        profiler_sha=sha(profiler);results['msprof']={'path':str(profiler),'sha256':profiler_sha}
        candidate=native.candidate_identity(b.SOURCE);save(b.RUN/'candidate-profile1.json',candidate)
        deadline=time.monotonic()+3400
        for round_no in range(1,11):
            need(time.monotonic()<deadline,'CAPTURE_PHASE_TIMEOUT')
            d=b.RUN/('round-'+str(round_no));d.mkdir();g={'round':round_no,'status':'FAIL_OR_UNVERIFIED','npu_launch':False,'directory':d.name};results['groups'].append(g)
            try:
                b.process_gate();b.integrity(True);need(sha(profiler)==profiler_sha,'MSPROF_CHANGED')
                g['before']=b.device_gate(d,'before');planned=b.ports(round_no+3)
                args=SimpleNamespace(source=b.SOURCE,devices=[0,1],phase='capture',mode='allreduce',tokens_per_rank=256,fixture=b.EXP/'fixture',cores_per_rank=(64,64),hccl_mode=4,ports=planned,run_id='native107-p2-capture-r'+str(round_no)+'-20261116')
                save(d/'launch-intent.json',{'utc':now(),'run_id':args.run_id,'ports':planned,'hccl_if_base_port':os.environ['HCCL_IF_BASE_PORT'],'warmup':20,'measured':1,'candidate':candidate,'msprof_sha256':profiler_sha})
                g['npu_launch']=None;results['npu_launch']=None;save(b.RUN/'worker-summary.json',results)
                launch(args,d,native,profiler);ranks=native.validate_round(d,'allreduce',case)
                validate_lifecycle(d,ranks,candidate,native);save(d/'rank-validation.json',ranks)
                report=native.enrich_capture(native.base.capture_report(args,d),ranks,cores_per_rank=(64,64));validate_report(report);save(d/'functional.json',report)
                g.update(status='PASS',npu_launch=True,ranks=[{'rank':r['rank'],'business_dispatches':21,'sum2_buffers':22,'input_device_readback':'PASS','flags_cleared':True,'lifecycle':'PASS','msprof_dispatch_tasks':21,'measured_generation':21,'device_task_us':r['device_task_us']} for r in report['ranks']],rank_validation_sha256=sha(d/'rank-validation.json'),functional_sha256=sha(d/'functional.json'))
            except BaseException as e:
                g.update(error_type=type(e).__name__,error_detail=str(e)[:220]);raise
            finally:
                try:g['after']=b.device_gate(d,'after');b.integrity(True);b.process_gate()
                except BaseException as e:g.update(status='FAIL_OR_UNVERIFIED',postcheck_error=type(e).__name__+':'+str(e)[:180])
                save(d/'summary.json',g);save(b.RUN/'worker-summary.json',results)
            need(g['status']=='PASS','GROUP_NOT_PASS')
        results.update(status='PASS',npu_launch=True,capture_groups_passed=10,actual_aiv_cores=[64,64])
    except BaseException as e:results.update(error_type=type(e).__name__,error_detail=str(e)[:220])
    save(b.RUN/'worker-summary.json',results)
    return 0 if results['status']=='PASS' else 1

def namespace():
    need(os.readlink('/proc/self/ns/mnt')!=sys.argv[2],'NAMESPACE_NOT_PRIVATE')
    for target,(name,digest) in b.PRIVATE.items():
        need(sha(b.PRE/name)==digest,'PRIVATE_SOURCE_SHA')
        subprocess.run(['mount','--bind',str(b.PRE/name),target],check=True,capture_output=True,timeout=10)
        subprocess.run(['mount','-o','remount,bind,ro',target],check=True,capture_output=True,timeout=10)
        need(sha(target)==digest,'PRIVATE_BIND_SHA')
    return worker()

def parent():
    b.integrity();need(not b.RUN.exists(),'SAVED_RUN_EXISTS_NO_REPLAY');b.RUN.mkdir(mode=0o750)
    need(not any(os.environ.get(k) for k in ('LD_PRELOAD','ASCEND_VISIBLE_DEVICES','ASCEND_RT_VISIBLE_DEVICES','TILEXR_UDMA_ATTACH_EXISTING_RA','HCCL_ALGO','HCCL_OP_EXPANSION_MODE','HCCL_DETERMINISTIC')),'UNEXPECTED_INHERITED_ENV')
    checker=Path(__file__).with_name('check_npu_status.py');need(sha(checker)==CHECKER_SHA,'CHECKER_SHA');(b.RUN/'check_npu_status.py').write_bytes(checker.read_bytes())
    need(sha(__file__)==SCRIPT_SHA,'CAPTURE_SOURCE_SHA')
    original=os.readlink('/proc/self/ns/mnt');argv=['unshare','--mount','--propagation','private','/usr/bin/python3',__file__,'namespace',original]
    prefix='source '+str(b.CANN/'set_env.sh')+' >/dev/null 2>&1 && export LD_LIBRARY_PATH='+str(b.SOURCE/'install/lib64')+':/usr/local/Ascend/driver/lib64/driver:/usr/local/Ascend/driver/lib64/common:${LD_LIBRARY_PATH:-} && export TILEXR_UDMA_ATTACH_EXISTING_RA=1 HCCL_CONNECT_TIMEOUT=120 HCCL_EXEC_TIMEOUT=180 && exec '
    records={};monitor_error=None;timed=False
    with (b.RUN/'namespace.stdout.log').open('xb') as out,(b.RUN/'namespace.stderr.log').open('xb') as err:
        p=subprocess.Popen(['bash','-c',prefix+shlex.join(argv)],stdout=out,stderr=err,stdin=subprocess.DEVNULL,start_new_session=True)
        s=b.state(p.pid)
        if s:records[p.pid]=s
        save(b.RUN/'launcher.json',{'pid':p.pid,'identity':s,'argv':argv,'utc':now()})
        until=time.monotonic()+3500
        try:
            while p.poll() is None:
                collect(p.pid,records);save(b.RUN/'owned-processes.json',list(records.values()))
                if time.monotonic()>until:timed=True;break
                time.sleep(.1)
        except BaseException as e:monitor_error=type(e).__name__+':'+str(e)[:200]
        finally:actions=b.reap_owned(records);code=p.wait(timeout=5)
    residual=[r for r in records.values() if b.live(r)]
    path=b.RUN/'worker-summary.json';v=json.loads(path.read_text()) if path.exists() else {'task_id':b.TASK,'status':'FAIL_OR_UNVERIFIED','npu_launch':None,'groups':[]}
    preserved=all(sha(p)==s for p,s in b.SYSTEM.items()) and original==os.readlink('/proc/self/ns/mnt')
    v.update(namespace_exit=code,timed_out=timed,monitor_error=monitor_error,cleanup_actions=actions,own_residual_children=residual,original_configuration_preserved=preserved,run_dir=str(b.RUN),qualification_reused=True,qualification_sha256=b.QS,script_sha256=SCRIPT_SHA,checker_sha256=CHECKER_SHA,base_script_sha256=BASE_SHA)
    if code or timed or monitor_error or actions or residual or not preserved:v['status']='FAIL_OR_UNVERIFIED'
    save(b.RUN/'summary.json',v);text=json.dumps(v,separators=(',',':'));need(len(text.encode())<23000,'SUMMARY_TOO_LARGE');print(text)
    return 0 if v['status']=='PASS' else 1

if __name__=='__main__':
    meta=json.loads(Path(__file__).with_name('capture-identity.json').read_text());SCRIPT_SHA=meta['script_sha256'];CHECKER_SHA=meta['checker_sha256']
    if len(sys.argv)>1:raise SystemExit(namespace())
    raise SystemExit(parent())
