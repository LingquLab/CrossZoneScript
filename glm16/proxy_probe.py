"""Check one small model resource through the existing proxy, without blob resolution."""
import json
import re
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request

proxy = urllib.parse.urlsplit(DIRECT_PROXIES.get('https') or DIRECT_PROXIES['http'])
out = {'proxy_host': proxy.hostname, 'proxy_auth_in_url': bool(proxy.username)}
started = time.monotonic()
try:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler(DIRECT_PROXIES),
        urllib.request.HTTPSHandler(context=ssl._create_unverified_context()))
    url = 'https://modelscope.cn/models/Eco-Tech/GLM-5.2-w4a4c8-mxfp4/resolve/4f470ee4c91fc9ea2318501a051e6015995e295d/config.json'
    with opener.open(url, timeout=12) as response:
        out.update(http_status=response.status, bytes_read=len(response.read(8192)))
except Exception as error:
    out['error_class'] = type(error).__name__
    if isinstance(error, urllib.error.HTTPError):
        out['http_status'] = error.code
    reason = getattr(error, 'reason', None)
    if reason is not None:
        out['reason_class'] = type(reason).__name__
        out['errno'] = getattr(reason, 'errno', None)
        match = re.search(r'Tunnel connection failed:\s*(\d+)\s*([A-Za-z ]*)', str(reason))
        if match:
            out['proxy_tunnel_status'] = int(match.group(1))
            out['proxy_tunnel_message'] = match.group(2)[:80]
out['elapsed_seconds'] = round(time.monotonic() - started, 3)
print(json.dumps(out))
