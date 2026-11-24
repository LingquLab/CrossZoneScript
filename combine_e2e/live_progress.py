# Copyright (c) 2026 TileXR Project.
"""Read CPU progress and request the owned SST process's default status report."""
import argparse,json,os,subprocess
from pathlib import Path

HOST=r'''
import json,subprocess
from pathlib import Path
root=Path('/mnt/share/tilexr-sim-jobs/ea219fe0-2177-4850-84f0-c0da68a4a5fa/run-013cc3e-a1')
cid=json.loads((root/'identity.json').read_text())['container_id']
info=json.loads(subprocess.check_output(['docker','inspect',cid]))[0]
assert info['Image']=='sha256:19e063d4eebab9785ad24b175ee1567dfb24c1180698f67fe0ab32a731a19cd5'
assert info['HostConfig']['NetworkMode']=='none'
code=r"""
import json,os,re,signal,time
from pathlib import Path
case=Path('/work/cases/64p-t4-return-a1')
run=json.loads((case/'run.json').read_text())
result={'run_status':run['status'],'status_query_only':True,'signal_sent':False}
if run['status']=='running':
 candidates=[]
 for proc in Path('/proc').glob('[0-9]*'):
  try:
   name=(proc/'comm').read_text().strip()
   cmd=(proc/'cmdline').read_bytes().replace(b'\0',b' ').decode(errors='replace')
   if name.startswith('sstsim') and str(case/'test_UBusSim.py') in cmd:candidates.append((proc,cmd))
  except OSError:pass
 result['matching_sst_processes']=len(candidates)
 if len(candidates)==1:
  proc,cmd=candidates[0]
  stat=(proc/'stat').read_text().split()
  before=int(stat[13])+int(stat[14])
  started=time.monotonic()
  log=case/'sim.log'
  offset=log.stat().st_size
  status=dict(line.split(':',1) for line in (proc/'status').read_text().splitlines() if ':' in line)
  caught=int(status.get('SigCgt','0').strip(),16)&(1<<(int(signal.SIGUSR1)-1))
  script=(case/'test_UBusSim.py').read_text()
  custom='--sigusr1' in cmd or '--disable-signal-handlers' in cmd or 'sigusr1' in script.lower()
  result.update(pid=int(proc.name),status_handler_installed=bool(caught),custom_signal_configuration=custom)
  if caught and not custom:
   try:os.kill(int(proc.name),signal.SIGUSR1);result['signal_sent']=True
   except ProcessLookupError:result['process_exited_during_query']=True
  time.sleep(2)
  try:
   stat=(proc/'stat').read_text().split()
   after=int(stat[13])+int(stat[14])
   result['sample_wall_seconds']=time.monotonic()-started
   result['sample_cpu_seconds']=(after-before)/os.sysconf('SC_CLK_TCK')
  except OSError:result['process_exited_during_query']=True
  with log.open('rb') as f:
   f.seek(offset);new=f.read(32768).decode(errors='replace')
  lines=[line[:250] for line in new.splitlines() if re.search(r'(?i)simul|current|time|rank|status',line)]
  result['new_status_lines']=lines[-8:]
else:
 p=case/'phase-result.json'
 if p.is_file():
  d=json.loads(p.read_text());result['stage_result']={k:d[k] for k in ('qualified','runner_exit','sim_us','model_errors','checked') if k in d}
print(json.dumps(result,separators=(',',':')))
"""
p=subprocess.run(['docker','exec',cid,'python3','-c',code],capture_output=True,timeout=25)
(root/'live-return-probe.stdout').write_bytes(p.stdout)
(root/'live-return-probe.stderr').write_bytes(p.stderr)
assert p.returncode==0,'probe_failed_see_task_local_log'
result=json.loads(p.stdout)
(root/'live-return-probe.json').write_text(json.dumps(result,indent=2))
print(json.dumps(result,separators=(',',':')))
'''

def main():
    p=argparse.ArgumentParser()
    p.add_argument('--args-file',type=Path,required=True)
    assert json.loads(p.parse_args().args_file.read_text(encoding='utf-8'))=={}
    logs=Path(os.environ['LOCALAPPDATA'])/'CrossZone/tilexr/ea219fe0-2177-4850-84f0-c0da68a4a5fa'
    key=Path(os.environ['USERPROFILE'])/'.ssh/id_ed25519'
    p=subprocess.run(['ssh','-T','-i',str(key),'-p','22','-o','BatchMode=yes','-o','IdentitiesOnly=yes',
                      '-o','StrictHostKeyChecking=yes','-o','ConnectTimeout=12','root@141.61.94.26',
                      'timeout --kill-after=5 45 python3 -'],input=HOST.encode(),capture_output=True,timeout=65)
    (logs/'live-return.stdout').write_bytes(p.stdout)
    (logs/'live-return.stderr').write_bytes(p.stderr)
    assert p.returncode==0,'live_status_probe_failed_see_green_logs'
    print(json.dumps(json.loads(p.stdout),separators=(',',':')))

if __name__=='__main__':main()
