import base64, concurrent.futures, hashlib, io, json, subprocess, zipfile
from pathlib import Path
from collect_dispatch_opt_ab import RUNS

ROOT='/root/ubx-dispatch-global-ab-d23ffa56'
REMOTE=r'''
import base64,hashlib,io,json,zipfile
from pathlib import Path
root=Path(ROOT)
files={};configs=[]
for rel in ['manifest.json','bs32/expert_ids.bin','bs32/expected.json','bs32/expected_rows.bin']:
 p=root/'inputs/ep32-full'/rel;assert p.is_file();files['inputs/ep32-full/'+rel]=p.read_bytes()
route=files['inputs/ep32-full/bs32/expert_ids.bin']
assert len(route)==32768 and hashlib.sha256(route).hexdigest()=='2a58c479564c82f5185d42bfa3608a5e9f2128e0125c921a1d6ab4ec18f63b24'
for phase,variant,run in RUNS:
 case=root/variant/run/'output'
 rankfiles=sorted(case.glob('rank*/rounds.jsonl'));assert len(rankfiles)==16
 rank_configs=[]
 for f in rankfiles:
  row=json.loads(f.read_text().splitlines()[0]);assert row['verified']
  rank_configs.append({k:v for k,v in row.items() if k not in ['cores','barrier']})
 configs.append(dict(phase=phase,variant=variant,run_id=run,rank_configuration_samples=rank_configs))
files['config/observed-first-round-'+HOST+'.json']=json.dumps(configs,indent=2).encode()
if HOST=='90.90.93.81':
 old=Path('/root/ubx-control-profile-ep32-d23ffa56/inputs/routes-ep32-bs32-e384-seed20261127.bin')
 assert old.read_bytes()==route
 files['inputs/previous-kernel/routes-ep32-bs32-e384-seed20261127.bin']=old.read_bytes()
 for variant in ['control','global']:
  source=root/variant/'source/dispatch/dispatch-release'
  for rel in ['testcase/generate_cases.py','tests/hardware/dispatch_fixture.h','tests/hardware/run_dispatch_node.py']:
   files['reproduction/'+variant+'/'+rel]=(source/rel).read_bytes()
manifest=[dict(path=k,bytes=len(v),sha256=hashlib.sha256(v).hexdigest()) for k,v in sorted(files.items())]
b=io.BytesIO()
with zipfile.ZipFile(b,'w',zipfile.ZIP_DEFLATED) as z:
 for k,v in files.items():z.writestr(k,v)
assert len(b.getvalue())<32*1024*1024
print(json.dumps(dict(archive_b64=base64.b64encode(b.getvalue()).decode(),manifest=manifest,route_sha256=hashlib.sha256(route).hexdigest())))
'''

def main():
 out=Path('D:/work/UBX攻关/UBX_Control/dispatch-kernels-inputs-and-config');out.mkdir(parents=True,exist_ok=True)
 keys=[p for p in Path('C:/Users/l00826879/.ssh').glob('*deploy*') if p.is_file() and p.suffix not in ['.pub','.old','.bak'] and b'PRIVATE KEY' in p.read_bytes()];assert keys
 def collect(host):
  argv=['ssh','-o','BatchMode=yes','-o','IdentitiesOnly=yes','-o','ConnectTimeout=10','-o','StrictHostKeyChecking=no','-o','UserKnownHostsFile=NUL']
  for k in keys:argv+=['-i',str(k)]
  script='ROOT='+repr(ROOT)+'\nHOST='+repr(host)+'\nRUNS='+repr(RUNS)+'\n'+REMOTE
  p=subprocess.run(argv+['root@'+host,'python3 -'],input=script,text=True,capture_output=True,timeout=180)
  (Path.cwd()/('export-input-'+host+'.stderr.log')).write_text(p.stderr)
  assert p.returncode==0,host
  return json.loads(p.stdout)
 with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(collect,['90.90.93.81','90.90.93.83']))
 assert results[0]['route_sha256']==results[1]['route_sha256']
 for result in results:
  with zipfile.ZipFile(io.BytesIO(base64.b64decode(result['archive_b64']))) as z:
   for info in z.infolist():
    path=Path(info.filename);assert not path.is_absolute() and '..' not in path.parts
    payload=z.read(info);dest=out/path;dest.parent.mkdir(parents=True,exist_ok=True)
    if dest.exists():assert dest.read_bytes()==payload
    else:dest.write_bytes(payload)
  for item in result['manifest']:
   assert hashlib.sha256((out/item['path']).read_bytes()).hexdigest()==item['sha256']
 # Preserve the original exported configuration records without replacing them by reconstructed values.
 for name,label in [('control-vs-global-ep32-bs32-profiling','previous-kernel'),('dispatch-opt-3094325-control-vs-global-ep32-bs32-profiling','dispatch-opt')]:
  src=out.parent/name/'experiment.json';assert src.is_file()
  dest=out/'config'/label/'experiment.json';dest.parent.mkdir(parents=True,exist_ok=True);dest.write_bytes(src.read_bytes())
 config=dict(hosts=[dict(address='90.90.93.81',devices=list(range(16)),gid_index=1,driver='25.9.0'),dict(address='90.90.93.83',devices=list(range(16)),gid_index=2,driver='25.9.0.1')],cann='/usr/local/Ascend/cann-9.2.T101',ep=32,batch=32,experts=384,hidden=3584,topk=8,route_seed=20261127,route_storage='little-endian uint32 [rank=32,batch=32,topk=8]',route_sha256=results[0]['route_sha256'],route_bytes=32768,dispatch_opt=dict(warmup=10,measured=20,profile='full',native_msprof=False,clock_aligned=False,runs=RUNS,coordinator='90.90.93.81',tick_hz=1000000000,role_partition=[16,8,16,16],rdma_environment=dict(TILEXR_RDMA_PORT='1',TILEXR_RDMA_MTU='4096',TILEXR_RDMA_TIMEOUT_MS='30000'),nic_mapping='hrn5_(device//2)',global_preupload_outside_timing=True),previous_kernel=dict(warmup=5,measured=20,profile=True,native_msprof=False,clock_start=True,configuration_source='config/previous-kernel/experiment.json'),notes=['Only BS32 inputs exported.','mt2 in dispatch-opt run names is an inherited directory label, not a parameter.','Port numbers were dynamically allocated per run; choose unused ports when reproducing.','No SSH credentials or full process environment are included.'])
 (out/'config/comparison-config.json').write_text(json.dumps(config,indent=2),encoding='utf-8')
 (out/'README.txt').write_text('此包保存两版 kernel 共用的实际 BS32 路由输入，以及 dispatch-opt 的 expected.json、expected_rows.bin 正确性参考。输入哈希经过 .81/.83 两端核对，也与上一版服务器路由文件逐字节一致。\n输入布局：little-endian uint32 [32 rank,32 token,8 expert IDs]，共8192项、32768字节。\nconfig 保存两次实验原有 experiment.json、对比配置和本次四次运行各rank第一轮记录的实际参数。\nreproduction 保存实测版本的输入生成器、数据夹具和启动器；payload/weights 等由夹具按rank/token/round生成，不是独立输入文件。expected_rows.bin 是该生成器的参考数据，不是NPU完整输出或payload输入。\n运行时仍需要对应commit的kernel/host产物和通信库；本包不包含二进制算子。全局路由预上传成本不计入kernel。上一版与新版预热和启动同步方式不同，比较时注意这些差异。\n每个文件的SHA256见files.sha256.json。\n',encoding='utf-8')
 entries=[dict(path=p.relative_to(out).as_posix(),bytes=p.stat().st_size,sha256=hashlib.sha256(p.read_bytes()).hexdigest()) for p in sorted(out.rglob('*')) if p.is_file() and p.name!='files.sha256.json']
 (out/'files.sha256.json').write_text(json.dumps(entries,indent=2),encoding='utf-8')
 archive=out.with_suffix('.zip')
 with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED) as z:
  for p in sorted(out.rglob('*')):
   if p.is_file():z.write(p,p.relative_to(out).as_posix())
 print(json.dumps(dict(directory=str(out),archive=str(archive),archive_bytes=archive.stat().st_size,archive_sha256=hashlib.sha256(archive.read_bytes()).hexdigest(),file_count=len(entries)+1,input_bytes=32768,input_sha256=results[0]['route_sha256'],both_hosts_identical=True,previous_kernel_input_identical=True),ensure_ascii=False))
if __name__=='__main__':main()
