import argparse
import json
from pathlib import Path
import time

git_probe_started = time.time()
parser = argparse.ArgumentParser()
parser.add_argument('--args-file', required=True)
args = json.loads(Path(parser.parse_args().args_file).read_text(encoding='utf-8'))
folder = Path.cwd() / 'runs' / 'review-token-test' / '1' / 'execution'
p = folder / 'script.py'
if not p.exists():
    candidates = list(folder.glob('*.py'))
    assert len(candidates) == 1
    p = candidates[0]
source = p.read_text(encoding='utf-8')
assert 'for budget in (2048,8192):' in source
assert args['budget'] in (2048, 8192)
source = source.replace('for budget in (2048,8192):', f"for budget in ({args['budget']},):").replace('time.time()+200', 'time.time()+550')
print(json.dumps({'mode': 'git', 'script_started_epoch': git_probe_started, 'budget': args['budget']}), flush=True)
exec(compile(source, str(p), 'exec'))
print(json.dumps({'script_elapsed_seconds': round(time.time()-git_probe_started, 2)}), flush=True)
