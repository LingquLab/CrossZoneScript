from pathlib import Path
import json
import os
import subprocess
import tempfile

os.umask(0o077)
base = Path('/opt')
final = base / OPTIONS['archive']
variants = OPTIONS['cases']
if final.exists():
    manifest = json.loads((final / 'MANIFEST.json').read_text())
    assert set(manifest) == {case[0] for case in variants}
    assert (final / 'README.md').stat().st_size
    print(json.dumps({'archive': str(final), 'existing': True, 'modes': {mode: {key: values[key] for key in ('dump_counts', 'profiles', 'sim_log_bytes', 'instruction_dump_chips')} for mode, values in manifest.items()}}))
    raise SystemExit(0)
stage = Path(tempfile.mkdtemp(prefix=final.name + '.partial-', dir=base))
manifest = {}
for mode, label, schedule in variants:
    root, = base.glob(OPTIONS['source_prefix'] + label + '-*')
    case = root / f'32p-131072i32-{schedule}-measurement'
    status = json.loads((case / 'execution.json').read_text())
    scenario = json.loads((case / 'scenario.json').read_text())
    assert status['state'] == 'simulation_outputs_verified'
    sim_log, = case.glob('runs/*/sim.log')
    assert sim_log.stat().st_size and (case / 'runner.log').stat().st_size
    target = stage / mode
    target.mkdir()
    required = ('UBusSim_config', 'v310_config', 'workload.yml', 'scenario.json', 'production_provenance.json', 'execution.json', 'runner.log', 'runs', 'stage-profiling')
    optional = ('environment.snapshot.json', 'library_load_expectations.json', 'installed_model.py', 'test_UBusSim.py', 'bin', 'OUTPUT', 'dumps')
    entries = [case / name for name in required] + [case / name for name in optional if (case / name).exists()]
    subprocess.run(['cp', '-a', '--reflink=auto', '--sparse=always', '--', *map(str, entries), str(target)], check=True, timeout=220)
    inputs = target / 'input_bin' / 'rank0'
    inputs.mkdir(parents=True)
    for name in ('sq_ctx.bin', 'profile_sq_ctx.bin', 'profile_info.bin', 'udma_info.bin', 'profile_registry.bin', 'kernel_args.bin'):
        original = case / 'input_bin' / 'rank0' / name
        subprocess.run(['cp', '-a', '--', str(original), str(inputs / name)], check=True, timeout=5)
        assert (inputs / name).stat().st_size == original.stat().st_size
    dump_counts = {name: sum(p.is_file() for p in (target / name).rglob('*')) for name in ('bin', 'OUTPUT', 'dumps') if (target / name).exists()}
    profiles = len(list((target / 'stage-profiling').glob('*.records.bin')))
    assert sum(dump_counts.values()) > 0 and profiles == 32
    assert (target / sim_log.relative_to(case)).stat().st_size == sim_log.stat().st_size
    for name in ('UBusSim_config/UBusSim_simulator_config.yml', 'v310_config/workload_config.yml', 'workload.yml'):
        assert (case / name).read_bytes() == (target / name).read_bytes()
    manifest[mode] = dict(source=str(case), entries=[item.name for item in entries], dump_counts=dump_counts, profiles=profiles, sim_log_bytes=sim_log.stat().st_size, instruction_dump_chips=scenario.get('instruction_dump_chips', []))

issue = OPTIONS['report']
(stage / 'README.md').write_text(issue, encoding='utf-8')
(stage / 'MANIFEST.json').write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
assert (stage / 'README.md').read_text(encoding='utf-8') == issue
stage.rename(final)
print(json.dumps({'archive': str(final), 'document': 'README.md', 'manifest': 'MANIFEST.json', 'modes': {mode: {key: values[key] for key in ('dump_counts', 'profiles', 'sim_log_bytes', 'instruction_dump_chips')} for mode, values in manifest.items()}}))
