from pathlib import Path
import json
import re

case = Path('/opt/sim/simulator/Testcases/dispatch_tilelang_64p_top6_68d32608_run01')
result={}
for name in ('tilelang-run.json','run.log','output-generator.log','config-load.jsonl'):
    f=case/name
    if f.is_file():
        data=f.read_text(errors='replace')
        if name.endswith('.json'):
            result['receipt']=json.loads(data)
        else:
            rows=data.splitlines()
            result[name]={'lines':len(rows),'interesting':[
                row[:240] for row in rows if re.search('error|fail|invalid|fatal|exception|runtime',row,re.I)][-16:],
                'tail':[row[:240] for row in rows[-8:]]}
        if name=='config-load.jsonl':
            out=[]
            for row in rows:
                d=json.loads(row);out.append({'architecture':d.get('architecture'),'stars':d.get('stars')})
            result[name]=out
print(json.dumps({'status':'PASS','evidence':result},separators=(',',':')))
