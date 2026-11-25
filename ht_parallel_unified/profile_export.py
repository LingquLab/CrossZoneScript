"""Render a completed BS32 profile on the SST host and deliver it on the green workstation."""
import hashlib
import io
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import zipfile

here = Path(__file__).resolve().parent
sys.path[:0] = [str(here.parent/'ht_t32_e2e'), str(here.parent/'alltoall')]
from green_auth import askpass_environment
from ssh_transport import HOST
from config import T32_PROFILE_CASE
from profile_markers import LOCAL_EVENTS

root = '/mnt/share/tilexr-sim-jobs/b4f4660a-6144-453c-99f7-339b40f56c54/inter-server-native'
case = f'{root}/{T32_PROFILE_CASE}'
remote_dir = f'{case}/profile-export'
remote_files = ('perfetto_trace.with-markers.json', 'profile-summary.json', 'per-aiv-us.csv', 'stage-report.md')
archive = (here/'profile-source.zip').read_bytes()
locations = {}
with zipfile.ZipFile(io.BytesIO(archive)) as source:
    for name in source.namelist():
        basename = Path(name).name
        locations[basename] = {}
        for number, line in enumerate(source.read(name).decode().splitlines(), 1):
            for marker in re.findall(r'\b(?:timing\(trace_,|(?:trace|writer)\.Mark\()\s*(\d+)', line):
                locations[basename].setdefault(marker, number)
            match = re.search(r'\bMark\(writer,\s*(\w+)\)', line)
            if match:
                for marker, symbol in LOCAL_EVENTS.items():
                    if match[1] == symbol:
                        locations[basename].setdefault(str(marker), number)
script = '\n'.join(((here/'profile_markers.py').read_text(encoding='utf-8'),
                    (here/'profile_render.py').read_text(encoding='utf-8'))) + f'''
try:
    root = Path({case!r})
    output = Path({remote_dir!r})
    summary = render(root, output, {locations!r})
    main_stages = [MARKERS[role][0][2] for role in ROLE_NAMES]
    measurements = {{name: summary['stages'][name] for name in main_stages if name in summary['stages']}}
    result = {{key: summary[key] for key in ('sst_sim_us', 'streams', 'expected_streams', 'raw_markers', 'slices', 'role_envelopes')}}
    result.update(status='rendered', stages=measurements, invalid_count=len(summary['invalid']),
                  invalid_sample=summary['invalid'][:5],
                  sha256={{name: sha256(output/name) for name in {remote_files!r}}})
    print(json.dumps(result, separators=(',', ':')))
except (ValueError, OSError, KeyError) as error:
    print(json.dumps(dict(status='profile_unavailable', error_type=type(error).__name__)))
'''
compile(script, '<bs32-profile-export>', 'exec')
options = ['-o', 'StrictHostKeyChecking=yes', '-o', 'ConnectTimeout=10', '-o', 'BatchMode=no',
           '-o', 'NumberOfPasswordPrompts=1', '-o', 'PreferredAuthentications=password', '-o', 'PubkeyAuthentication=no']
with askpass_environment() as environment:
    process = subprocess.run(['ssh', '-T', *options, HOST, 'python3', '-'],
                             input=script.encode(), env=environment, capture_output=True, timeout=180)
    if process.returncode:
        print(json.dumps(dict(status='remote_render_failed', ssh_exit=process.returncode)))
        sys.exit(0)
    result = json.loads(process.stdout)
    if result['status'] == 'rendered':
        output = Path('D:/project/tilexr-inter-server-sst')/T32_PROFILE_CASE
        output.mkdir(parents=True, exist_ok=True)
        for name, expected in result['sha256'].items():
            local = output/name
            temporary = local.with_suffix('.part')
            transfer = subprocess.run(['scp', '-q', *options, f'{HOST}:{remote_dir}/{name}', str(temporary)],
                                      env=environment, capture_output=True, timeout=120)
            if transfer.returncode or hashlib.sha256(temporary.read_bytes()).hexdigest() != expected:
                result['status'] = 'copy_failed'
                result['copy_failed_on'] = name
                break
            temporary.replace(local)
        if result['status'] == 'rendered':
            shutil.copyfile(here/'profile-source.zip', output/'profile-source.zip')
            result['artifact_dir'] = str(output)
print(json.dumps(result, separators=(',', ':')))
