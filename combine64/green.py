# Copyright (c) 2026 TileXR Project.
"""Run one bounded task phase through the green Windows SSH identity."""
import argparse,base64,hashlib,json,os,subprocess
from pathlib import Path
SESSION='fac4ae28-1586-42fe-b55b-ff09982f17ec'
def main():
    p=argparse.ArgumentParser();p.add_argument('--args-file',required=True,type=Path)
    args=json.loads(p.parse_args().args_file.read_text(encoding='utf-8'))
    assert args['action'] in ('prepare','smoke','qualify-local','local','return','qualify-return','instrument','repair-profile-send','pipeline-build','qualify-pipeline','pipeline','calibrate','matrix-case','independent-bs','trace-pipeline','debug-pipeline','pending-layout','cq-snapshot','cq-snapshot-decode','cq-sq-snapshot','sq-align-send16','sq-align-regression','sq-align-snapshot','inter-align-regression','all-align-snapshot','cq-visible-joint16','dcci-pipeline','snapshot-pipeline','isolate-pipeline','cq-compiler-barrier','status','batch-status','stop')
    directory=Path(__file__).parent
    names=('cq_visible_joint_build.py','all_sq_align_snapshot.py','inter_sq_align_build.py','sq_align_snapshot.py','sq_align_pipeline_build.py','sq_align_build.py','cq_snapshot_decode.py','cq_snapshot.py','fixture.py','cq_compiler_barrier.py','leader_barrier.h','isolate_inter_pipeline.py','snapshot_pipeline.py','dcci_pipeline.py','pending_layout_remote.py','debug_pipeline.py','trace_pipeline.py','install_prepared.py','prefill.cpp','exports.py','check_local.py','profile.py','clock_evidence.json','instrument.py','profile_flush.h','clock_probe.cpp','clock_fixture.py','check_clock.py','return_fixture.py',
           'check_return.py','pipeline_kernel.cpp','pipeline_fixture.py','check_pipeline.py','pipeline_build.sh')
    files={name:(directory/name).read_text(encoding='utf-8') for name in names}
    request=dict(args,files=files)
    if args['action']=='independent-bs' and args.get('start_index',0)==0:
        asset=directory/'data'/('64p-t%d'%args['tokens'])
        request['portable']=dict(identity=json.loads((asset/'identity.json').read_text()),abis=json.loads((asset/'send-abis.json').read_text()),archive=base64.b64encode((asset/'fixture.tar.gz').read_bytes()).decode())
    code=(directory/'host.py').read_text(encoding='utf-8')+'\nraise SystemExit(main('+repr(request)+'))\n'
    logs=Path(os.environ['LOCALAPPDATA'])/'CrossZone/tilexr'/SESSION;logs.mkdir(parents=True,exist_ok=True)
    stem=args['action']+'-'+str(args.get('tokens',4))+'-'+str(args.get('phase',args.get('stage','')))
    if args['action']=='independent-bs':stem+='start'+str(args.get('start_index',0))
    if args['action']=='cq-compiler-barrier':stem+=args.get('attempt','')
    seconds={'prepare':600,'smoke':1700,'qualify-local':2400,'local':1100,'instrument':700,'repair-profile-send':1800,'return':1250,'qualify-return':10000,'pipeline-build':400,'qualify-pipeline':3600,'pipeline':3300,'calibrate':1100,'matrix-case':7200,'independent-bs':19000,'trace-pipeline':950,'debug-pipeline':450,'pending-layout':120,'cq-snapshot':1100,'cq-snapshot-decode':180,'cq-sq-snapshot':1400,'sq-align-send16':1700,'sq-align-regression':6500,'sq-align-snapshot':1600,'inter-align-regression':6000,'all-align-snapshot':1600,'cq-visible-joint16':1900,'dcci-pipeline':2100,'snapshot-pipeline':1900,'isolate-pipeline':1900,'cq-compiler-barrier':600,'status':60,'batch-status':60,'stop':90}[args['action']]
    if args['action']=='independent-bs':seconds={16:12000,32:18000,64:24000,128:36000}[args['tokens']]
    cmd=['ssh','-T','-i',str(Path(os.environ['USERPROFILE'])/'.ssh/id_ed25519'),'-p','22','-o','BatchMode=yes',
         '-o','IdentitiesOnly=yes','-o','StrictHostKeyChecking=yes','-o','ConnectTimeout=12',
         'root@141.61.94.26','timeout --kill-after=20 '+str(seconds)+' python3 -']
    proc=subprocess.run(cmd,input=code.encode(),capture_output=True,timeout=seconds+40)
    (logs/(stem+'.stdout')).write_bytes(proc.stdout);(logs/(stem+'.stderr')).write_bytes(proc.stderr)
    if args['action'] in ('sq-align-send16','sq-align-regression','inter-align-regression','all-align-snapshot','cq-visible-joint16'):assert len(proc.stdout)<7000
    if proc.stdout.strip():
        records=[json.loads(line) for line in proc.stdout.splitlines() if line.strip()]
        assert len(proc.stdout)<45000
        if args['action'] in ('sq-align-regression','inter-align-regression'):
            keys=('qualified','operator_qualified','run','platform_simulated_us','profiling_operator_us','sst_wall_seconds','wall_seconds','kernel_sha256','kernel_compatibility_variant','compatibility_inter_source_sha256','profiling_lanes','profiling_dropped')
            records=[dict(case=Path(row['case']).name,**{k:row[k] for k in keys if k in row}) if 'case' in row else {k:row[k] for k in ('prepare_exit','stage','log') if k in row} for row in records]
        result=json.dumps({'records':records,'ssh_exit':proc.returncode},separators=(',',':'))
        if args['action'] in ('sq-align-send16','sq-align-regression','inter-align-regression','all-align-snapshot','cq-visible-joint16'):assert len(result.encode())<3500
        print(result)
    else:print(json.dumps({'ssh_exit':proc.returncode,'stage':stem,'evidence':str(logs)}))
    return proc.returncode
if __name__=='__main__':raise SystemExit(main())
