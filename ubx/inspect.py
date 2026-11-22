"""中文：只读核对已有 HCCL 基线和构建产物，不运行 HCCL 或设备工作负载。
English: Read retained HCCL qualification and artifact metadata without launching workloads.
"""
import concurrent.futures
import json
from common import HOSTS, ROOT, remote_python, json_rows

remote = r'''
from pathlib import Path
import json,socket,re
allowed={'status','result','passed','correctness','rank','rank_size','ranks','devices','device','hosts','host','hostname','cann','cann_version','driver','driver_version','firmware','firmware_version','topology','mode','accelerator_mode','version','schema','baseline','backend','aiv_only','exit','exit_code','source','path','build','binary','sha256','command','argv','start_utc','end_utc'}
def select(obj):
    if isinstance(obj,dict):
        result={}
        for key,value in obj.items():
            if re.search(r'password|secret|token|credential|private',key,re.I):continue
            if isinstance(value,(dict,list)):
                child=select(value)
                if child:result[key]=child
            elif key.lower() in allowed and (not isinstance(value,str) or len(value)<1000):result[key]=value
        return result
    if isinstance(obj,list):
        return [select(v) if isinstance(v,(dict,list)) else v for v in obj[:40]]
    return None
bases=[Path('/root/ubx-expert-fa0eaf8-2dc7/hccl-ep32-once'),Path('/root/ubx-dispatch-sync-3c30e64d-06682e53/matrix-acab27ad4fe34a2b84388e836253b953/retained-hccl-baseline.json')]
out=[]
for base in bases:
    files=[base] if base.is_file() else sorted(p for p in base.rglob('*') if p.is_file())[:80] if base.is_dir() else []
    for p in files:
        row={'file':str(p),'bytes':p.stat().st_size}
        if p.suffix=='.json' and p.stat().st_size<100000:
            try:
                obj=json.loads(p.read_text())
                row['metadata']=select(obj)
            except (ValueError,UnicodeError):row['unreadable_json']=True
        elif p.suffix in ('.log','.stdout','.stderr','.out') and p.stat().st_size<4000000:
            text=p.read_text(errors='replace')
            row['has_error']=bool(re.search(r'\bFAIL(?:ED)?\b|\bERROR\b',text,re.I))
            row['success_lines']=[line.strip()[:250] for line in text.splitlines() if re.search(r'check.*(?:pass|success)|(?:pass|success).*check|Rank.*success',line,re.I)][:32]
        out.append(row)
print(json.dumps({'hostname':socket.gethostname(),'baseline_evidence':out},separators=(',',':')))
'''

def one(host):
    result = remote_python(host, remote, timeout=40, label='ep32-baseline')
    rows = json_rows(result.stdout)
    # Retain all evidence locally. Return only bounded metadata, never scripts or full logs.
    return dict(host=host, exit=result.returncode, rows=rows, stderr_bytes=len(result.stderr))

with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
    answer=list(pool.map(one,HOSTS))
(ROOT/'ep32-baseline-result.json').write_text(json.dumps(answer,indent=2),encoding='utf-8')
encoded=json.dumps(answer,separators=(',',':'))
assert len(encoded.encode())<24000, 'Baseline metadata too large; retained locally'
print(encoded)
assert all(row['exit']==0 for row in answer)
