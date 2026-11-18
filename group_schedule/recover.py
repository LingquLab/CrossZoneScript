"""Read saved case state and live processes without launching or replaying SST."""
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from alltoall.common import arguments, CONTAINER, ssh

args = arguments()
revision, labels = args['revision'], args['labels']
assert re.fullmatch('[0-9a-f]{40}', revision)
assert 1 <= len(labels) <= 8 and len(set(labels)) == len(labels)
assert all(re.fullmatch('[a-z0-9-]{1,60}', label) for label in labels)
root = '/opt/sim/alltoall-group-schedule-' + revision[:8] + '-20261118'
source = Path(__file__).with_name('recover_remote.py').read_text()
source = source.replace('ROOT_VALUE', repr(root)).replace('REV_VALUE', repr(revision))
source = source.replace('LABELS_VALUE', repr(labels))
compile(source, 'recover_remote.py', 'exec')
host = '''import json,subprocess
name=NAME_VALUE
c=json.loads(subprocess.check_output(['docker','inspect',name],text=True,timeout=15))[0]
assert c['Id']=='21b762bee47b5b0302043652e6880c55763d5afedd247568185310d37a010198'
assert c['Image']=='sha256:19e063d4eebab9785ad24b175ee1567dfb24c1180698f67fe0ab32a731a19cd5'
assert c['State']['Running']
p=subprocess.run(['docker','exec','-i',name,'python3','-'],input=SOURCE_VALUE,capture_output=True,text=True,timeout=120)
print(json.dumps(dict(container_exit=p.returncode,stdout=p.stdout,error_tail=p.stderr.splitlines()[-4:])))
assert p.returncode==0
'''.replace('NAME_VALUE', repr(CONTAINER)).replace('SOURCE_VALUE', repr(source))
ssh(host, 150)
