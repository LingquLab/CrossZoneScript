"""只读定位目标CANN计数器实现，限制返回代码片段总计不超过8行。"""
import json
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from alltoall.common import arguments, CONTAINER, ssh

revision = arguments()['revision']
assert re.fullmatch('[0-9a-f]{40}', revision)
root = '/opt/sim/alltoall-clock-group-' + revision[:8] + '-20261117'
query = '''import hashlib,json,os,pathlib,re,shutil,subprocess,time
toolkit=pathlib.Path('/opt/sim/Ascend/cann-9.1.0')
root=pathlib.Path(ROOT_VALUE)
depdir=root/'build/CMakeFiles/direct_two_group_clock_object.dir'
used=set();dependency_files=[]
for dependency in list(depdir.glob('*.o.d'))+list(depdir.glob('compiler_depend.*')):
 if not dependency.is_file() or dependency.stat().st_size>4000000:continue
 dependency_files.append(dependency.name)
 for token in dependency.read_text(errors='replace').replace(chr(92)+chr(10),' ').split():
  if 'kernel_operator_sys_var_' in token and token.endswith('.h'):
   p=pathlib.Path(token.rstrip(':'))
   if p.is_file():used.add(str(p.resolve()))
if used:
 paths=list(used);method='compiler_dependencies'
elif shutil.which('rg'):
 p=subprocess.run(['rg','--files','--follow',str(toolkit),'-g','*sys_var*'],
                  capture_output=True,text=True,timeout=40)
 paths=p.stdout.splitlines();method='rg'
else:
 paths=[];seen=set();deadline=time.monotonic()+40;method='python_walk'
 for directory,children,files in os.walk(toolkit,followlinks=True):
  if time.monotonic()>deadline:raise TimeoutError('bounded CANN header scan expired')
  real=os.path.realpath(directory)
  if real in seen:children[:]=[];continue
  seen.add(real)
  paths.extend(str(pathlib.Path(directory)/name) for name in files if 'sys_var' in name)
paths=sorted(set(str(pathlib.Path(p).resolve()) for p in paths),
             key=lambda p:('dav_m310' not in p,'dav_c310' not in p,'impl' not in p,p))
flags=(depdir/'flags.make').read_text(errors='replace') if (depdir/'flags.make').is_file() else ''
out=dict(read_only=True,simulation_launched=False,scan_method=method,candidates=len(paths),snippets=[],includes=[],
         dependency_files=dependency_files,used_sys_var_headers=sorted(used),
         compiled_arch=re.findall(r'--cce-aicore-arch=([^\\s]+)',flags),
         cpu_debug_defined='-DASCENDC_CPU_DEBUG=1' in flags)
lines_left=8
for name in paths[:120]:
 path=pathlib.Path(name)
 if path.stat().st_size>200000:continue
 text=path.read_text(errors='replace');lines=text.splitlines()
 hits=[i for i,line in enumerate(lines) if 'GetSystemCycleImpl(' in line]
 if not hits:continue
 out['includes'].append(dict(path=name,headers=re.findall(r'#include\\s+[<"]([^>"]+)',text)[:12]))
 if lines_left:
  i=hits[0];start=i;count=min(4,lines_left)
  for j in range(i+1,min(len(lines),i+35)):
   if 'return ' in lines[j] and re.search(r'get_.*\\(',lines[j]):start=max(i,j-1);break
  out['snippets'].append(dict(path=name,sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                              line=start+1,code=lines[start:start+count]))
  lines_left-=count
 if len(out['includes'])==8:break
out['exported_code_lines']=8-lines_left
print(json.dumps(out))
'''.replace('ROOT_VALUE', repr(root))
host = '''import json,subprocess
p=subprocess.run(['docker','inspect',NAME_VALUE],capture_output=True,text=True,timeout=10,check=True)
c=json.loads(p.stdout)[0]
assert c['Id']=='21b762bee47b5b0302043652e6880c55763d5afedd247568185310d37a010198'
assert c['State']['Running']
p=subprocess.run(['docker','exec','-i',NAME_VALUE,'python3','-'],input=QUERY_VALUE,
                 capture_output=True,text=True,timeout=60)
print(json.dumps(dict(query_exit=p.returncode,stdout=p.stdout,error_tail=p.stderr.splitlines()[-3:])))
assert p.returncode==0
'''.replace('NAME_VALUE', repr(CONTAINER)).replace('QUERY_VALUE', repr(query))
compile(query, 'query.py', 'exec')
compile(host, 'host.py', 'exec')
ssh(host, 80)
