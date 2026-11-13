import argparse
import json
from pathlib import Path
import urllib.request

parser = argparse.ArgumentParser()
parser.add_argument('--args-file', required=True)
args = json.loads(Path(parser.parse_args().args_file).read_text(encoding='utf-8'))
key = Path('D:/tmp/key.txt').read_text(encoding='utf-8-sig').strip()
assert key and '\n' not in key
url = 'http://models.ascend.huawei.com/v1'
if args['mode'] == 'list':
    request = urllib.request.Request(url+'/models', headers={'Authorization': 'Bearer '+key})
    with urllib.request.urlopen(request, timeout=30) as response:
        data = json.load(response)
    models = sorted({item['id'] for item in data['data']})
    print(json.dumps({'models': models}))
else:
    folder = Path.cwd()/'runs'/'review-token-test'/'1'/'execution'
    p = folder/'script.py'
    if not p.exists():
        candidates = list(folder.glob('*.py'))
        assert len(candidates) == 1
        p = candidates[0]
    source = p.read_text(encoding='utf-8')
    anchor = "config=json.loads((root/'config.local.json').read_text(encoding='utf-8-sig'))"
    assert anchor in source and 'for budget in (2048,8192):' in source
    settings = {'base_url':url, 'model':args['model'], 'api_key':key, 'timeout_seconds':240}
    source = source.replace(anchor, anchor+"\nconfig['exec_review']="+repr(settings))
    source = source.replace('for budget in (2048,8192):','for budget in (8192,):').replace('time.time()+200','time.time()+240')
    print(json.dumps({'model':args['model']}), flush=True)
    exec(compile(source, str(p), 'exec'))
