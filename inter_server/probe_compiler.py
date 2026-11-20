"""Stage the pinned SHMEM dependency and run bounded compiler probes."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'alltoall'))
from ssh_transport import run

REV='a5e55f750d506d0fcfe01b5959299cbf019f762a'
REPORT=Path('D:/project/inter-server-sst-reports/b4f4660a-6144-453c-99f7-339b40f56c54')
SDK=Path('D:/project/inter-server-sst-deps/shmem-a5e55f7')
SDK.parent.mkdir(parents=True,exist_ok=True)
with (REPORT/'dependency.log').open('wb') as log:
    def git(*args,timeout=180):
        subprocess.run(['git',*map(str,args)],stdout=log,stderr=subprocess.STDOUT,check=True,timeout=timeout)
    assert not SDK.exists(),'Dependency checkout already exists; inspect before retry'
    git('init',SDK)
    git('-C',SDK,'fetch','--depth=1','https://gitcode.com/cann/shmem.git',REV)
    git('-C',SDK,'checkout','--detach','FETCH_HEAD')
actual=subprocess.check_output(['git','-C',str(SDK),'rev-parse','HEAD'],timeout=15).decode().strip()
assert actual==REV
archive=REPORT/'shmem.tar.gz'
with archive.open('wb') as f:
    subprocess.run(['git','-C',str(SDK),'archive','--format=tar.gz','--prefix=shmem/',REV,'include','src','LICENSE'],stdout=f,stderr=subprocess.PIPE,check=True,timeout=30)
hostroot='/mnt/share/tilexr-sim-jobs/b4f4660a-6144-453c-99f7-339b40f56c54/inter-server-sdk-probe'
run('import json; from pathlib import Path; p=Path('+repr(hostroot)+'); p.mkdir(); print(json.dumps({"reserved":str(p)}))')
subprocess.run(['scp','-q','-o','BatchMode=yes','-o','StrictHostKeyChecking=yes',str(archive),'root@141.61.94.58:'+hostroot+'/shmem.tar.gz'],capture_output=True,check=True,timeout=120)
result=run(Path(__file__).with_name('probe_compiler_remote.py').read_text(encoding='utf-8'),timeout=600)
result['dependency_archive_sha256']=hashlib.sha256(archive.read_bytes()).hexdigest()
(REPORT/'compiler-probes.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
print(json.dumps(result,ensure_ascii=True))
