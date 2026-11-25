"""Bounded model-download connectivity checks with proxy credentials suppressed."""
import json
import socket
import ssl
import subprocess
import urllib.error
import urllib.parse
import urllib.request

out = {'proxies': {}, 'dns': {}, 'requests': []}
for scheme, value in urllib.request.getproxies().items():
    if scheme not in {'http', 'https', 'all'}:
        continue
    parsed = urllib.parse.urlsplit(value)
    out['proxies'][scheme] = {'host': parsed.hostname, 'port': parsed.port}
try:
    out['dns']['modelscope.cn'] = sorted({x[4][0] for x in socket.getaddrinfo('modelscope.cn', 443)})
except Exception as error:
    out['dns']['error_class'] = type(error).__name__
urls = [
    'https://modelscope.cn/models/Eco-Tech/GLM-5.2-w4a4c8-mxfp4/resolve/4f470ee4c91fc9ea2318501a051e6015995e295d/config.json',
    'https://modelscope.cn/api/v1/models/Eco-Tech/GLM-5.2-w4a4c8-mxfp4/repo/files?Revision=4f470ee4c91fc9ea2318501a051e6015995e295d&Recursive=true',
]
for url in urls:
    record = {'resource': 'config' if url.endswith('config.json') else 'manifest'}
    try:
        with urllib.request.urlopen(url, context=ssl._create_unverified_context(), timeout=12) as response:
            data = response.read(2 * 1024 * 1024)
            record.update(http_status=response.status, bytes=len(data))
            decoded = json.loads(data)
            if record['resource'] == 'manifest':
                record['repository_code'] = decoded.get('Code')
                record['file_count'] = len(decoded.get('Data', {}).get('Files', []))
    except Exception as error:
        record['error_class'] = type(error).__name__
        if isinstance(error, urllib.error.HTTPError):
            record['http_status'] = error.code
        reason = getattr(error, 'reason', None)
        if reason is not None:
            record['reason_class'] = type(reason).__name__
            record['errno'] = getattr(reason, 'errno', None)
            record['reason'] = str(reason)[:250] if not isinstance(reason, str) else 'connection_error'
    out['requests'].append(record)

archive_code = '''import tarfile,json
with tarfile.open('/mnt/share/glm52_lly.tar') as t:
 manifest=json.load(t.extractfile('manifest.json'))[0]
 config=json.load(t.extractfile(manifest['Config']))
 print(json.dumps({'image_id':manifest['Config'].removesuffix('.json'),'architecture':config.get('architecture'),'created':config.get('created'),'version_labels':{k:v for k,v in (config.get('config',{}).get('Labels') or {}).items() if 'version' in k.lower()}}))
'''
try:
    p = subprocess.run(['python3', '-c', archive_code], capture_output=True, text=True, timeout=20)
    out['archive_config'] = json.loads(p.stdout) if p.returncode == 0 else {'exit': p.returncode}
except Exception as error:
    out['archive_config'] = {'error_class': type(error).__name__}
print(json.dumps(out))
