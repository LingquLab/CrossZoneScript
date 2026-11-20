from pathlib import Path
import hashlib
import json
import os
import re
import subprocess
import time


case_name = 'dispatch_tilelang_64p_top6_68d32608_debug01'
case = '/opt/sim/simulator/Testcases/' + case_name
logs = Path('/root/sim-workspace/results') / case_name
logs.mkdir()
docker_python = ['docker','exec','-i','simbox','python3','-B','-']


def find_simulator():
    listing = subprocess.check_output(['docker','top','simbox','-eo','pid,comm'], text=True, timeout=5)
    for row in listing.splitlines()[1:]:
        parts = row.split()
        if len(parts)!=2 or parts[1] not in ('sst','sstsim.x'):
            continue
        try:
            if os.readlink(f'/proc/{parts[0]}/cwd') == case:
                return int(parts[0])
        except (FileNotFoundError, ProcessLookupError):
            continue


result = {'status':'PASS','case':case}
with (logs/'stage.stderr.log').open('xb') as stderr:
    prepared = subprocess.run(docker_python,input=stage_source,stdout=subprocess.PIPE,stderr=stderr,timeout=120)
result['stage'] = json.loads(prepared.stdout) if prepared.returncode==0 else {'exit':prepared.returncode}
if result['stage'].get('status')=='PASS':
    with (logs/'controller.stdout.log').open('xb') as stdout, (logs/'controller.stderr.log').open('xb') as stderr:
        controller = subprocess.Popen(docker_python,stdin=subprocess.PIPE,stdout=stdout,stderr=stderr)
        with controller.stdin:
            controller.stdin.write(run_source)
        try:
            pid=None
            for _ in range(30):
                pid=find_simulator()
                if pid is not None or controller.poll() is not None:
                    break
                time.sleep(1)
            result['host_pid_attached']=pid
            if pid is not None:
                steps=['set debuginfod enabled off','set print frame-arguments none',
                       f'set sysroot /proc/{pid}/root',f'attach {pid}',
                       'handle SIGSEGV stop print pass','continue','info symbol $pc',
                       'info registers pc sp x0 x1 x2','info args','bt 20','detach']
                gdb=['gdb','-q','-nx','-batch']
                for step in steps:
                    gdb.extend(['-ex',step])
                with (logs/'gdb.log').open('xb') as log:
                    debug=subprocess.run(gdb,stdout=log,stderr=subprocess.STDOUT,timeout=260)
                raw=(logs/'gdb.log').read_bytes()
                result['gdb_sha256']=hashlib.sha256(raw).hexdigest()
                result['stack']=[line[:265] for line in raw.decode(errors='replace').splitlines()
                                 if re.search(r'^#\d+|received signal SIGSEGV|^(pc|sp|x0|x1|x2)\s|^this\s*=|ptrace:',line)][:23]
                result['gdb_exit']=debug.returncode
        finally:
            result['controller_exit']=controller.wait(timeout=300)
    if result['controller_exit']==0:
        result['simulation']=json.loads((logs/'controller.stdout.log').read_text())
(logs/'host-debug-receipt.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps(result,separators=(',',':')))
