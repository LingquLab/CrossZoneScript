"""[AI] COORD009: three fresh native107 P2 correctness groups; first error stops."""
from pathlib import Path
from types import SimpleNamespace
import concurrent.futures, datetime, hashlib, json, os, re, shlex, signal, socket, subprocess, sys, time
EXP=Path('/root/native-tlxr-p2-107-5c25e0f0-20261116-run01')
RUN=EXP/'p2-correctness-20261116-01'
TASK='xr-native107-p2-correctness-20261116-01'
REV='5c25e0f03a585b6f07cc69608eb21b589c6b82f7'
CANN=Path('/usr/local/Ascend/cann-9.1.0')
SOURCE=EXP/'profile0/TileXR'
ART='096f33a2ac2e4edf6ef5193a449d51dd8b9565da4a64c83927bea2bce7f158bd'
PROV='aee5dfb32a42e2746a4bfbc43d1d329fccd083c783c20d19d25622ecf36f8074'
SOURCES='0e8c252053219e3b6bf41732b63ce4ac59b1f46faa2ca07d42289b4d3ddd79fc'
SYSTEM={'/etc/hccl_rootinfo.json':'56a0d1eb4c38422fbbdd946b34036ed6496fe36c1be95045d0519f35d69ad523','/usr/local/Ascend/driver/topo/950/atlas_850_1.json':'65e257641adea221a576bab32710f759a59df80b50bc8ef613b38e10c6164db9'}
PRIVATE=dict(zip(SYSTEM,[('rootinfo-layer0.json','61ccdd60085f52a721864c2077eed60f9e3e9a080f23b6a919dd901dfebd91ea'),('topology-layer0.json','d54ecef1dea84ea6c8106d502cc173afc43a9bab6b21d117e4a1b7d9b152e09d')]))
PRE=Path('/root/tilelang-107-p4-peer-view-preflight-20261116-01')
QUAL=Path('/root/tilelang-hccl-qualifications/693ec683dcc80807f1ccc0c0356a6e032008fd71e1a9a1538c435eaa1a153dc6/supplements/layer0-peer-private-view-single/receipt.json')
QS='e22807e4b530ded459533c840eb9e77722204776f5e99f2994d5a0640fe1b609'
LIBS={'libhccl.so':'25dc451dc22780a11c0dd58065a5f90fece8945986c756aa80831265ce261807','libhccl_v2.so':'1b3a4d8a1d09e03f036ca4371c4304f4deb0ee374c2527affaac7623e81bdb04','libhcomm.so':'e1ebc2e879cc923d1e2b310706a15ebb7250bde6644780875584ce5af3cb530c'}

def need(ok,code):
    if not ok: raise RuntimeError(code)
def sha(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def save(p,v):
    p=Path(p);t=p.with_name(p.name+'.new');t.write_text(json.dumps(v,ensure_ascii=True,indent=2)+'\n');t.replace(p)
def now(): return datetime.datetime.now(datetime.timezone.utc).isoformat()
def state(pid):
    try:
        f=Path('/proc/'+str(pid)+'/stat').read_text().rsplit(') ',1)[1].split()
        return dict(pid=pid,start_ticks=f[19],pgid=int(f[2]),state=f[0],ppid=int(f[1]))
    except (FileNotFoundError,ProcessLookupError): return None
def live(r):
    c=state(r['pid']);return c is not None and c['start_ticks']==r['start_ticks'] and c['state']!='Z'
def reap_owned(records):
    actions=[]
    for sig in (signal.SIGTERM,signal.SIGKILL):
        for r in reversed(list(records.values())):
            if live(r):
                try: os.kill(r['pid'],sig);actions.append({'pid':r['pid'],'start_ticks':r['start_ticks'],'signal':int(sig)})
                except ProcessLookupError: pass
        until=time.monotonic()+3
        while time.monotonic()<until and any(live(r) for r in records.values()): time.sleep(.05)
    return actions

def integrity(private=False):
    need(os.geteuid()==0 and sha('/etc/machine-id')=='78020a81dd869c8ca5a7ba9f4a18b25a9fead957b807f74e65793fced37b9af0','HOST_IDENTITY')
    for n,s in SYSTEM.items(): need(sha(n)==(PRIVATE[n][1] if private else s),'CONFIG_SHA')
    need(sha(QUAL)==QS,'QUAL_SHA');q=json.loads(QUAL.read_text());need(q['status']=='PASS' and q['own_residual_children']==[] and q['environment_key']=='e4db0e0177a56f1daa97c54c6e7a296067a74315b33a7c06b96a65d0a4b67836','QUAL_STATUS')
    for n,s in LIBS.items(): need(sha(CANN/'lib64'/n)==s,'QUALIFIED_LIB_SHA')
    for n,s in [('artifact-hashes.json',ART),('provenance.json',PROV),('source-hashes.json',SOURCES)]: need(sha(EXP/n)==s,'MANIFEST_SHA_'+n)
    b=json.loads((EXP/'build-summary.json').read_text());need(b['status']=='PASS' and b['revision']==REV and b['core_cxx11_abi']==0 and b['profile_variants']==[0,1],'BUILD_IDENTITY')
    for n,s in json.loads((EXP/'artifact-hashes.json').read_text()).items(): need(sha(EXP/n)==s,'ARTIFACT_SHA_'+n)
    for n,s in json.loads((EXP/'source-hashes.json').read_text()).items():
        for root in (EXP/'source',SOURCE,EXP/'profile1/TileXR'): need(sha(root/n)==s,'SOURCE_SHA_'+n)
    prov=json.loads((EXP/'provenance.json').read_text());need(sha(EXP/'input-identity.json')==prov['input_manifest_sha256'],'INPUT_MANIFEST_SHA')
    ident=json.loads((EXP/'input-identity.json').read_text());need(len(ident)==12,'INPUT_COUNT')
    for n,v in ident.items():
        p=EXP/'fixture'/n;need(p.stat().st_size==v['bytes'] and sha(p)==v['sha256'],'INPUT_SHA_'+n)
    case=json.loads((EXP/'fixture/case.json').read_text());need(case['shape']=={'world':2,'experts':4,'tokens_per_rank':256,'hidden':256,'topk':2} and case['cores_per_rank']==[64,64],'CASE_SHAPE')
    need(case['hashes']=={n:v['sha256'] for n,v in ident.items()} and case['root']==str(EXP/'fixture'),'CASE_IDENTITY')
    return case

def process_gate():
    ancestors=set();pid=os.getpid()
    while pid:
        ancestors.add(pid);s=state(pid);need(s is not None,'ANCESTOR_UNKNOWN');pid=s['ppid']
    hits=[];errors=[]
    markers=(b'native-tlxr-p2-107',b'xr-native107',b'tilelang-107',b'tilexr_ep_dispatch_demo',b'npu_dispatch_hccl_test',b'tilelang-hccl-qualifications')
    for p in Path('/proc').iterdir():
        if not p.name.isdigit() or int(p.name) in ancestors: continue
        try:
            s=state(int(p.name))
            if s is None or s['state']=='Z': continue
            cmd=(p/'cmdline').read_bytes()
            if any(k in cmd for k in markers): hits.append({'identity':s,'cmd_sha256':hashlib.sha256(cmd).hexdigest()})
        except (FileNotFoundError,ProcessLookupError): pass
        except OSError as e: errors.append({'pid':p.name,'type':type(e).__name__})
    need(not hits and not errors,'KNOWN_PROCESS_OR_UNKNOWN_PROC');return {'known_processes':hits,'unknown':errors}

def device_gate(d,label):
    inventory={'version':1,'environments':[{'name':'native107','ssh':'local','devices':[0,1]}]}
    save(d/(label+'-env.json'),inventory)
    p=subprocess.run(['/usr/bin/python3',str(RUN/'check_npu_status.py'),str(d/(label+'-env.json'))],capture_output=True,timeout=35)
    (d/(label+'-occupancy.stdout.log')).write_bytes(p.stdout);(d/(label+'-occupancy.stderr.log')).write_bytes(p.stderr)
    need(p.returncode==0 and json.loads(p.stdout)['safe_to_use'] is True,'BUSY_OR_UNKNOWN')
    rows=[]
    for dev in (0,1):
        p=subprocess.run(['npu-smi','info','-t','health','-i',str(dev)],capture_output=True,timeout=18)
        (d/(label+'-health-'+str(dev)+'.stdout.log')).write_bytes(p.stdout);(d/(label+'-health-'+str(dev)+'.stderr.log')).write_bytes(p.stderr)
        ok=p.returncode==0 and bool(re.search(rb'Health Status\s*:\s*OK\s*$',p.stdout,re.M));need(ok,'HEALTH_UNKNOWN_OR_BAD')
        rows.append({'device':dev,'health':'OK'})
    return {'utc':now(),'occupancy':'IDLE','health':rows,'reservation':'NONE_SNAPSHOT_ONLY'}
def ports(i):
    base=25000+(i-1)*64;host=29100+(i-1)*3
    ps=list(range(base,base+64))+list(range(host,host+3));lo,hi=map(int,Path('/proc/sys/net/ipv4/ip_local_port_range').read_text().split())
    need(len(set(ps))==len(ps) and all(1024<=p<65536 and not lo<=p<=hi for p in ps),'PORT_PLAN')
    for p in ps:
        for fam,addr in ((socket.AF_INET,'0.0.0.0'),(socket.AF_INET6,'::')):
            with socket.socket(fam,socket.SOCK_STREAM) as s:
                if fam==socket.AF_INET6:s.setsockopt(socket.IPPROTO_IPV6,socket.IPV6_V6ONLY,1)
                s.bind((addr,p))
    os.environ['HCCL_IF_BASE_PORT']=str(base)
    return (host,host+1,host+2)
def core_query(d):
    code='#include <acl/acl.h>\n#include <cstdio>\nint main(){int r=aclInit(nullptr);printf("init %d\\n",r);if(r)return 1;bool ok=true;for(unsigned d=0;d<2;++d){int64_t v=0;int q=aclrtGetDeviceInfo(d,ACL_DEV_ATTR_VECTOR_CORE_NUM,&v);printf("core %u %d %lld\\n",d,q,(long long)v);ok=ok&&q==0&&v==64;}int f=aclFinalize();printf("final %d\\n",f);return ok&&f==0?0:1;}\n'
    (d/'core-query.cpp').write_text(code)
    cmds=[['g++','-std=c++14',str(d/'core-query.cpp'),'-I'+str(CANN/'include'),'-L'+str(CANN/'lib64'),'-Wl,-rpath,'+str(CANN/'lib64'),'-lascendcl','-o',str(d/'core-query')],[str(d/'core-query')]]
    for j,c in enumerate(cmds):
        p=subprocess.run(c,capture_output=True,timeout=35);(d/('core-query-'+str(j)+'.stdout.log')).write_bytes(p.stdout);(d/('core-query-'+str(j)+'.stderr.log')).write_bytes(p.stderr);need(p.returncode==0,'CORE_QUERY_FAILED')
    need(p.stdout.decode().splitlines()==['init 0','core 0 0 64','core 1 0 64','final 0'],'CORE_QUERY_NOT_64_64')
    return [64,64]
def launch(args,d,native):
    processes=[];logs=[];commands=[];records={};actions=[]
    try:
        for rank in range(2):
            evidence=d/('evidence-rank-'+str(rank));evidence.mkdir()
            env,settings=native.env_for(args,rank,args.ports,evidence)
            env['LD_LIBRARY_PATH']=str(SOURCE/'install/lib64')+':'+env.get('LD_LIBRARY_PATH','')
            command=[str(SOURCE/'tests/ep/install/bin/tilexr_ep_dispatch_demo'),'2',str(rank),'2','0']
            commands.append({'rank':rank,'argv':command,'settings':settings,'ld_library_path_prefix':str(SOURCE/'install/lib64')})
            log=(d/('rank_'+str(rank)+'.log')).open('xb');logs.append(log)
            p=subprocess.Popen(command,env=env,stdout=log,stderr=subprocess.STDOUT,start_new_session=True);processes.append(p)
            ident=state(p.pid);need(ident is not None,'RANK_IDENTITY_UNKNOWN');records[p.pid]=ident
            save(d/'owned-ranks.json',list(records.values()));save(d/'commands.json',commands)
        deadline=time.monotonic()+180
        while any(p.poll() is None for p in processes):
            need(all(p.poll() in (None,0) for p in processes),'RANK_FAILED')
            need(time.monotonic()<deadline,'RANK_TIMEOUT');time.sleep(.1)
        need(all(p.returncode==0 for p in processes),'RANK_EXIT_FAILED')
    finally:
        actions=reap_owned(records)
        for p in processes:p.wait(timeout=5)
        for f in logs:f.close()
        rows=[{'pid':p.pid,'exit':p.returncode,'process_group_remaining':bool(state(p.pid) and live(records[p.pid]))} for p in processes if p.pid in records]
        save(d/'process-exits.json',rows);save(d/'rank-cleanup.json',actions)
    need(not actions,'RANK_REQUIRED_CLEANUP')

def core_query(d):
    code='#include <acl/acl.h>\n#include <cstdio>\nint main(){int r=aclInit(nullptr);printf("init %d\\n",r);if(r)return 1;bool good=true;for(unsigned d=0;d<2;++d){int64_t v=0;int q=aclrtGetDeviceInfo(d,ACL_DEV_ATTR_VECTOR_CORE_NUM,&v);printf("core %u %d %lld\\n",d,q,(long long)v);good=good&&q==0&&v==64;}int f=aclFinalize();printf("final %d\\n",f);return good&&f==0?0:1;}\n'
    (d/'core-query.cpp').write_text(code)
    steps=[('compile',['g++','-std=c++14',str(d/'core-query.cpp'),'-I'+str(CANN/'include'),'-L'+str(CANN/'lib64'),'-Wl,-rpath,'+str(CANN/'lib64'),'-lascendcl','-o',str(d/'core-query')]),('run',[str(d/'core-query')])]
    for label,argv in steps:
        p=subprocess.run(argv,capture_output=True,timeout=35)
        (d/('core-query-'+label+'.stdout.log')).write_bytes(p.stdout);(d/('core-query-'+label+'.stderr.log')).write_bytes(p.stderr)
        need(p.returncode==0,'ACTUAL_CORE_QUERY_'+label)
        if label=='run':need(p.stdout.decode().splitlines()==['init 0','core 0 0 64','core 1 0 64','final 0'],'ACTUAL_CORE_COUNT')
    return [64,64]

def worker():
    results={'schema':'native107_p2_correctness.v1','task_id':TASK,'revision':REV,'status':'FAIL_OR_UNVERIFIED','phase':'correctness','devices':[0,1],'cores':[64,64],'groups':[],'npu_launch':False,'clock':'NOT_VERIFIED','official_hccl_launch':False}
    try:
        need(os.environ.get('TILEXR_UDMA_ATTACH_EXISTING_RA')=='1','RA_ATTACH_ENV')
        sys.path.insert(0,str(SOURCE/'tools/ep'));import run_dispatch_pre_allreduce as native
        case=integrity(True);candidate=native.candidate_identity(SOURCE);save(RUN/'candidate-profile0.json',candidate)
        for i in range(1,4):
            d=RUN/('group-'+str(i));d.mkdir();g={'group':i,'status':'FAIL_OR_UNVERIFIED','npu_launch':False,'directory':d.name};results['groups'].append(g)
            try:
                process_gate();integrity(True);g['before']=device_gate(d,'before');planned=ports(i)
                if i==1:
                    g['device_runtime_probe']=True;save(RUN/'worker-summary.json',results)
                    g['actual_aiv_cores']=core_query(d);g['after_core_query']=device_gate(d,'after-core-query')
                args=SimpleNamespace(source=SOURCE,devices=[0,1],phase='correctness',mode='allreduce',tokens_per_rank=256,fixture=EXP/'fixture',cores_per_rank=(64,64),hccl_mode=4,ports=planned,run_id='native107-p2-correctness-g'+str(i)+'-20261116')
                save(d/'launch-intent.json',{'utc':now(),'run_id':args.run_id,'ports':planned,'hccl_if_base_port':os.environ['HCCL_IF_BASE_PORT'],'warmup':20,'measured':1,'candidate':candidate})
                g['npu_launch']=None;results['npu_launch']=None;save(RUN/'worker-summary.json',results)
                launch(args,d,native);ranks=native.validate_round(d,'allreduce',case)
                for rank in ranks:
                    native.check_loaded_candidate(rank,candidate)
                    log=(d/('rank_'+str(rank['rank'])+'.log')).read_text(errors='replace')
                    marks=['NATIVE107 lifecycle HCCL_READY SUM2 CONFIG4','NATIVE107 lifecycle TILEXR_READY','NATIVE107 lifecycle TILEXR_CLOSED','NATIVE107 lifecycle HCCL_CLOSED'];pos=[log.index(m) for m in marks]
                    need(all(log.count(m)==1 for m in marks) and pos==sorted(pos),'LIFECYCLE_ORDER')
                    need(log.count('TileXR UDMA attaching to existing RA initialization')==1 and 'InitUDMA success' in log and 'teardown UNKNOWN' not in log,'RA_ATTACHMENT_OR_TEARDOWN')
                    for name,digest in LIBS.items():
                        loaded=[v['sha256'] for p,v in rank['loaded_libraries'].items() if Path(p).name==name];need(loaded==[digest],'ACTUAL_LOADED_HCCL_IDENTITY')
                save(d/'rank-validation.json',ranks)
                g.update(status='PASS',npu_launch=True,ranks=[{'rank':r['rank'],'business_dispatches':21,'sum2_buffers':22,'input_device_readback':'PASS','flags_cleared':True,'lifecycle':'PASS'} for r in ranks],rank_validation_sha256=sha(d/'rank-validation.json'))
            except BaseException as e:
                g.update(error_type=type(e).__name__,error_detail=str(e)[:220]);raise
            finally:
                try:
                    g['after']=device_gate(d,'after');integrity(True);process_gate()
                except BaseException as e:g.update(status='FAIL_OR_UNVERIFIED',postcheck_error=type(e).__name__+':'+str(e)[:180])
                save(d/'summary.json',g);save(RUN/'worker-summary.json',results)
            need(g['status']=='PASS','GROUP_NOT_PASS')
        results.update(status='PASS',npu_launch=True,correctness_groups_passed=3,actual_aiv_cores=[64,64])
    except BaseException as e:results.update(error_type=type(e).__name__,error_detail=str(e)[:220])
    save(RUN/'worker-summary.json',results)
    return 0 if results['status']=='PASS' else 1

def namespace():
    need(os.readlink('/proc/self/ns/mnt')!=sys.argv[2],'NAMESPACE_NOT_PRIVATE')
    for target,(name,digest) in PRIVATE.items():
        need(sha(PRE/name)==digest,'PRIVATE_SOURCE_SHA')
        subprocess.run(['mount','--bind',str(PRE/name),target],check=True,capture_output=True,timeout=10)
        subprocess.run(['mount','-o','remount,bind,ro',target],check=True,capture_output=True,timeout=10)
        need(sha(target)==digest,'PRIVATE_BIND_SHA')
    return worker()

def parent():
    integrity();need(not RUN.exists(),'SAVED_RUN_EXISTS_NO_REPLAY');RUN.mkdir(mode=0o750)
    need(not any(os.environ.get(k) for k in ('LD_PRELOAD','ASCEND_VISIBLE_DEVICES','ASCEND_RT_VISIBLE_DEVICES','TILEXR_UDMA_ATTACH_EXISTING_RA','HCCL_ALGO','HCCL_OP_EXPANSION_MODE','HCCL_DETERMINISTIC')),'UNEXPECTED_INHERITED_ENV')
    checker=Path(__file__).with_name('check_npu_status.py');need(sha(checker)==CHECKER_SHA,'CHECKER_SHA');(RUN/'check_npu_status.py').write_bytes(checker.read_bytes())
    need(sha(__file__)==SCRIPT_SHA,'RUNTIME_SOURCE_SHA')
    original=os.readlink('/proc/self/ns/mnt');argv=['unshare','--mount','--propagation','private','/usr/bin/python3',__file__,'namespace',original]
    prefix='source '+str(CANN/'set_env.sh')+' >/dev/null 2>&1 && export LD_LIBRARY_PATH='+str(SOURCE/'install/lib64')+':/usr/local/Ascend/driver/lib64/driver:/usr/local/Ascend/driver/lib64/common:${LD_LIBRARY_PATH:-} && export TILEXR_UDMA_ATTACH_EXISTING_RA=1 HCCL_CONNECT_TIMEOUT=120 HCCL_EXEC_TIMEOUT=180 && exec '
    records={};monitor_error=None;timed=False
    def collect(pid):
        s=state(pid)
        if s is None:return
        old=records.setdefault(pid,s);need(old['start_ticks']==s['start_ticks'],'OWN_PID_REUSED')
        try:children=Path('/proc/'+str(pid)+'/task/'+str(pid)+'/children').read_text().split()
        except (FileNotFoundError,ProcessLookupError):children=[]
        for c in children:collect(int(c))
    with (RUN/'namespace.stdout.log').open('xb') as out,(RUN/'namespace.stderr.log').open('xb') as err:
        p=subprocess.Popen(['bash','-c',prefix+shlex.join(argv)],stdout=out,stderr=err,stdin=subprocess.DEVNULL,start_new_session=True)
        s=state(p.pid)
        if s:records[p.pid]=s
        save(RUN/'launcher.json',{'pid':p.pid,'identity':s,'argv':argv,'utc':now()})
        until=time.monotonic()+850
        try:
            while p.poll() is None:
                collect(p.pid);save(RUN/'owned-processes.json',list(records.values()))
                if time.monotonic()>until:timed=True;break
                time.sleep(.1)
        except BaseException as e:monitor_error=type(e).__name__+':'+str(e)[:200]
        finally:
            actions=reap_owned(records);code=p.wait(timeout=5)
    residual=[r for r in records.values() if live(r)]
    path=RUN/'worker-summary.json';v=json.loads(path.read_text()) if path.exists() else {'task_id':TASK,'status':'FAIL_OR_UNVERIFIED','npu_launch':None,'groups':[]}
    preserved=all(sha(p)==s for p,s in SYSTEM.items()) and original==os.readlink('/proc/self/ns/mnt')
    v.update(namespace_exit=code,timed_out=timed,monitor_error=monitor_error,cleanup_actions=actions,own_residual_children=residual,original_configuration_preserved=preserved,run_dir=str(RUN),qualification_reused=True,qualification_sha256=QS,script_sha256=SCRIPT_SHA,checker_sha256=CHECKER_SHA)
    if code or timed or monitor_error or actions or residual or not preserved:v['status']='FAIL_OR_UNVERIFIED'
    save(RUN/'summary.json',v);text=json.dumps(v,separators=(',',':'));need(len(text.encode())<13000,'SUMMARY_TOO_LARGE');print(text)
    return 0

if __name__=='__main__':
    meta=json.loads(Path(__file__).with_name('identity.json').read_text());SCRIPT_SHA=meta['script_sha256'];CHECKER_SHA=meta['checker_sha256']
    if len(sys.argv)>1:raise SystemExit(namespace())
    raise SystemExit(parent())
