"""Check device occupancy, image identity and the two-node rendezvous network."""
import glob
import hashlib
import json
from pathlib import Path
import re
import socket
import subprocess
import time

def run(argv, timeout=20):
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
        return {'exit': p.returncode, 'output': p.stdout}
    except Exception as error:
        return {'error_class': type(error).__name__}

out = {'hostname': socket.gethostname()}
state = Path('/home/glm16-09b20820')
state.mkdir(exist_ok=True)
image = run(['docker', 'image', 'inspect', '--format', '{{.Id}}', '558855903609'])
out['image'] = image.get('output', '').strip()
out['image_present'] = out['image'] == 'sha256:5588559036095d339c1cfb48c16d0227b841355490d8c0cb4e6730759f9c6674'
out['devices'] = sorted(set(glob.glob('/dev/davinci*') + glob.glob('/dev/devmm*')
                            + glob.glob('/dev/hisi*') + glob.glob('/dev/uburma/*')
                            + glob.glob('/dev/ummu*') + glob.glob('/dev/tid*')))[:60]
info = run(['/usr/local/sbin/npu-smi', 'info'], timeout=65)
out['npu_query'] = {k: v for k, v in info.items() if k != 'output'}
text = info.get('output', '')
table = text.split('Process id', 1)[-1]
out['npu_process_rows'] = [x.strip() for x in table.splitlines()
                           if re.match(r'^\|\s*\d+\s*\|\s*\d+\s*\|', x)][:20]
out['npu_idle_confirmed'] = (info.get('exit') == 0 and 'Process id' in text
                            and table.count('No running processes found') == 8
                            and not out['npu_process_rows'])
out['health_rows'] = [x.strip() for x in text.splitlines() if 'Ascend950' in x][:8]
ip = '173.27.3.58' if ARGS['host'] == '141.61.94.58' else '173.27.3.26'
peer = '173.27.3.26' if ARGS['host'] == '141.61.94.58' else '173.27.3.58'
out['data_ip'] = ip
out['ping_peer'] = run(['ping', '-c', '1', '-W', '3', peer], timeout=8).get('exit')
out['ports'] = {}
for port in [8016, 29651, 29652, 29653]:
    try:
        with socket.socket() as s:
            s.bind((ip, port))
        out['ports'][port] = 'available'
    except OSError as e:
        out['ports'][port] = {'errno': e.errno}
config = Path('/mnt/share/GLM-5.2/config.json')
if config.is_file():
    out['model_config_sha256'] = hashlib.sha256(config.read_bytes()).hexdigest()
p = state / 'download-status.json'
if p.is_file():
    d = json.loads(p.read_text())
    out['download'] = {k: d[k] for k in ['state', 'files_done', 'files_total', 'downloaded_bytes',
                                       'verified_bytes', 'total_bytes', 'elapsed_seconds', 'failures']}
    out['download']['status_age_seconds'] = round(time.time() - d['updated_at'], 1)
print(json.dumps(out))
