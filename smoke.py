import argparse
import json
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument('--args-file', required=True)
options = parser.parse_args()
args = json.loads(Path(options.args_file).read_text(encoding='utf-8'))
assert args['value'] == '中文 空格 $HOME `literal`'
assert Path(__file__).with_name('fixture.txt').read_text(encoding='utf-8') == 'fixture-v1\n'
print(json.dumps({'mode': 'git-session', 'args': args, 'resource': 'PASS'}, ensure_ascii=False))
