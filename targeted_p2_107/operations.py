"""Task-scoped 107 guards and bounded command receipts. No implicit device launch."""
from pathlib import Path
import datetime
import hashlib
import json
import os
import re
import shlex
import signal
import socket
import subprocess
import time

ROOT = Path('/root/tilelang-107-targeted-p2-20261117-run01')
CANN = Path('/usr/local/Ascend/cann-9.1.0')
QUAL = Path('/root/tilelang-hccl-qualifications/693ec683dcc80807f1ccc0c0356a6e032008fd71e1a9a1538c435eaa1a153dc6/supplements/layer0-peer-private-view-single/receipt.json')
QUAL_SHA = 'e22807e4b530ded459533c840eb9e77722204776f5e99f2994d5a0640fe1b609'
SYSTEM = {'/etc/hccl_rootinfo.json':'56a0d1eb4c38422fbbdd946b34036ed6496fe36c1be95045d0519f35d69ad523', '/usr/local/Ascend/driver/topo/950/atlas_850_1.json':'65e257641adea221a576bab32710f759a59df80b50bc8ef613b38e10c6164db9'}
LIBS = {'libhccl.so':'25dc451dc22780a11c0dd58065a5f90fece8945986c756aa80831265ce261807','libhccl_v2.so':'1b3a4d8a1d09e03f036ca4371c4304f4deb0ee374c2527affaac7623e81bdb04','libhcomm.so':'e1ebc2e879cc923d1e2b310706a15ebb7250bde6644780875584ce5af3cb530c'}
PRIVATE_ROOT = Path('/root/tilelang-107-p4-peer-view-preflight-20261116-01')
PRIVATE = {'/etc/hccl_rootinfo.json':('rootinfo-layer0.json','61ccdd60085f52a721864c2077eed60f9e3e9a080f23b6a919dd901dfebd91ea'), '/usr/local/Ascend/driver/topo/950/atlas_850_1.json':('topology-layer0.json','d54ecef1dea84ea6c8106d502cc173afc43a9bab6b21d117e4a1b7d9b152e09d')}

def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def save(path, value):
    path=Path(path); temp=path.with_name(path.name+'.new')
    temp.write_text(json.dumps(value,ensure_ascii=True,indent=2)+'\n');temp.replace(path)

def require(ok, message):
    if not ok: raise RuntimeError(message)

def integrity(private=False):
    require(os.geteuid()==0 and sha('/etc/machine-id')=='78020a81dd869c8ca5a7ba9f4a18b25a9fead957b807f74e65793fced37b9af0','Host identity mismatch')
    for target,digest in SYSTEM.items(): require(sha(target)==(PRIVATE[target][1] if private else digest),'Topology changed: '+target)
    require(sha(QUAL)==QUAL_SHA,'Qualification changed')
    q=json.loads(QUAL.read_text())
    require(q['status']=='PASS' and q['identity']['devices']==[0,1,2,3] and not q['own_residual_children'],'Qualification failed')
    for name,digest in LIBS.items():require(sha(CANN/'lib64'/name)==digest,'Qualified library changed')
    for name,entry in json.loads((ROOT/'input-identity.json').read_text())['business_files'].items():
        p=ROOT/'fixture'/name
        require(p.stat().st_size==entry['bytes'] and sha(p)==entry['sha256'],'Business bytes changed: '+name)
    return {'qualification_receipt_sha256':QUAL_SHA,'libraries':LIBS,'business_files_verified':12}

def process_gate(directory):
    ancestors=set();pid=os.getpid()
    while pid:
        ancestors.add(pid)
        fields=Path('/proc/'+str(pid)+'/stat').read_text().rsplit(') ',1)[1].split();pid=int(fields[1])
    matches=[];errors=[]
    for p in Path('/proc').iterdir():
        if not p.name.isdigit() or int(p.name) in ancestors:continue
        try:
            cmd=(p/'cmdline').read_bytes(); f=(p/'stat').read_text().rsplit(') ',1)[1].split()
            # CPU-only readers may also match and deliberately stop the device window.
            if f[0]!='Z' and any(m in cmd for m in (b'tilelang-107-',b'xr-native107-',b'bs256-p4-107',b'tilexr_runtime_runner')):
                matches.append({'pid':int(p.name),'start_ticks':f[19],'cmdline_sha256':hashlib.sha256(cmd).hexdigest()})
        except (FileNotFoundError,ProcessLookupError):pass
        except OSError:errors.append(int(p.name))
    value={'matches':matches,'read_errors':errors};save(directory/'process-gate.json',value)
    require(not matches and not errors,'Known task active or process status unknown')

def query(directory, device, kind):
    p=subprocess.run(['npu-smi','info','-t',kind,'-i',str(device)],capture_output=True,timeout=20)
    prefix=directory/(str(device)+'-'+kind)
    prefix.with_suffix('.stdout.log').write_bytes(p.stdout);prefix.with_suffix('.stderr.log').write_bytes(p.stderr)
    ok=bool(re.search(rb'Health Status\s*:\s*OK\s*$',p.stdout,re.M)) if kind=='health' else b'No process in device.' in p.stdout
    return {'device':device,'kind':kind,'exit_code':p.returncode,'ok':p.returncode==0 and ok}

def gate(directory, all_devices=False, ports=()):
    directory.mkdir(exist_ok=False);process_gate(directory)
    rows=[query(directory,d,k) for d in range(8 if all_devices else 2) for k in ('health','proc-mem')]
    # Devices2-7 are observed only; their occupancy is not a reason to use or disrupt them.
    require(all(r['ok'] for r in rows if r['device'] in (0,1)),'Selected devices busy, unhealthy or unknown')
    ephemeral=list(map(int,Path('/proc/sys/net/ipv4/ip_local_port_range').read_text().split()))
    require(len(ports)==len(set(ports)) and all(1024<=p<=65535 and not ephemeral[0]<=p<=ephemeral[1] for p in ports),'Invalid port plan')
    for port in ports:
        for family,address in ((socket.AF_INET,'0.0.0.0'),(socket.AF_INET6,'::')):
            with socket.socket(family,socket.SOCK_STREAM) as s:
                if family==socket.AF_INET6:s.setsockopt(socket.IPPROTO_IPV6,socket.IPV6_V6ONLY,1)
                s.bind((address,port))
    value={'status':'PASS','utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'devices':rows,'ports_checked':len(ports),'snapshot_only':True}
    save(directory/'summary.json',value);return value

def run(directory, label, argv, timeout, cann=True):
    argv=list(map(str,argv));save(directory/(label+'.command.json'),argv)
    command=shlex.join(argv)
    if cann:command='source '+shlex.quote(str(CANN/'set_env.sh'))+' >/dev/null 2>&1 && export ASCEND_HOME_PATH='+shlex.quote(str(CANN))+' && exec '+command
    started=time.monotonic()
    with (directory/(label+'.log')).open('xb') as f:
        p=subprocess.Popen(['bash','-c',command],stdin=subprocess.DEVNULL,stdout=f,stderr=subprocess.STDOUT,start_new_session=True)
        save(directory/(label+'.started.json'),{'pid':p.pid})
        try:code=p.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            try:os.killpg(p.pid,signal.SIGTERM)
            except ProcessLookupError:pass
            try:p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                try:os.killpg(p.pid,signal.SIGKILL)
                except ProcessLookupError:pass
                p.wait(timeout=5)
            save(directory/(label+'.receipt.json'),{'timed_out':True,'exit_code':p.returncode});raise
    row={'exit_code':code,'seconds':round(time.monotonic()-started,3)};save(directory/(label+'.receipt.json'),row)
    require(code==0,'First failed command: '+label)
    return row
