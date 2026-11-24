# Copyright (c) 2026 TileXR Project.
"""Compare the same scalar Send candidate with the retained V1.0 perf models."""

from contextlib import redirect_stdout
import hashlib
import json
from pathlib import Path
import re
import sys

sys.path.insert(0, '/work')
import sst

ROOT = Path('/trial')
SOURCE = Path('/work/resume-7de1/mte-a1')
OBJECT = SOURCE / 'tilexr_moonep_combine_ht_kernel.o'
assert sst.checksum(OBJECT) == 'efe602e3973951d3059c3af8ff371166140a2dfb2687333d71b1deedb7288a36'
assert '-DTILEXR_COMBINE_HT_SIM_SEND=1' in json.loads((SOURCE / 'build.json').read_text())[0]
sst.WORK = ROOT
with (ROOT / 'environment.log').open('w') as log, redirect_stdout(log):
    snapshot = sst.select_environment()
sst.verify_environment(snapshot)
library = Path(snapshot['settings']['library_dir']) / 'libDavinciSimulator.so'
decoder = sst.checksum(library)
assert decoder == '6d4cd3b729177646ca1c43d57971e5266a946405b6f820f3d0d914114b7ccfce', 'unverified_v10_die_decoder'
# This exact V1.0 library's Die mapping was independently verified in the retained AllToAll report.
sst.V11_DIE_DECODER_SHA256 = decoder
specs, routes = sst.fixtures.prepare(8, 4, 256, 16, 4, 40, 64)
route_hash = hashlib.sha256(json.dumps(routes, separators=(',', ':')).encode()).hexdigest()
assert route_hash == 'f5eb786c6ca0a725607b16caef0ac8a8c0efc161576b185d9c74ffa2f4127881'
result = dict(ok=False, environment='V1.0.0_PERF', sim_send=True, doorbell_word_address=False,
    baseline_source_revision='16080aad58ced172c6e6682cf7eb87da4847bdad', route_sha256=route_hash,
    source_sha256={name: sst.checksum(SOURCE / name) for name in ('combine_ht.cpp', 'combine_ht_kernel.h', 'ht_mte_state.h')},
    runtime_files=snapshot['runtime_files'], phases=[])


def run(name, binary):
    folder = ROOT / name
    folder.mkdir(exist_ok=False)
    sst.WORK, sst.KERNEL = folder, binary
    send = sst.run_phase(folder / 'send-only', 1, specs, [spec.serialize() for spec in specs], snapshot,
                         dict(mode='send', threads=8, stage_timeout=210))
    errors = []
    with (folder / 'send-only/sim.log').open(errors='replace') as handle:
        for line in handle:
            match = re.search(r'FATAL|Unknown low level|[Aa]ssertion|Segmentation fault', line)
            if match and len(errors) < 2:
                errors.append(line[max(0,match.start()-30):match.start()+700].strip())
    row = dict(name=name, kernel_sha256=sst.checksum(binary), report=str(folder), simulator_errors=errors,
               send={k: v for k, v in send.items() if k not in ('model', 'verified_workspace_sha256')}, model=send['model']['su'])
    result['phases'].append(row)
    (ROOT / 'result.json').write_text(json.dumps(result, indent=2)+'\n')
    return send['ok']


if run('same-object', OBJECT):
    result['ok'] = True
else:
    folder = ROOT / 'rebuilt'
    folder.mkdir(exist_ok=False)
    binary = folder / 'tilexr_moonep_combine_ht_kernel.o'
    sst.WORK = Path('/work')
    try:
        with (folder / 'build.log').open('wb') as log:
            sst.build_kernel(snapshot['settings'], log, source=SOURCE / 'combine_ht.cpp', binary=binary)
        result['ok'] = run('rebuilt-run', binary)
    except Exception as error:
        result['build_error'] = dict(type=type(error).__name__, detail=str(error)[:350])
        result['compile_errors'] = [line[:350] for line in (folder / 'build.log').read_text(errors='replace').splitlines() if 'error:' in line][:3]
(ROOT / 'result.json').write_text(json.dumps(result, indent=2)+'\n')
print(json.dumps(result, ensure_ascii=True))
raise SystemExit(0 if result['ok'] else 1)
