"""Resolve the reference model snapshot and inspect the available image archive."""
import hashlib
import json
from pathlib import Path
import ssl
import subprocess
import urllib.request

REVISION = '4f470ee4c91fc9ea2318501a051e6015995e295d'
MODEL = 'Eco-Tech/GLM-5.2-w4a4c8-mxfp4'
ROOT = Path('/mnt/weight/GLM-5.2')
STATE = Path('/home/glm16-09b20820')
STATE.mkdir(exist_ok=True)

def fetch(url):
    request = urllib.request.Request(url, headers={'User-Agent': 'GLM16-validation/1'})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler(PROXIES),
              urllib.request.HTTPSHandler(context=ssl._create_unverified_context()))
    with opener.open(request, timeout=45) as response:
        return response.read(16 * 1024 * 1024)

result = {'model': MODEL, 'revision': REVISION,
          'target': str(ROOT), 'tls_download_verify': False}
try:
    url = f'https://modelscope.cn/api/v1/models/{MODEL}/repo/files?Revision={REVISION}&Recursive=true'
    raw = fetch(url)
    listing = json.loads(raw)
    data = listing.get('Data', {})
    files = data.get('Files', [])
    assert listing.get('Code', 200) == 200 and files, 'repository file list unavailable'
    manifest = []
    for item in files:
        if item.get('Type') in {'tree', 'directory'}:
            continue
        path = item.get('Path') or item.get('Name')
        if not path or path.startswith('.') or path.startswith('optional/'):
            continue
        assert not Path(path).is_absolute() and '..' not in Path(path).parts
        manifest.append({'path': path, 'size': item.get('Size'),
                         'sha256': item.get('Sha256') or item.get('SHA256')})
    assert manifest and all(isinstance(x['size'], int) for x in manifest)
    ROOT.mkdir(exist_ok=True)
    manifest_path = STATE / 'model-manifest.json'
    manifest_path.write_text(json.dumps({'model': MODEL, 'revision': REVISION,
                                       'files': manifest}, indent=2))
    (STATE / 'repository-listing.json').write_bytes(raw)
    config_bytes = fetch(f'https://modelscope.cn/models/{MODEL}/resolve/{REVISION}/config.json')
    config = json.loads(config_bytes)
    result.update(files=len(manifest), total_bytes=sum(x['size'] for x in manifest),
                  weight_files=sum(x['path'].endswith('.safetensors') for x in manifest),
                  sha256_files=sum(bool(x['sha256']) for x in manifest),
                  model_type=config.get('model_type'), architectures=config.get('architectures'),
                  config_sha256=hashlib.sha256(config_bytes).hexdigest(),
                  manifest=str(manifest_path))
except Exception as error:
    result['error_class'] = type(error).__name__
    if hasattr(error, 'code'):
        result['http_status'] = error.code
    reason = getattr(error, 'reason', None)
    if reason is not None:
        result['reason_class'] = type(reason).__name__
        result['errno'] = getattr(reason, 'errno', None)

archive_probe = r'''
import json, tarfile
from pathlib import Path
p=Path('/mnt/share/glm52_lly.tar')
out={'path':str(p),'exists':p.is_file()}
if p.is_file():
 out['bytes']=p.stat().st_size
 with tarfile.open(p) as t:
  out['first_members']=[]
  for _ in range(8):
   m=t.next()
   if m is None:break
   out['first_members'].append(m.name)
  try:
   m=t.getmember('manifest.json')
   assert m.size<1024*1024
   entries=json.load(t.extractfile(m))
   out['images']=[{'config':x.get('Config'),'tags':x.get('RepoTags')} for x in entries]
  except KeyError:out['docker_manifest']=False
print(json.dumps(out))
'''
try:
    p = subprocess.run(['python3', '-c', archive_probe], capture_output=True,
                       text=True, timeout=25)
    result['archive'] = json.loads(p.stdout) if p.returncode == 0 else {'exit': p.returncode}
except Exception as error:
    result['archive'] = {'error_class': type(error).__name__}
print(json.dumps(result))
