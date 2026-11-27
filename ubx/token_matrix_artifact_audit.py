import json,subprocess
from pathlib import Path
keys=[p for p in Path('C:/Users/l00826879/.ssh').glob('*deploy*') if p.is_file() and p.suffix not in ['.pub','.old','.bak'] and b'PRIVATE KEY' in p.read_bytes()];assert keys
ssh=['ssh','-o','BatchMode=yes','-o','IdentitiesOnly=yes','-o','ConnectTimeout=10','-o','StrictHostKeyChecking=no','-o','UserKnownHostsFile=NUL']
for k in keys:ssh+=['-i',str(k)]
remote=r'''
import hashlib,json,os
from pathlib import Path
old=Path('/root/ubx-control-profile-ep32-d23ffa56/control');new=Path('/root/ubx-token-merge-matrix-d23ffa56');rows=[]
for label,root in [('old',old),('new',new)]:
 files={}
 for name in ['on/lib/libtile-comm.so','on/lib/libtilexr-ep.so','on/lib/libtilexr-diagnostics.so','on/lib/tilexr/kernels/tilexr_ep_ubx_kernel.o','source/src/ep/kernels/tilexr_ep_ubx_kernel.h','source/src/ep/kernels/ep_ubx_rdma_send.h','source/src/ep/kernels/tilexr_ep_ubx_route_ops.h','source/src/ep/common/ep_ubx_window.h','source/src/include/tilexr/profiling/direct.h']:
  p=root/name
  if p.is_file():files[name]=dict(bytes=p.stat().st_size,sha256=hashlib.sha256(p.read_bytes()).hexdigest())
 cache=root/'on-build/CMakeCache.txt';settings=[]
 if cache.is_file():settings=[s for s in cache.read_text().splitlines() if any(s.startswith(k) for k in ['CMAKE_BUILD_TYPE:','CMAKE_CCE_COMPILER:','TILEXR_EP_ENABLE_PROFILING:','TILEXR_EP_UBX_FANOUT_COPIES:','TILEXR_EP_UBX_PACKET_TRACE:','ASCEND_HOME_PATH:'])]
 rows.append(dict(label=label,files=files,settings=settings))
print(json.dumps(dict(artifacts=rows,cann=os.path.realpath('/usr/local/Ascend/cann'))))
'''
p=subprocess.run(ssh+['root@90.90.93.81','python3 -'],input=remote,text=True,capture_output=True,timeout=90);Path('artifact-audit.stderr.log').write_text(p.stderr);assert p.returncode==0;print(p.stdout)
