"""Prepare CPU container and inspect local immutable Git revision availability."""
import json
from pathlib import Path
import subprocess
from ssh_transport import run

result = {'remote': run(Path(__file__).with_name('create_cpu_container_remote.py').read_text(encoding='utf-8'),timeout=100)}
repo=Path('D:/project/tileXR')
result['git_objects']={}
for rev in ('85afd1fa61669a5ef800a80bec4df93745f119c6','96581a23de5e7e87ce90c770fa130a596600944e','23a542b2e6bfcc34f85313d02677a4aafdfc3efb'):
    p=subprocess.run(['git','-C',str(repo),'cat-file','-e',rev+'^{commit}'],capture_output=True,timeout=15)
    result['git_objects'][rev]=p.returncode==0
report=Path('D:/project/tilexr-sim-reports/b4f4660a-6144-453c-99f7-339b40f56c54')
report.mkdir(parents=True,exist_ok=True)
(report/'container.json').write_text(json.dumps(result['remote'],indent=2),encoding='utf-8')
result['report_directory']=str(report)
print(json.dumps(result,ensure_ascii=True))
