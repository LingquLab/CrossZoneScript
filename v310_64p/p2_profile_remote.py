import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import time
import yaml

sim = Path('/opt/sim/simulator')
original = sim/'Testcases/dispatch_memory_2p'
case = original.with_name('dispatch_memory_2p_profile_bridge_20261119_run01')
profile_package = Path('/work/src/dispatch_memory_64p_handoff')
baseline_sha = '4e95390406c98b33e787aae974293658b50583d77b461bc636b1fa2012c013f4'
config_files = ('workload.yml', 'workload_config.yml', 'v310_config/workload_config.yml')
result = {'case': str(case), 'status': 'PREPARING', 'sst_started': False, 'wall_seconds': {}}
created = False


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save():
    if created:
        (case/'bridge-profile-receipt.json').write_text(json.dumps(result, indent=2)+'\n')


def stage(name, command, limit, folder=None):
    result['phase'] = name
    save()
    started = time.monotonic()
    log = case/(name+'.log')
    with log.open('xb') as stream:
        proc = subprocess.run(command, cwd=folder or case, stdout=stream, stderr=subprocess.STDOUT,
                              timeout=limit)
    result['wall_seconds'][name] = round(time.monotonic()-started, 3)
    result[name+'_exit'] = proc.returncode
    if proc.returncode:
        lines = log.read_text(errors='replace').splitlines()
        result[name+'_error'] = [line[-220:] for line in lines[-6:]]
        raise RuntimeError(name+' failed')
    return log


def active_runs():
    active = []
    for path in Path('/proc').glob('[0-9]*/comm'):
        try:
            if path.read_text().strip() not in ('sst', 'sstsim.x'):
                continue
            state = (path.parent/'stat').read_text().rsplit(')', 1)[1].split()[0]
            if state not in ('Z', 'X'):
                active.append(Path(os.readlink(path.parent/'cwd')).name)
        except FileNotFoundError:
            continue
    return active


def find_fields(node, key):
    if not isinstance(node, dict):
        return []
    fields = [node] if key in node else []
    return fields + [item for child in node.values() for item in find_fields(child, key)]


def add_profile_rows(chip):
    owner, = find_fields(chip, 'output_param')
    current = owner['output_param']
    entries = list(current.values()) if isinstance(current, dict) else current
    exemplar = entries[0]
    address_key, = set(exemplar) & {'value', 'addr', 'address', 'gm_addr', 'output_addr'}
    assert all(name not in current for name in ('profile_coarse', 'profile_functional'))
    return current, exemplar, address_key


def fmt(sample, value):
    if isinstance(sample, str):
        return hex(value) if sample.lower().startswith('0x') else str(value)
    return value


try:
    manifest = json.loads((original/'manifest.json').read_text())
    scenario = manifest['scenario']
    scalars = scenario['kernel']['scalars']
    assert scenario['world_size'] == 2 and scenario['block_dim_by_rank'] == [4, 4]
    assert [scalars[k] for k in ('bs','h','moeExpertNum','topK','dtype','magic')] == [4,256,4,2,11,1]
    assert manifest['kernel_sha256'] == baseline_sha and sha(Path(manifest['kernel_file'])) == baseline_sha
    baseline_workload = (original/'v310_config/workload_config.yml').read_bytes()
    assert hashlib.sha256(baseline_workload).hexdigest() == manifest['workload_sha256']
    wanted_headers = {
        'src/ep/kernels/tilexr_ep_dispatch_memory_kernel.h':
            'c7e4efee97d89339cee6ab3cedd69d5cb20a20ece2a1c7a923f8678dbd87e22a',
        'src/ep/common/ep_dispatch_memory_profile.h':
            '4a41719d1faffcb6801f54fe028cc857df0931d40f8963d8c96d6f572abc7ce0',
    }
    mapping_file = profile_package/'profile/dispatch-functional-v1.json'
    assert sha(mapping_file) == 'a0f42185421581bb7d4ae6f53faa2529a108f2f2423a70f86555bcfad5a0cd5f'
    for header, digest in wanted_headers.items():
        assert sha(profile_package/'kernel/source'/header) == digest, header
    for entry in manifest['files']:
        assert sha(original/entry['path']) == entry['sha256'], entry['path']
    case.mkdir()
    created = True
    for directory in ('input','expected','UBusSim_config','v310_config'):
        shutil.copytree(original/directory, case/directory)
    for name in ('test_UBusSim.py','output_generator.py'):
        shutil.copy2(original/name,case/name)

    candidate = case/'tilexr_ep_dispatch_memory_kernel.o'
    stage('build', ['bash','-lc','source /opt/sim/cann-env.sh && exec bash "$1" --out "$2" --format linked',
                    'build', str(profile_package/'kernel/build.sh'), str(candidate)], 150)
    result['profiled_elf_sha256'] = sha(candidate)
    assert result['profiled_elf_sha256'] != baseline_sha
    workload_bytes = baseline_workload.replace(str(original).encode(), str(case).encode())
    workload = yaml.safe_load(workload_bytes)
    chips = workload.get('workload_config', workload)
    outputs = {}
    for rank in manifest['rank_layouts']:
        number = rank['rank']
        assert rank['block_dim'] == 4
        chip = chips[f'chip{number}']
        kernel_owner, = find_fields(chip, 'kernel_file')
        kernel_owner['kernel_file'] = str(candidate)
        rows, sample, address_key = add_profile_rows(chip)
        rank['profile_outputs'] = {}
        for name, offset in (('profile_coarse',768*1024),('profile_functional',512*1024)):
            address = rank['peer_base'] + 2*1024*1024 + offset
            span = {'addr': address, 'readback_bytes': 2048}
            rank['profile_outputs'][name] = span
            row = {key: name for key in ('name','output_name','param_name') if key in sample}
            row.update({address_key: fmt(sample[address_key], address), 'size': fmt(sample['size'], 2048)})
            if isinstance(rows, dict):
                rows[name] = row
            else:
                assert row.keys() & {'name','output_name','param_name'}
                rows.append(row)
            outputs[number, name] = case/'bin'/f'david_{number}_{name}.bin'
    compiled = yaml.safe_dump(workload, sort_keys=False, allow_unicode=True).encode()
    for field in config_files:
        (case/field).write_bytes(compiled)
    result['baseline_elf_sha256'] = baseline_sha
    manifest.update(baseline_kernel_sha256=baseline_sha, kernel_file=str(candidate),
                    kernel_sha256=result['profiled_elf_sha256'], kernel_size_bytes=candidate.stat().st_size,
                    workload_sha256=hashlib.sha256(compiled).hexdigest(), profiling='dispatch-functional-v1')
    (case/'manifest.json').write_text(json.dumps(manifest, indent=2)+'\n')
    active = active_runs()
    if active:
        result['active_sst_cases'] = active
        result['status'] = 'STAGED_WAITING_FOR_SIMULATOR'
    else:
        result['sst_started'] = True
        command = ('source /opt/sim/sst-env.sh && '
                   'export SST_PATH=/opt/sim/local/sstcore '
                   'SST_CONFIG_FILE_PATH=/opt/sim/simulator/config/sst-plugins.conf '
                   'SST_CONFIG_FILE_PATH_SEPARATOR=: && ulimit -n 524288 && '
                   'exec timeout -k 10s 240s sst -n 2 ./test_UBusSim.py')
        log = stage('run', ['bash','-lc',command], 260)
        matches = re.findall(r'Simulation is complete,\s*simulated time:\s*([0-9.]+)\s*(ps|ns|us|ms|s)',
                             log.read_text(errors='replace'))
        assert len(matches) == 1, 'SST_COMPLETION'
        number, unit = matches[0]
        result['sst_simulated_time_us'] = float(number)*{'ps':1e-6,'ns':1e-3,'us':1,'ms':1e3,'s':1e6}[unit]
        stage('output-generator', ['python3','-B','output_generator.py','v310_config'], 120)
        checked = []
        for row in manifest['files']:
            if row['role'] != 'expected':
                continue
            name = Path(row['path']).name
            actual = case/'bin'/f'david_{row["rank"]}_{name}'
            assert sha(actual) == row['sha256'], ('GOLDEN_MISMATCH', row['path'])
            checked.append(row['path'])
        assert len(checked) == 8, 'BUSINESS_FILE_COUNT'
        result['golden_checked'] = len(checked)
        mapping = json.loads(mapping_file.read_text())
        decoded = decode_p2_records(case, mapping, outputs)
        result['ranks'] = {rank: {label: v for label, v in values.items() if label != 'substage'}
                           for rank, values in decoded.items()}
        result['raw_source'] = 'GetSystemCycle'
        result['clock_alignment'] = 'NOT_VERIFIED'
        result['status'] = 'PASS'
except (AssertionError, KeyError, OSError, RuntimeError, ValueError, subprocess.TimeoutExpired) as error:
    result.update(status='STOPPED', reason=str(error)[:240] if isinstance(error,(AssertionError,KeyError,RuntimeError))
                  else type(error).__name__)
save()
print(json.dumps(result, ensure_ascii=True, separators=(',', ':')))
