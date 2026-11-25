"""Compare existing proxy access and a bounded request to the resolved model CDN."""
import json
import re
import socket
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request

out = {'cdn_host': urllib.parse.urlsplit(SIGNED_URL).hostname, 'requests': []}
for label, url, proxies, size in [
    ('server_proxy', 'https://modelscope.cn/models/Eco-Tech/GLM-5.2-w4a4c8-mxfp4/resolve/4f470ee4c91fc9ea2318501a051e6015995e295d/config.json', DIRECT_PROXIES, 8192),
    ('server_cdn_direct', SIGNED_URL, {}, 1024 * 1024),
]:
    started = time.monotonic()
    record = {'route': label}
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler(proxies),
                    urllib.request.HTTPSHandler(context=ssl._create_unverified_context()))
        request = urllib.request.Request(url, headers={'Range': f'bytes=0-{size-1}'})
        with opener.open(request, timeout=15) as response:
            sample = response.read(size)
            record.update(http_status=response.status, sample_bytes=len(sample),
                          content_range=response.headers.get('Content-Range'))
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
                record['proxy_tunnel_status'] = match.group(1)
                record['proxy_tunnel_message'] = match.group(2)[:80]
    record['elapsed_seconds'] = round(time.monotonic() - started, 3)
    out['requests'].append(record)
print(json.dumps(out))
