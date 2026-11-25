"""Dispatch one unified kernel SST run from the immutable blue fixture inputs."""
import base64
import hashlib
import json
from pathlib import Path
import subprocess
import sys

from config import CQ_ORDERED_GZIP_SHA256, CROSS_STRIPE_GZIP_SHA256, ENTRY, FP32_INIT_GZIP_SHA256, GZIP_SHA256, NAMES
from config import PROFILE_GZIP_SHA256

here=Path(__file__).resolve().parent
root=here.parent
support=root/'ht_t32_e2e'
sys.path[:0]=[str(support),str(root/'alltoall')]
from green_auth import askpass_environment
from ssh_transport import HOST


def bytes_and_hash(path):
    data=path.read_bytes()
    return base64.b64encode(data).decode(),hashlib.sha256(data).hexdigest()


options = json.loads(Path(sys.argv[2]).read_text()) if sys.argv[1:2] == ['--args-file'] else {}
tokens = int(options.get('tokens', 4))
assert tokens in (4, 8, 16, 32)
long_wall = bool(options.get('long_wall'))
profile = bool(options.get('profile'))
cross_stripe = options.get('cross_stripe', False)
assert cross_stripe in (False, True, 'ordered', 'fp32-init')
assert not (long_wall or cross_stripe or profile) or tokens == 32
sim_timeout = {4: 1200, 8: 2400, 16: 4800, 32: 6000 if profile else 4800 if long_wall or cross_stripe else 2400}[tokens]
case_override = 'T32_LONG_CASE' if long_wall else f'T{tokens}_CASE'
binary = here/'parallel.o.gz'
gzip_digest = GZIP_SHA256
kernel_override = ''
if profile:
    case_override = 'T32_PROFILE_CASE'
    binary = here/'parallel-profile.o.gz'
    gzip_digest = PROFILE_GZIP_SHA256
    kernel_override = 'KERNEL_SHA256 = PROFILE_KERNEL_SHA256'
elif cross_stripe == 'fp32-init':
    case_override = 'T32_FP32_INIT_CASE'
    binary = here/'parallel-fp32-init.o.gz'
    gzip_digest = FP32_INIT_GZIP_SHA256
    kernel_override = 'KERNEL_SHA256 = FP32_INIT_KERNEL_SHA256'
elif cross_stripe == 'ordered':
    case_override = 'T32_CQ_ORDERED_CASE'
    binary = here/'parallel-cq-ordered.o.gz'
    gzip_digest = CQ_ORDERED_GZIP_SHA256
    kernel_override = 'KERNEL_SHA256 = CQ_ORDERED_KERNEL_SHA256'
elif cross_stripe:
    case_override = 'T32_CROSS_STRIPE_CASE'
    binary = here/'parallel-cross-stripe.o.gz'
    gzip_digest = CROSS_STRIPE_GZIP_SHA256
    kernel_override = 'KERNEL_SHA256 = CROSS_STRIPE_KERNEL_SHA256'
obj,digest=bytes_and_hash(binary)
assert digest==gzip_digest
plans,plans_sha=bytes_and_hash(here/('plans.zip' if tokens == 4 else f'plans-t{tokens}.zip'))
exports=(support/'exports.py').read_text()
fields='("returned", "completed", "status", "output")'
exports=exports.replace(fields,repr(NAMES)).replace(
    '{"returned", "completed", "status", "output"}',repr(set(NAMES)))
assert 'if name not in '+repr(NAMES) in exports
request=dict(revision='4d9a6f2f62f0b343998ea9760637cf3993e0539c',object=obj,
             plans=plans,plans_sha256=plans_sha,exports=exports,checker=(here/'check.py').read_text(),
             loader=(root/'ht_shared_ready/loader.py').read_text())
if profile:
    request['profile'] = True
prepare=(support/'prepare.py').read_text().replace(
    '"tilexr_moonep_combine_ht_kernel"}',f'"{ENTRY}"}}').replace('chip["block_dim"] == 16','chip["block_dim"] == 56').replace('(4, 16, 32)', '(4, 8, 16, 32)')
base=(root/'inter_server/run_mte_t4_remote.py').read_text().rsplit('if __name__ == ',1)[0]
phase=(root/'ht_four_stage/local_phases.py').read_text().replace('"--timeout", "540"',f'"--timeout", "{sim_timeout}"').replace(
    'timeout=600)', f'timeout={sim_timeout + 75})')
source='\n'.join((
    base,prepare,phase,
    (here/'config.py').read_text(),
    (here/'profile_fixture.py').read_text() if profile else '',
    f'TOKENS = {tokens}\nCASE = {case_override}' if tokens != 4 else '',
    kernel_override,
    *((here/name).read_text() for name in ('route.py','fixture.py','trace.py','green.py')),
    f'''
try:
    print(json.dumps(main({request!r}),separators=(',',':')))
except Exception as error:
    import traceback
    last=traceback.extract_tb(error.__traceback__)[-1]
    print(json.dumps(dict(error_type=type(error).__name__,function=last.name,line=last.lineno)))
'''))
compile(source,'<unified-parallel>','exec')
ssh=['ssh','-T','-o','StrictHostKeyChecking=yes','-o','ConnectTimeout=10','-o',
     'BatchMode=no','-o','NumberOfPasswordPrompts=1','-o','PreferredAuthentications=password',
     '-o','PubkeyAuthentication=no',HOST,'python3','-']
with askpass_environment() as env:
    proc=subprocess.run(ssh,input=source.encode(),env=env,capture_output=True,timeout=sim_timeout+250)
if proc.returncode:
    print(json.dumps(dict(ssh_exit=proc.returncode,stderr_bytes=len(proc.stderr))))
else:
    print(json.dumps(json.loads(proc.stdout),separators=(',',':')))
