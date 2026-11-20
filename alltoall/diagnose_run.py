"""Read only retained LR8 run evidence and process state."""
import json
from ssh_transport import run

inner = '''import json,re,os
from pathlib import Path
root=Path('/work/lr8-source/lr8-8p-516b')
result={'case_exists':root.is_dir(),'files':[],'reports':[],'logs':[],'processes':[]}
if root.is_dir():
    files=sorted(p for p in root.rglob('*') if p.is_file() and len(p.relative_to(root).parts)<=4)
    result['files']=[str(p.relative_to(root)) for p in files if p.suffix in ('.log','.json')][:70]
    for p in files:
        if p.name in ('run.json','status.json'):
            try:
                obj=json.loads(p.read_text())
                row={'path':str(p),'keys':list(obj)[:30]}
                for k in ('state','status','error','returncode','simulation_time','elapsed_seconds','command'):
                    if k in obj and k!='command': row[k]=obj[k]
                result['reports'].append(row)
            except (ValueError,OSError): pass
        if p.suffix=='.log' or p.name.endswith('.runner.log'):
            with p.open('rb') as f:
                f.seek(max(0,p.stat().st_size-24000)); text=f.read().decode('utf-8','replace')
            lines=[s for s in text.splitlines() if re.search(r'error|fatal|failed|exception|traceback|cannot|not found|No such|mismatch|abort|terminated|segmentation|assert|undefined',s,re.I)]
            lines=[s[:1000] for s in lines if not re.search(r'password|secret|authorization|private.key',s,re.I)][-8:]
            if lines: result['logs'].append({'path':str(p),'bytes':p.stat().st_size,'diagnostics':lines})
for p in Path('/proc').iterdir():
    if not p.name.isdigit() or int(p.name)==os.getpid(): continue
    try:
        comm=(p/'comm').read_text().strip()
        if comm in ('sst','sst.x','sstsim.x'): result['processes'].append({'pid':int(p.name),'comm':comm})
    except OSError: pass
print(json.dumps(result,ensure_ascii=True))
'''
outer='import json,subprocess\np=subprocess.run('+repr(['docker','exec','tilexr-cpu-b4f4660a-lr8','python3','-c',inner])+',capture_output=True,timeout=30)\nassert p.returncode==0\nprint(p.stdout.decode())\n'
print(json.dumps(run(outer,timeout=45),ensure_ascii=True))
