"""Compile small toolchain/SHMEM API probes, never claim actual module validation."""
import json
from pathlib import Path
import subprocess

NAME='tilexr-cpu-b4f4660a-lr8'
ROOT='/work/inter-server-sdk-probe'


def command(argv,timeout=30):
    return subprocess.check_output(argv,stderr=subprocess.PIPE,timeout=timeout).decode('utf-8')


info,=json.loads(command(['docker','inspect',NAME]))
assert info['Id']=='1f98e70b00ed8b171fea3b06152a7fe9507a2270e9dee05c72015af5b995c2f4'
assert not info['State']['Running'] and info['HostConfig']['Runtime']=='runc' and not info['HostConfig']['Devices']
command(['docker','start',NAME])
inner='''import glob,hashlib,json,re,subprocess,tarfile
from pathlib import Path,PurePosixPath
root=Path('/work/inter-server-sdk-probe')
assert not glob.glob('/dev/davinci*') and not glob.glob('/dev/devmm*') and not glob.glob('/dev/hisi_hdc*')
with tarfile.open(root/'shmem.tar.gz') as stream:
 for member in stream.getmembers():
  rel=PurePosixPath(member.name)
  assert not rel.is_absolute() and '..' not in rel.parts
  assert member.isfile() or member.isdir()
 stream.extractall(root,filter='data')
cann=Path('/opt/sim/Ascend/cann-9.1.0')
compiler=cann/'tools/bisheng_compiler/bin/bisheng'
sdk=root/'shmem'
sources={
 'toolchain': '#include "kernel_operator.h"\\nextern "C" __global__ __aicore__ void sst_toolchain_probe(GM_ADDR out) { *reinterpret_cast<__gm__ unsigned*>(out)=AscendC::GetBlockIdx(); }\\n',
 'shmem_api': '#include "kernel_operator.h"\\n#include "shmem.h"\\n#include "device_simt/shmem_device_simt_instance.h"\\nextern "C" __global__ __aicore__ void sst_shmem_probe(GM_ADDR out) { aclshmemx_instance_ctx_set(0); auto* p=aclshmemi_udma_qp_info_fetch(); *reinterpret_cast<__gm__ unsigned*>(out)=p->qp_num; aclshmemx_udma_qp_quiet(8,0); }\\n'}
includes=[cann/'aarch64-linux/asc/include',cann/'aarch64-linux/include',cann/'aarch64-linux/pkg_inc',cann/'aarch64-linux/pkg_inc/runtime',cann/'compiler/tikcpp',cann/'compiler/tikcpp/tikcfw',cann/'compiler/tikcpp/tikcfw/impl',cann/'compiler/tikcpp/tikcfw/interface',sdk/'include',sdk/'src',sdk/'src/device',sdk/'src/device_simt']
common=['-O2','-std=gnu++17','-DMULTI_INSTANCE','-DCATLASS_ARCH=3510','-DUSE_SIMT','-DL2_CACHE_HINT','--cce-auto-infer-kernel-type=false']+['-I'+str(p) for p in includes]
variants={'asc':['-xasc','--npu-arch=dav-3510','--cce-aicore-only'],'cce':['-xcce','--cce-aicore-arch=dav-c310-vec','--cce-aicore-only']}
result={'scope':'compiler and SHMEM API probes only; actual inter_server_send not compiled or run','probes':[],'shmem_revision':'a5e55f750d506d0fcfe01b5959299cbf019f762a'}
for name,source in sources.items():
 path=root/(name+'.cpp');path.write_text(source)
 for variant,flags in variants.items():
  out=root/(name+'-'+variant+'.o')
  argv=[str(compiler),*flags,*common,'-c',str(path),'-o',str(out)]
  proc=subprocess.run(argv,capture_output=True,timeout=120)
  log=root/(name+'-'+variant+'.log');log.write_bytes(proc.stdout+proc.stderr)
  errors=[line[:1000] for line in (proc.stdout+proc.stderr).decode('utf-8','replace').splitlines() if re.search(r'(fatal error:|error:|unsupported|unknown argument)',line)]
  result['probes'].append({'name':name,'variant':variant,'command':argv,'exit_code':proc.returncode,'errors':errors[:8],'log':str(log),'object_sha256':hashlib.sha256(out.read_bytes()).hexdigest() if out.exists() else None})
(root/'compiler-probes.json').write_text(json.dumps(result,indent=2))
print(json.dumps(result,ensure_ascii=True))
'''
try:
    result=json.loads(command(['docker','exec',NAME,'bash','-c','source /opt/sim/cann-env.sh; exec python3 -c "$1"','bash',inner],timeout=540))
finally:
    command(['docker','stop','--time','10',NAME])
result['container_stopped']=True
print(json.dumps(result,ensure_ascii=True))
