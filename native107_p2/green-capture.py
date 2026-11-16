# [AI] Fixed raw blob staging followed by ten authorized fresh profile1/msprof captures.
from pathlib import Path
import hashlib,json,os,shutil,sqlite3,subprocess,time
TASK='xr-native107-p2-capture-20261116-02'
COMMIT=PINNED_COMMIT
SESSION='de762d10-4557-47bf-b966-f62eb2ea5c6f'
EXP='/root/native-tlxr-p2-107-5c25e0f0-20261116-run01'
DEST=EXP+'/capture-script-02'
FILES={'runtime-capture.py': ('f0d6a36a45330890e02b684698bb809fcc2a0d90', 'f93df7cbb4a4917168cf236e1234f6ff85452fc4535f9be6b23c2bff81826527'), 'runtime-correctness.py': ('9c0fc7fce316c642efdeadfe89981fa156e83e01', 'bccc9295a13b93a14f8517d1d19a051fe9698c80eb12a31e768c074442be27a8'), 'check_npu_status.py': ('ab4b9d84602782b47dc90e07c710c0794afba8d5', '7b7b4658bc974bf43b333756a1ac4cf590e73e9fa37139d0de9e59202d96e0b9'), 'capture-identity.json': ('66d1a8c2327bd19c2321dc9739a829c162e91a48', 'b69f2892ad0dbc8d0879a6a054b6deab9c5acedd4de6c6432492341f54f167a2')}
root=Path.cwd();work=root/'xr107-p2-capture-20261116-02';work.mkdir(exist_ok=False)
r={'task_id':TASK,'status':'FAIL_OR_UNVERIFIED','npu_launch':False,'device_runtime_probe':False,'official_hccl_launch':False,'replay':False,'commit':COMMIT,'executed_script_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
env=os.environ.copy();env.update(GIT_TERMINAL_PROMPT='0',GCM_INTERACTIVE='never')
stage='bridge-active-gate'
def run(label,argv,limit,data=None,cwd=None):
    start=time.monotonic()
    try:p=subprocess.run(argv,input=data,cwd=cwd,env=env,capture_output=True,timeout=limit)
    except subprocess.TimeoutExpired:
        (work/(label+'.timeout.json')).write_text(json.dumps({'effects':'UNKNOWN_READ_SAVED_STATE_NO_REPLAY'}));raise RuntimeError('TIMEOUT_READ_SAVED_STATE') from None
    (work/(label+'.stdout.log')).write_bytes(p.stdout);(work/(label+'.stderr.log')).write_bytes(p.stderr)
    (work/(label+'.receipt.json')).write_text(json.dumps({'exit_code':p.returncode,'seconds':round(time.monotonic()-start,3)}))
    assert p.returncode==0,'FAILED_'+label
    return p.stdout
try:
    con=sqlite3.connect((root/'bridge.sqlite3').resolve().as_uri()+'?mode=ro',uri=True,timeout=5);con.execute('PRAGMA query_only=ON')
    active=con.execute("SELECT task_id,iteration,state FROM tasks WHERE state IN ('RUNNING','QUEUED','WAITING','WAITING_ANSWER') AND task_id!=? LIMIT 20",(TASK,)).fetchall();con.close();assert not active,'OTHER_ACTIVE_TASK_REQUIRES_AUDIT'
    build=json.loads((root/'xr107-p2-build-20261116-02/build-transport-summary.json').read_text());assert build['status']==build['remote']['status']=='PASS' and build['remote']['runtime_validation']=='NOT_RUN'
    prior=root/'runs/xr-native107-p2-capture-20261116-01/1/execution'
    assert json.loads((prior/'execution.json').read_text())['exit_code']==1,'OLD_RECEIPT'
    old=(prior/'script.py').read_bytes();assert hashlib.sha256(old).hexdigest()=='a01a583f03c301bd035f7e9399e5f4b4bbbdfa3687dae42f7d1b0b1512f17c31','OLD_SCRIPT_SHA'
    try:compile(old,'prior','exec')
    except SyntaxError:pass
    else:raise AssertionError('OLD_SCRIPT_NOT_SYNTAX_FAILURE')
    assert not (root/'xr107-p2-capture-20261116-01').exists(),'OLD_EFFECTS_REQUIRE_AUDIT'
    stage='fixed-raw-git-blobs'
    repo=Path(SCRIPT_REPO)
    assert run('git-head',['git','rev-parse','FETCH_HEAD'],15,cwd=repo).decode().strip()==COMMIT,'SESSION_HEAD_CHANGED'
    for name,(blob,digest) in FILES.items():
        actual=run('git-id-'+name,['git','rev-parse',COMMIT+':native107_p2/'+name],15,cwd=repo).decode().strip();assert actual==blob,'BLOB_ID_'+name
        raw=run('git-blob-'+name,['git','cat-file','blob',blob],20,cwd=repo);assert hashlib.sha256(raw).hexdigest()==digest,'RAW_SHA_'+name
        if name.endswith('.py'):compile(raw,name,'exec')
        (work/name).write_bytes(raw)
    meta=json.loads((work/'capture-identity.json').read_text());assert meta['script_sha256']==FILES['runtime-capture.py'][1] and meta['base_script_sha256']==FILES['runtime-correctness.py'][1] and meta['checker_sha256']==FILES['check_npu_status.py'][1]
    opts=['-i',str(Path.home()/'.ssh/id_ed25519'),'-o','IdentitiesOnly=yes','-o','BatchMode=yes','-o','StrictHostKeyChecking=yes','-o','ConnectTimeout=15','-o','ConnectionAttempts=1']
    stage='fresh-remote-stage-directory'
    prepare="from pathlib import Path\nimport hashlib,json,os\nassert os.geteuid()==0 and hashlib.sha256(Path('/etc/machine-id').read_bytes()).hexdigest()=='78020a81dd869c8ca5a7ba9f4a18b25a9fead957b807f74e65793fced37b9af0'\np=Path("+repr(EXP)+")\nassert json.loads((p/'build-summary.json').read_text())['status']=='PASS'\nassert not (p/'p2-capture-20261116-02').exists(),'EXISTING_RUN_NO_REPLAY'\nassert hashlib.sha256((p/'p2-correctness-20261116-01/summary.json').read_bytes()).hexdigest()=='b544c1408554f3404571fe73972841ebb05682c0b3356cd54554c1a3b587e457','CORRECTNESS_SUMMARY_SHA'\ns=Path("+repr(DEST)+");assert not s.exists(),'EXISTING_STAGE_AUDIT_FIRST'\ns.mkdir(mode=0o750)\nprint('FRESH_CPU_STAGE')\n"
    run('ssh-prepare',['ssh',*opts,'root@141.61.73.107','python3 -'],40,data=prepare.encode())
    stage='scp-plaintext-scripts'
    run('scp-scripts',['scp',*opts,*[str(work/n) for n in FILES],'root@141.61.73.107:'+DEST+'/'],70)
    stage='verify-remote-stage'
    verify="from pathlib import Path\nimport hashlib,json\np=Path("+repr(DEST)+")\ne="+repr({n:v[1] for n,v in FILES.items()})+"\nassert all(hashlib.sha256((p/n).read_bytes()).hexdigest()==h for n,h in e.items())\nfor n in e:\n if n.endswith('.py'):compile((p/n).read_bytes(),n,'exec')\nprint(json.dumps({'status':'STAGED_SHA_PASS','files':len(e),'npu_launch':False}))\n"
    saved=json.loads(run('ssh-verify',['ssh',*opts,'root@141.61.73.107','python3 -'],40,data=verify.encode()));assert saved['status']=='STAGED_SHA_PASS' and saved['npu_launch'] is False
    r.update(remote_stage=DEST,files_sha256={n:v[1] for n,v in FILES.items()})
    stage='execute-ten-capture-groups'
    bootstrap="from pathlib import Path\nimport os,hashlib\np=Path("+repr(DEST)+")\ne="+repr({n:v[1] for n,v in FILES.items()})+"\nassert all(hashlib.sha256((p/n).read_bytes()).hexdigest()==h for n,h in e.items()),'STAGED_SCRIPT_SHA'\nassert not Path("+repr(EXP+'/p2-capture-20261116-02')+").exists(),'SAVED_RUN_EXISTS_NO_REPLAY'\nos.execv('/usr/bin/python3',['/usr/bin/python3',str(p/'runtime-capture.py')])\n"
    (work/'bootstrap.py').write_text(bootstrap);r['npu_launch']=None
    start=time.monotonic()
    try:p=subprocess.run(['ssh',*opts,'root@141.61.73.107','python3 -'],input=bootstrap.encode(),env=env,capture_output=True,timeout=3620)
    except subprocess.TimeoutExpired as e:
        (work/'ssh-capture.stdout.log').write_bytes(e.stdout or b'');(work/'ssh-capture.stderr.log').write_bytes(e.stderr or b'')
        (work/'ssh-capture.receipt.json').write_text(json.dumps({'effects':'UNKNOWN_READ_SAVED_STATE_NO_REPLAY','timed_out':True}))
        raise RuntimeError('SSH_TIMEOUT_UNKNOWN_READ_SAVED_STATE') from None
    (work/'ssh-capture.stdout.log').write_bytes(p.stdout);(work/'ssh-capture.stderr.log').write_bytes(p.stderr)
    (work/'ssh-capture.receipt.json').write_text(json.dumps({'exit_code':p.returncode,'timed_out':False,'seconds':round(time.monotonic()-start,3)}))
    r['ssh_exit_code']=p.returncode;stage='validate-capture-business-receipt'
    remote=json.loads(p.stdout.decode('utf-8'));(work/'remote-summary.json').write_text(json.dumps(remote,indent=2)+'\n')
    assert remote['task_id']==TASK and remote['script_sha256']==FILES['runtime-capture.py'][1] and remote['base_script_sha256']==FILES['runtime-correctness.py'][1],'REMOTE_SOURCE_IDENTITY'
    r.update(remote=remote,status=remote['status'],npu_launch=remote['npu_launch'])
    if remote['status']=='PASS':
        assert p.returncode==0 and remote['phase']=='capture' and remote['revision']=='5c25e0f03a585b6f07cc69608eb21b589c6b82f7'
        assert remote['capture_groups_passed']==10 and len(remote['groups'])==10 and remote['actual_aiv_cores']==[64,64] and remote['devices']==[0,1]
        assert remote['namespace_exit']==0 and not remote['timed_out'] and not remote['monitor_error'] and not remote['cleanup_actions'] and not remote['own_residual_children'] and remote['original_configuration_preserved']
        assert remote['qualification_reused'] and not remote['official_hccl_launch'] and remote['clock']=='NOT_VERIFIED'
        assert remote['correctness_gate']['summary_sha256']=='b544c1408554f3404571fe73972841ebb05682c0b3356cd54554c1a3b587e457'
        for g in remote['groups']:
            assert g['status']=='PASS' and g['npu_launch'] is True and g['after']['occupancy']=='IDLE' and len(g['ranks'])==2
            assert all(x['business_dispatches']==21 and x['sum2_buffers']==22 and x['msprof_dispatch_tasks']==21 and x['measured_generation']==21 and x['input_device_readback']=='PASS' and x['flags_cleared'] and x['lifecycle']=='PASS' for x in g['ranks'])
except BaseException as e:
    r.update(status='FAIL_OR_UNVERIFIED',blocked_stage=stage,error_type=type(e).__name__)
    if isinstance(e,(AssertionError,RuntimeError)):r['reason_code']=str(e)[:160]
text=json.dumps(r,ensure_ascii=True,separators=(',',':'));assert len(text.encode())<25000
(work/'capture-transport-summary.json').write_text(text+'\n');print(text)
