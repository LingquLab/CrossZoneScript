"""Bounded bridge SSH transport; fixed task container, no credentials in source.
限定桥接 SSH 传输，仅操作本任务容器，不包含认证材料。
"""
import argparse
import json
import pathlib
import subprocess

CONTAINER = 'tilexr_alltoall_compare_20261113'
ROOT = '/opt/sim/alltoall-rebase-db76effb-20261116'
REV = 'db76effb34a25809afc346e4728c80a636214ec7'
OLD = '/opt/sim/alltoall-compare-df3d2601-20261113'
OPTS = ['-o','BatchMode=yes','-o','ConnectTimeout=10','-o','IdentitiesOnly=yes',
        '-o','ServerAliveInterval=15','-o','ServerAliveCountMax=2',
        '-i','C:/Users/l00929943/.ssh/id_ed25519_server']

def arguments():
    p = argparse.ArgumentParser()
    p.add_argument('--args-file', required=True)
    return json.loads(pathlib.Path(p.parse_args().args_file).read_text(encoding='utf-8'))

def ssh(script, timeout):
    p = subprocess.run(['ssh']+OPTS+['root@141.61.94.30','python3 -'],
                       input=script, capture_output=True, text=True, timeout=timeout)
    print(json.dumps(dict(ssh_exit=p.returncode, stdout=p.stdout,
                         error_tail=p.stderr.splitlines()[-4:])), flush=True)
    assert p.returncode == 0, 'remote execution failed; inspect saved state before replay'

def in_container(script, timeout):
    compile(script,'container.py','exec')
    host = """import json,subprocess
name=NAME
p=subprocess.run(['docker','inspect',name],capture_output=True,text=True,check=True)
c=json.loads(p.stdout)[0]
assert c['Id']=='21b762bee47b5b0302043652e6880c55763d5afedd247568185310d37a010198'
assert c['Image']=='sha256:19e063d4eebab9785ad24b175ee1567dfb24c1180698f67fe0ab32a731a19cd5'
assert c['State']['Running'], 'restore container separately; do not replay unknown work'
top=subprocess.check_output(['docker','top',name,'-eo','stat,comm'],text=True)
assert not any(r.split()[-1] in ('sst','sstsim.x') and not r.split()[0].startswith('Z') for r in top.splitlines()[1:]), 'simulator already active'
p=subprocess.run(['docker','exec','-i',name,'python3','-'],input=SCRIPT,capture_output=True,text=True,timeout=TIMEOUT)
print(json.dumps(dict(container_exit=p.returncode,stdout=p.stdout,error_tail=p.stderr.splitlines()[-4:])))
assert p.returncode==0
""".replace('NAME',repr(CONTAINER)).replace('SCRIPT',repr(script)).replace('TIMEOUT',str(timeout))
    ssh(host, timeout+30)
