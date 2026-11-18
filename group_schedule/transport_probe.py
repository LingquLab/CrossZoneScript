"""Read-only transport diagnosis; report negative observations as data."""
import json
from pathlib import Path
import re
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from alltoall.common import arguments, CONTAINER, OPTS

args = arguments()
revision, labels = args['revision'], args['labels']
assert re.fullmatch('[0-9a-f]{40}', revision)
assert 1 <= len(labels) <= 8 and len(labels) == len(set(labels))
assert all(re.fullmatch('[a-z0-9-]{1,60}', label) for label in labels)
root = '/opt/sim/alltoall-group-schedule-' + revision[:8] + '-20261118'
source = Path(__file__).with_name('recover_remote.py').read_text()
source = source.replace('ROOT_VALUE', repr(root)).replace('REV_VALUE', repr(revision))
source = source.replace('LABELS_VALUE', repr(labels))
compile(source, 'recover_remote.py', 'exec')
child = '''import json,traceback
try:
 exec(compile(SOURCE_VALUE,'recover_remote.py','exec'))
except Exception as error:
 print(json.dumps(dict(inspection_error_class=type(error).__name__,errno=getattr(error,'errno',None),
  frames=[dict(file=f.filename.rsplit('/',1)[-1],line=f.lineno,function=f.name) for f in traceback.extract_tb(error.__traceback__)[-3:]])))
'''.replace('SOURCE_VALUE', repr(source))
host = '''import json,subprocess
result=dict(stage='docker_inspect')
try:
 p=subprocess.run(['docker','inspect',NAME_VALUE],capture_output=True,text=True,timeout=15)
 result['docker_inspect_exit']=p.returncode
 if p.returncode==0:
  c=json.loads(p.stdout)[0]
  result.update(container_running=c['State']['Running'],container_status=c['State']['Status'],
   container_oom_killed=c['State'].get('OOMKilled'),container_exit_code=c['State'].get('ExitCode'),
   expected_container=c['Id']=='21b762bee47b5b0302043652e6880c55763d5afedd247568185310d37a010198',
   expected_image=c['Image']=='sha256:19e063d4eebab9785ad24b175ee1567dfb24c1180698f67fe0ab32a731a19cd5')
  if all(result[k] for k in ('container_running','expected_container','expected_image')):
   result['stage']='container_inspect'
   p=subprocess.run(['docker','exec','-i',NAME_VALUE,'python3','-'],input=CHILD_VALUE,capture_output=True,text=True,timeout=120)
   result.update(container_inspect_exit=p.returncode,container_stdout_bytes=len(p.stdout.encode()),
                 container_stderr_bytes=len(p.stderr.encode()))
   if p.returncode==0:result['case_state']=json.loads(p.stdout)
except Exception as error:
 result['inspection_error_class']=type(error).__name__
print(json.dumps(result))
'''.replace('NAME_VALUE', repr(CONTAINER)).replace('CHILD_VALUE', repr(child))
compile(host, 'transport_host.py', 'exec')
result = dict(read_only=True, simulation_launched=False, diagnostic='transport_and_saved_state',
              runtime_commit=revision)
try:
    p = subprocess.run(['ssh']+OPTS+['root@141.61.94.30', 'python3 -'],
                       input=host, capture_output=True, text=True, timeout=150)
    result.update(ssh_exit=p.returncode, ssh_stdout_bytes=len(p.stdout.encode()),
                  ssh_stderr_bytes=len(p.stderr.encode()))
    result['ssh_signals'] = {name: bool(re.search(pattern, p.stderr, re.I)) for name, pattern in (
        ('connection_timeout', r'connection timed out|operation timed out'),
        ('connection_refused', r'connection refused'), ('connection_reset', r'connection reset|broken pipe'),
        ('no_route', r'no route to host|network is unreachable'),
        ('authentication', r'permission denied|no such identity|identity file .* not accessible'),
        ('host_key', r'host key verification failed|remote host identification has changed'),
        ('ssh_exception', r'traceback|syntaxerror|nameerror|assertionerror'))}
    if p.returncode == 0:
        result['remote'] = json.loads(p.stdout)
except (OSError, ValueError, subprocess.SubprocessError) as error:
    result['inspection_error_class'] = type(error).__name__
print(json.dumps(result))
