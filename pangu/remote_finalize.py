"""Verify immutable source and publish per-server offline reproduction launchers."""
import datetime
import hashlib
import json
import math
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tarfile

alias, commit, success_path = sys.argv[1:]
assert alias in ('ma-0','ma-1','ma-2')
work = pathlib.Path.home() / 'work'
repo = work / 'pangu_sophon_pytorch' / alias / commit
archive = work / ('pangu-' + alias + '-' + commit + '.tar')
marker = json.loads((repo / '.cross-zone-deployment.json').read_text())
assert marker['commit'] == commit
assert hashlib.sha256(archive.read_bytes()).hexdigest() == marker['archive_sha256']
checked = 0
with tarfile.open(archive) as tar:
    for member in tar:
        path = repo / member.name
        if member.isfile():
            assert path.is_file(), 'Missing deployed source file'
            assert hashlib.sha256(path.read_bytes()).digest() == hashlib.sha256(tar.extractfile(member).read()).digest(), 'Deployed source file changed'
            checked += 1
        elif member.issym():
            assert path.is_symlink() and os.readlink(path) == member.linkname

success = pathlib.Path(success_path)
saved = json.loads((success / 'result.json').read_text())
assert saved['source_commit'] == commit and saved['exit_code'] == 0
assert not saved['timed_out'] and saved['termination_confirmed']
iterations = []
for line in (success / 'supervisor.log').read_text(errors='replace').splitlines():
    match = re.search(r'\biteration\s+(\d+)\s*/\s*(\d+)\s*\|',line)
    if not match: continue
    losses = {name.strip():float(value) for name,value in re.findall(r'(?:^|\|)\s*([^|:]*\bloss)\s*:\s*([+\-\d.Ee]+)',line)}
    grad = re.search(r'\bgrad norm:\s*([+\-\d.Ee]+)',line)
    assert losses and all(math.isfinite(v) for v in losses.values())
    assert grad and math.isfinite(float(grad.group(1)))
    iterations.append({'iteration':int(match.group(1)),'total':int(match.group(2)),
                       'losses':losses,'grad_norm':float(grad.group(1))})
assert {i['iteration'] for i in iterations} == {1,2}

profile = repo.parent / 'validated-gqa'
assert not profile.exists(), 'Reproduction profile already exists; preserved for inspection'
profile.mkdir()
for name in ['synthetic-offline-data','offline-tokenizer','runtime','guard-bin']:
    shutil.copytree(success / name,profile / name)
idle = profile / 'check_idle.py'
idle.write_text('''import concurrent.futures,json,re,subprocess,sys
def probe(device):
 try:
  p=subprocess.run(['npu-smi','info','-t','proc-mem','-i',str(device)],capture_output=True,text=True,timeout=8)
  if p.returncode: state='indeterminate'
  elif re.search(r'Process id\\s*:',p.stdout): state='busy'
  elif re.search(r'NPU ID\\s*:\\s*'+str(device)+r'(?:\\s|$)',p.stdout): state='idle'
  else: state='indeterminate'
 except Exception: state='indeterminate'
 return {'id':device,'state':state}
with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool: devices=list(pool.map(probe,range(8)))
ready=all(d['state']=='idle' for d in devices)
print(json.dumps({'safe_to_use':ready,'devices':devices}))
sys.exit(0 if ready else 1)
''')

original_repo = work / 'pangu_sophon_pytorch' / 'ma-0' / commit
body = (success / 'run_farescale_gqa_offline.sh').read_text()
body = body.replace(str(original_repo),str(repo)).replace(str(success),str(profile))
body = body.replace(str(profile / 'rank-logs'),'"$run_log/rank-logs"')
body = body.replace(str(profile / 'console.log'),'"$run_log/console.log"')
prefix = '''#!/usr/bin/env bash
set -eo pipefail
profile_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
python3 "$profile_dir/check_idle.py"
run_log="$profile_dir/runs/$(date +%Y%m%d-%H%M%S)-$$"
mkdir -p "$run_log"
export BASE_DATA_PATH="$profile_dir/synthetic-offline-data"
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 HF_DATASETS_OFFLINE=1 WANDB_MODE=offline TOKENIZERS_PARALLELISM=false
'''
prefix += 'export PYTHONPATH="$profile_dir/runtime:' + str(repo) + ':${PYTHONPATH:-}"\n'
prefix += 'export PATH="$profile_dir/guard-bin:$PATH"\n'
launcher = profile / 'run_farescale_gqa_offline.sh'
launcher.write_text(prefix + body)
launcher.chmod(0o700)
syntax = subprocess.run(['bash','-n',str(launcher)],capture_output=True,text=True,timeout=10)
assert syntax.returncode == 0, 'Reproduction launcher syntax validation failed'
summary = {'alias':alias,'source_commit':commit,'source_directory':str(repo),'source_files_verified':checked,
           'source_unchanged':True,'launcher':str(launcher),'profile':str(profile),
           'validated_on':'ma-0','validation_run':str(success),'synthetic_data':True,
           'iterations':iterations,'exit_code':saved['exit_code'],'idle_guard_installed':True,
           'runtime_changes':'process-local only','public_environment_modified':False,
           'known_shutdown_warning':'Python resource_tracker shared-memory cleanup warnings; process exited zero'}
(profile / 'validation.json').write_text(json.dumps(summary,indent=2)+'\n')
print(json.dumps(summary))
