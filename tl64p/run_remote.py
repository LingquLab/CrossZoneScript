from pathlib import Path
import hashlib
import json
import os
import re
import subprocess
import time


CASE = Path('/opt/sim/simulator/Testcases/dispatch_tilelang_64p_top6_68d32608_run01')
RECEIPT = CASE/'tilelang-run.json'
COMMAND = (
    'source /opt/sim/sst-env.sh && '
    'export SST_PATH=/opt/sim/local/sstcore '
    'SST_CONFIG_FILE_PATH=/opt/sim/simulator/config/sst-plugins.conf '
    'SST_CONFIG_FILE_PATH_SEPARATOR=: && ulimit -n 524288 && '
    'exec timeout -k 15s 3600s taskset -c "$1" sst -n 40 --print-timing-info ./audited_launcher.py'
)


def idle():
    for proc in Path('/proc').glob('[0-9]*/comm'):
        try:
            if proc.read_text().strip() in ('sst','sstsim.x','sstsim'):
                state=(proc.parent/'stat').read_text().rsplit(')',1)[1].split()[0]
                assert state in ('Z','X'),'SST_ALREADY_ACTIVE'
        except (FileNotFoundError,ProcessLookupError):
            continue


def cpu_list():
    allowed=os.sched_getaffinity(0)
    cpus={}
    for row in subprocess.check_output(['lscpu','-p=CPU,CORE,SOCKET,NODE'],text=True).splitlines():
        if row.startswith('#'):
            continue
        cpu,core,socket,node=map(int,row.split(','))
        if node==0 and cpu in allowed:
            cpus.setdefault((socket,core),cpu)
    assert len(cpus)>=40,'TOO_FEW_NODE0_CORES'
    return ','.join(map(str,sorted(cpus.values())[:40]))


def record():
    temp=RECEIPT.with_suffix('.tmp')
    temp.write_text(json.dumps(state,indent=2)+'\n')
    temp.replace(RECEIPT)


def run(name,args,limit):
    state['phase']=name
    record()
    start=time.monotonic()
    with (CASE/(name+'.log')).open('xb') as log:
        result=subprocess.run(args,cwd=CASE,stdout=log,stderr=subprocess.STDOUT,timeout=limit)
    state[name+'_wall_seconds']=round(time.monotonic()-start,3)
    state[name+'_exit_code']=result.returncode
    record()
    return result.returncode


state={'case':str(CASE),'status':'STARTING'}
owns_receipt=False
try:
    with RECEIPT.open('x') as stream:
        stream.write(json.dumps(state)+'\n')
    owns_receipt=True
    plan=json.loads((CASE/'tilelang-scenario.json').read_text())
    assert plan['status']=='PASS' and plan['rank_count']==64
    state.update(revision=plan['revision'],elf_sha=plan['elf_sha'],rank_count=64,threads=40,
                 workload_sha=plan['workload_sha'],cpu_affinity=cpu_list())
    for path in ('workload.yml','workload_config.yml','v310_config/workload_config.yml'):
        assert hashlib.sha256((CASE/path).read_bytes()).hexdigest()==plan['workload_sha']
    assert hashlib.sha256((CASE/'dispatch.o').read_bytes()).hexdigest()==plan['elf_sha']
    idle()
    state['status']='RUNNING'
    rc=run('run',['bash','-lc',COMMAND,'launch',state['cpu_affinity']],3640)
    text=(CASE/'run.log').read_text(errors='replace')
    times=re.findall(r'Simulation is complete, simulated time:\s*([\d.]+)\s*(ps|ns|us|ms|s)',text)
    errors=[word for word in ('FATAL','Traceback','Unknown low level','dumped core','Unsupported instruction') if word in text]
    state.update(completion_times=times,errors=errors)
    record()
    assert rc==0 and len(times)==1 and not errors,'SST_INCOMPLETE'
    rc=run('output-generator',['python3','-B','output_generator.py','v310_config'],300)
    assert rc==0,'OUTPUT_GENERATOR_FAILED'
    state.update(status='PASS',phase='OUTPUT_READY',bin_files=len(list((CASE/'bin').glob('*.bin'))))
except (OSError,AssertionError,ValueError,KeyError,subprocess.TimeoutExpired) as error:
    state.update(status='FAIL',reason=type(error).__name__+': '+str(error)[:150])
    run_log=CASE/'run.log'
    if run_log.is_file():
        lines=run_log.read_text(errors='replace').splitlines()
        state['error_lines']=[line[:205] for line in lines if re.search(r'FATAL|Error:|dumped core|unsupported',line,re.I)][-7:]
if owns_receipt:
    record()
print(json.dumps(state,separators=(',',':')))
