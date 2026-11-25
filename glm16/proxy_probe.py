"""Compare Python and curl for one bounded request through the existing proxy."""
import hashlib
import http.client
import inspect
import json
from pathlib import Path
import re
import shutil
import ssl
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

proxy_url = DIRECT_PROXIES.get('https') or DIRECT_PROXIES['http']
proxy = urllib.parse.urlsplit(proxy_url)
url = 'https://modelscope.cn/models/Eco-Tech/GLM-5.2-w4a4c8-mxfp4/resolve/4f470ee4c91fc9ea2318501a051e6015995e295d/config.json'
out = {'proxy_host': proxy.hostname, 'proxy_scheme': proxy.scheme,
       'proxy_auth_in_url': bool(proxy.username), 'python': sys.version.split()[0],
       'python_connect_http10': 'HTTP/1.0' in inspect.getsource(http.client.HTTPConnection._tunnel)}
record = {}
started = time.monotonic()
try:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler(DIRECT_PROXIES),
        urllib.request.HTTPSHandler(context=ssl._create_unverified_context()))
    with opener.open(url, timeout=12) as response:
        record.update(http_status=response.status, bytes_read=len(response.read(8192)))
except Exception as error:
    record['error_class'] = type(error).__name__
    if isinstance(error, urllib.error.HTTPError):
        record['http_status'] = error.code
    reason = getattr(error, 'reason', None)
    if reason is not None:
        record['reason_class'] = type(reason).__name__
        record['errno'] = getattr(reason, 'errno', None)
        match = re.search(r'Tunnel connection failed:\s*(\d+)\s*([A-Za-z ]*)', str(reason))
        if match:
            record['proxy_tunnel_status'] = int(match.group(1))
            record['proxy_tunnel_message'] = match.group(2)[:80]
record['elapsed_seconds'] = round(time.monotonic() - started, 3)
out['urllib'] = record
out['curl_present'] = bool(shutil.which('curl'))
if out['curl_present']:
    with tempfile.TemporaryDirectory(prefix='glm16-proxy-check-') as directory:
        target = Path(directory) / 'config.json'
        p = subprocess.run(['curl', '--silent', '--show-error', '--location', '--http1.1',
            '--insecure', '--proxy-insecure', '--proxy', proxy_url, '--noproxy', '',
            '--connect-timeout', '5', '--max-time', '20', '--max-filesize', '8192',
            '--output', str(target), '--write-out',
            '{"http_status":%{http_code},"proxy_connect_status":%{http_connect},"bytes":%{size_download},"seconds":%{time_total}}',
            url], capture_output=True, text=True, timeout=28)
        try:
            observation = json.loads(p.stdout)
        except ValueError:
            observation = {}
        observation['exit'] = p.returncode
        if target.is_file():
            digest = hashlib.sha256(target.read_bytes()).hexdigest()
            observation['matches_config_sha256'] = digest == '185f93ee6d12548e16a847e279dc0c3c90b1524c970b0866b42fb545747d859a'
        out['curl'] = observation
print(json.dumps(out))
