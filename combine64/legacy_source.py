"""Check the reference host for the legacy image and its pinned platform fixtures."""
import json
import os
from pathlib import Path
import subprocess

PROBE = r"""
import json
from pathlib import Path
import subprocess
image = 'sha256:19e063d4eebab9785ad24b175ee1567dfb24c1180698f67fe0ab32a731a19cd5'
r = subprocess.run(['docker','image','inspect','--format','{{.Id}} {{.Architecture}} {{.Size}}',image],
                   capture_output=True,timeout=15)
fields = r.stdout.decode().split()
base = Path('/mnt/share/tilexr-sim-jobs/b4f4660a-6144-453c-99f7-339b40f56c54/inter-server-native')
template = base/'t4-sst-mte-bulk-forward-hotpath-1a2f3549'
manifest = template/'case-manifest.json'
print(json.dumps({'image_available': r.returncode == 0 and fields[:2] == [image,'arm64'],
                  'image_bytes': int(fields[-1]) if len(fields) == 3 else None,
                  'reference_platform_present': manifest.is_file(),
                  'platform_known_pass': json.loads((template/'run.json').read_text()).get('status') if manifest.is_file() else None,
                  'legacy_archives': [str(p) for p in Path('/mnt/share/simulator/images').glob('*v1.0*.tar*')]}))
"""

def main():
    key = Path(os.environ['USERPROFILE'])/'.ssh/id_ed25519'
    process = subprocess.run(['ssh','-T','-i',str(key),'-o','BatchMode=yes','-o','IdentitiesOnly=yes',
                              '-o','StrictHostKeyChecking=yes','-o','ConnectTimeout=10',
                              'root@141.61.94.26','timeout --kill-after=3 40 python3 -'],
                             input=PROBE.encode(),capture_output=True,timeout=55)
    data = json.loads(process.stdout) if process.returncode == 0 else {}
    print(json.dumps({'ssh_exit': process.returncode,**data}))


if __name__ == '__main__':
    main()
