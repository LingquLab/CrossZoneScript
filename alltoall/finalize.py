"""Save and return final simulation evidence without exporting raw dumps."""
import json
from pathlib import Path
from ssh_transport import run

result=run(Path(__file__).with_name('finalize_remote.py').read_text(encoding='utf-8'),timeout=90)
root=Path('D:/project/tilexr-sim-reports/b4f4660a-6144-453c-99f7-339b40f56c54')
result['local_reports']=[]
for label in ('unitest-128','unitest-129'):
    p=root/label/'result.json'
    obj=json.loads(p.read_text(encoding='utf-8'))
    result['local_reports'].append({'path':str(p),'status':obj['status'],'operator_result':obj['operator_result']})
(root/'final-summary.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
print(json.dumps(result,ensure_ascii=True))
