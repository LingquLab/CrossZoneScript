"""Inspect green-local candidate checkout locations and remote simulator resources."""
import json
from pathlib import Path
import subprocess
from ssh_transport import run

result = {'remote': run(Path(__file__).with_name('inspect_sim_remote.py').read_text(encoding='utf-8'))}
result['local_tilexr_candidates'] = []
for root in (Path('D:/project'), Path('D:/work'), Path('D:/workspace')):
    if not root.is_dir():
        continue
    for path in sorted(root.iterdir()):
        if path.is_dir() and 'tilexr' in path.name.lower():
            row = {'path': str(path), 'git_present': (path / '.git').exists()}
            if row['git_present']:
                p = subprocess.run(['git', '-C', str(path), 'rev-parse', 'HEAD'], capture_output=True, timeout=10)
                if p.returncode == 0:
                    row['head'] = p.stdout.decode('ascii').strip()
            result['local_tilexr_candidates'].append(row)
print(json.dumps(result, ensure_ascii=True))
