"""Return bounded CPU/SST toolchain evidence through the existing green SSH route."""
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'alltoall'))
from ssh_transport import run

result=run(Path(__file__).with_name('probe_environment_remote.py').read_text(encoding='utf-8'),timeout=140)
report=Path('D:/project/inter-server-sst-reports/b4f4660a-6144-453c-99f7-339b40f56c54')
report.mkdir(parents=True,exist_ok=True)
(report/'environment.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
result['green_report']=str(report/'environment.json')
print(json.dumps(result,ensure_ascii=True))
