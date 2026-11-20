"""Read bounded Docker metadata; do not inspect logs, environment or file contents."""
import json
import platform
import shutil
import subprocess


def docker(*args):
    p = subprocess.run(['docker', *args], capture_output=True, timeout=10)
    return p.returncode, p.stdout.decode('utf-8', errors='replace')


result = {'architecture': platform.machine(), 'hostname': platform.node(),
          'docker_present': shutil.which('docker') is not None}
if result['docker_present']:
    status, output = docker('info', '--format', '{{json .Runtimes}}')
    result['docker_accessible'] = status == 0
    result['docker_runtimes'] = sorted(json.loads(output)) if status == 0 else []
    image = 'tilexr-sim-9.1:aarch64-ac2cee81-v1.0.0'
    status, output = docker('image', 'inspect', '--format', '{{.Id}}', image)
    result['required_image_id'] = output.strip() if status == 0 else None
    result['required_image_matches'] = output.strip() == 'sha256:19e063d4eebab9785ad24b175ee1567dfb24c1180698f67fe0ab32a731a19cd5'
    status, output = docker('ps', '-a', '--format', '{{json .}}')
    containers = [json.loads(line) for line in output.splitlines()] if status == 0 else []
    selected = [c for c in containers if any(s in (c.get('Image', '') + c.get('Names', '')).lower()
                for s in ['tilexr', 'sst', 'alltoall'])]
    result['matching_containers'] = []
    for container in selected[:5]:
        status, mounts = docker('inspect', '--format', '{{json .Mounts}}', container['ID'])
        result['matching_containers'].append({
            'name': container['Names'], 'image': container['Image'], 'state': container.get('State'),
            'mounts': [{'source': m['Source'], 'destination': m['Destination'], 'writable': m['RW']}
                       for m in json.loads(mounts)][:12] if status == 0 else [],
        })
print(json.dumps(result, ensure_ascii=True))
