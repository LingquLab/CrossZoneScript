"""Read only the known SST platform and its deployed workload parser."""
import json
import subprocess
import time

remote = r"""
import hashlib
import io
import json
import posixpath
import re
import subprocess
import tarfile
from pathlib import Path

root = Path('/mnt/share/tilexr-sim-jobs/b4f4660a-6144-453c-99f7-339b40f56c54')
platform = root/'inter-server-native/t4-sst-mte-drain-count-cache-38d743f7'
reference = root/'lr8-source/lr8-64p-512b'
template_name = 'tilexr-cpu-b4f4660a-lr8'
result = {'ssh': 'connected', 'known_platform_exists': platform.is_dir()}
for tag, name in (('platform', template_name), ('send_original', 'tilexr-cpu-b4f4660a-ht-t32-send'),
                  ('send_distributed', 'tilexr-cpu-b4f4660a-ht-t32-send-distributed')):
    try:
        p = subprocess.run(['docker','inspect','--format',
                            '{{.State.Status}} {{.State.OOMKilled}} {{.State.ExitCode}} {{.HostConfig.Runtime}}',
                            name], capture_output=True, timeout=8)
        fields = p.stdout.decode('ascii', 'replace').split()
        result[tag] = fields if p.returncode == 0 and len(fields)==4 else {'inspect_exit': p.returncode}
    except (OSError, subprocess.TimeoutExpired) as error:
        result[tag] = {'inspect_error': type(error).__name__}
image = 'sha256:19e063d4eebab9785ad24b175ee1567dfb24c1180698f67fe0ab32a731a19cd5'
try:
    p = subprocess.run(['docker','image','inspect','--format','{{.Id}}',image],
                       capture_output=True,timeout=8)
    result['pinned_image_available'] = p.returncode == 0 and p.stdout.strip().decode('ascii') == image
except (OSError, subprocess.TimeoutExpired) as error:
    result['image_inspect_error'] = type(error).__name__

snapshot = next((p for p in (platform/'environment.snapshot.json', reference/'environment.snapshot.json') if p.is_file()), None)
if snapshot is None:
    result['snapshot_found'] = False
else:
    settings = json.loads(snapshot.read_text())
    simulator = settings['settings']['simulator_root']
    runtime = settings.get('runtime_files', {})
    names = {
        'multi_chip_test.py', 'stars_subsys.py', 'ascend_chip_simulator.py', 'read_config.py', 'component_param.py',
    }
    candidates = {}
    for item in runtime.values() if isinstance(runtime, dict) else runtime:
        if isinstance(item, dict) and item.get('path'):
            name = posixpath.basename(item['path'])
            if name in names:
                candidates[name] = item['path']
    for name, rel in {
        'multi_chip_test.py': 'v310_deployment/simulator/multi_chip_test.py',
        'stars_subsys.py': 'v310_deployment/simulator/stars_subsys.py',
        'ascend_chip_simulator.py': 'v310_deployment/simulator/ascend_chip_simulator.py',
        'read_config.py': 'v310_deployment/common/read_config.py',
        'component_param.py': 'v310_deployment/common/component_param.py',
    }.items():
        candidates.setdefault(name,posixpath.join(simulator, rel))
    result['parser'] = {}
    for name, path in candidates.items():
        raw = None
        if Path(path).is_file():
            raw = Path(path).read_bytes()
        else:
            try:
                cp = subprocess.run(['docker','cp',f'{template_name}:{path}','-'],
                                    capture_output=True,timeout=8)
                if cp.returncode == 0 and len(cp.stdout) <= 262144:
                    with tarfile.open(fileobj=io.BytesIO(cp.stdout)) as stream:
                        members = [entry for entry in stream.getmembers() if entry.isfile()]
                        if len(members) == 1 and members[0].size < 131072:
                            raw = stream.extractfile(members[0]).read()
            except (OSError, subprocess.TimeoutExpired, tarfile.TarError):
                continue
        if raw is None:
            continue
        lines = raw.decode(errors='replace').splitlines()
        anchors = [i for i,line in enumerate(lines) if re.search(
            r'kernel_file|vec_func|workload_config|kernel_list|kernel_sequence|multi_kernel', line, flags=re.I)]
        result['parser'][name] = {
            'sha256': hashlib.sha256(raw).hexdigest(),
            'matched_lines': [[i+1, lines[i].strip()[:160]] for i in anchors[:12]],
        }
print(json.dumps(result, separators=(',', ':')))
"""

command = ["ssh", "-T", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes",
           "-o", "ConnectTimeout=6", "root@141.61.94.58", "python3", "-"]
results = []
for attempt in range(3):
    try:
        proc = subprocess.run(command, input=remote.encode(), capture_output=True, timeout=85)
    except subprocess.TimeoutExpired:
        results.append({"transport": "timeout"})
    else:
        if proc.returncode == 0:
            print(json.dumps(json.loads(proc.stdout), separators=(",", ":")))
            break
        message = proc.stderr.decode(errors="replace").lower()
        category = ("connect_refused" if "connection refused" in message else
                    "connect_timeout" if "connection timed out" in message else "unclassified")
        results.append({"ssh_exit": proc.returncode, "category": category, "stderr_bytes": len(proc.stderr)})
    if attempt < 2:
        time.sleep(4)
else:
    print(json.dumps({"ssh_attempts": results}, separators=(",", ":")))
