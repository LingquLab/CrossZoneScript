"""只读查询指定时钟版本的容器、构建摘要和进程，不重跑编译或仿真。"""
import json
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from alltoall.common import arguments, CONTAINER, ssh

revision = arguments()['revision']
assert re.fullmatch('[0-9a-f]{40}', revision)
root = '/opt/sim/alltoall-clock-group-' + revision[:8] + '-20261117'
query = '''import json,pathlib
root=pathlib.Path(ROOT_VALUE)
out=dict(read_only=True,simulation_launched=False,root_exists=root.exists())
p=root/'build-summary.json'
if p.exists():
 try:out['build_summary']=json.loads(p.read_text())
 except (ValueError,OSError) as e:out['summary_read_error']=type(e).__name__
out['files']={}
for name in ('configure.log','compile.log','build/tilexr_alltoall_direct_two_group.o',
             'build/tilexr_alltoall_direct_two_group_clock.o'):
 p=root/name
 if p.exists():out['files'][name]=dict(bytes=p.stat().st_size)
out['compiler_errors']=[]
p=root/'compile.log'
if p.exists():
 with p.open(errors='replace') as stream:
  for line in stream:
   if 'error:' in line:
    out['compiler_errors'].append(line.strip()[:260])
    if len(out['compiler_errors'])==4:break
print(json.dumps(out))
'''.replace('ROOT_VALUE', repr(root))
host = '''import json,subprocess
p=subprocess.run(['docker','inspect',NAME_VALUE],capture_output=True,text=True,timeout=10)
assert p.returncode==0,'container inspect failed'
c=json.loads(p.stdout)[0]
assert c['Id']=='21b762bee47b5b0302043652e6880c55763d5afedd247568185310d37a010198'
out=dict(read_only=True,simulation_launched=False,container_running=c['State']['Running'])
if c['State']['Running']:
 p=subprocess.run(['docker','exec','-i',NAME_VALUE,'python3','-'],input=QUERY_VALUE,
                  capture_output=True,text=True,timeout=25)
 out.update(query_exit=p.returncode,query_stdout=p.stdout)
 assert p.returncode==0,'read-only query failed'
 p=subprocess.run(['docker','top',NAME_VALUE,'-eo','pid,stat,comm'],capture_output=True,text=True,timeout=10)
 out['active_processes']=[r.strip() for r in p.stdout.splitlines()[1:]
                         if r.split()[-1] in ('cmake','ccec','clang','clang++','sst','sstsim.x')][:12]
print(json.dumps(out))
'''.replace('NAME_VALUE', repr(CONTAINER)).replace('QUERY_VALUE', repr(query))
compile(query, 'query.py', 'exec')
compile(host, 'host.py', 'exec')
ssh(host, 60)
