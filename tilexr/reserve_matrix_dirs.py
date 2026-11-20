import sys
import json,subprocess
from pathlib import Path
name='tilexr-matrix-acc8c509-lr8-1'
info=json.loads(subprocess.check_output(['docker','inspect',name],timeout=15))[0]
assert info['Image']=='sha256:19e063d4eebab9785ad24b175ee1567dfb24c1180698f67fe0ab32a731a19cd5'
assert info['State']['Running']
root=Path('/root/crosszone-tilexr-acc8c509') / sys.argv[1]
root.mkdir(exist_ok=False)
for name in ['urma_lr8','urma_cw','urma_lr8_p16_s16_local7','urma_lr8_p16_s8_local7']:
 (root/name).mkdir()
