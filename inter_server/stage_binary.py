"""Stage a hash-pinned blue-built object; never compile or launch SST here."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'alltoall'))
from ssh_transport import run
from artifact_tools import unpack

package = Path(__file__).with_name('resources') / 'native_binary'
assert package.is_dir(), 'No frozen native binary resource has been published'
manifest = json.loads((package / 'artifact.json').read_text(encoding='utf-8'))
tag = manifest['candidate_commit'] + '-' + manifest['binary_sha256'][:12]
report = Path('D:/project/inter-server-sst-reports/b4f4660a-6144-453c-99f7-339b40f56c54')
local = report / 'binary-staging' / tag
unpack(package, local)
host_root = '/mnt/share/tilexr-sim-jobs/b4f4660a-6144-453c-99f7-339b40f56c54'
relative = 'inter-server-native/' + tag
destination = host_root + '/' + relative
run('import json; from pathlib import Path; p=Path(' + repr(destination) +
    '); p.mkdir(parents=True,exist_ok=True); print(json.dumps({"reserved":str(p)}))')
for name in ('artifact.json', 'build-record.json', 'inter_server_send.o.gz'):
    subprocess.run(['scp', '-q', '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes',
        str(package / name), 'root@141.61.94.58:' + destination + '/' + name],
        capture_output=True, check=True, timeout=120)
source = '''import gzip,hashlib,json
from pathlib import Path
root=Path(DESTINATION)
m=json.loads((root/'artifact.json').read_text())
assert m['candidate_commit']==COMMIT and m['binary_sha256']==OBJECT_SHA
compressed=(root/'inter_server_send.o.gz').read_bytes()
assert hashlib.sha256(compressed).hexdigest()==m['compressed_sha256']
assert len(compressed)==m['compressed_bytes']
assert hashlib.sha256((root/'build-record.json').read_bytes()).hexdigest()==m['build_record_sha256']
with gzip.open(root/'inter_server_send.o.gz','rb') as f: raw=f.read(m['binary_bytes']+1)
assert len(raw)==m['binary_bytes'] and raw.startswith(b'\\x7fELF')
assert hashlib.sha256(raw).hexdigest()==m['binary_sha256']
target=root/'inter_server_send.o'
if target.exists(): assert hashlib.sha256(target.read_bytes()).hexdigest()==m['binary_sha256']
else:
 with target.open('xb') as f: f.write(raw)
print(json.dumps({'status':'blue_object_staged_not_run','candidate_commit':m['candidate_commit'],
 'kernel_sha256':m['binary_sha256'],'entry':m['entry'],'host_object':str(target),
 'container_object':CONTAINER_PATH,'compiled_in_green':False,'sst_started':False}))
'''
source = ('DESTINATION=' + repr(destination) + '\nCOMMIT=' + repr(manifest['candidate_commit']) +
          '\nOBJECT_SHA=' + repr(manifest['binary_sha256']) +
          '\nCONTAINER_PATH=' + repr('/work/' + relative + '/inter_server_send.o') + '\n' + source)
result = run(source, timeout=45)
(report / ('binary-stage-' + tag + '.json')).write_text(json.dumps(result, indent=2), encoding='utf-8')
print(json.dumps(result, ensure_ascii=True))
