"""Generate a 64-rank AIV SST platform from the installed perf model."""
import hashlib
import json
from pathlib import Path
import shutil
import sys

import yaml

WORK = Path('/work')
SUPPORT = WORK / 'source/tools/unitest/simulator/experiments/inter_server_send/sst'
sys.path.insert(0, str(SUPPORT))
from runtime import BASES, BB_BYTES, DEPTH, DOORBELL_BASE, PHYSICAL_QPS, WORLD

SIM = Path('/opt/sim/simulator')
V310 = SIM / 'v310_deployment/config/perf_config'
UBUS = SIM / 'UBusSim_deployment'
DECODER = Path('/opt/sim/plugins/libDavinciSimulator.so')
DECODER_SHA = 'bf93fd6d0749fc3c6eaae365c7cba558988e1fa25c1b9a26e139be61b60a3da3'


def digest(path):
    value = hashlib.sha256()
    with Path(path).open('rb') as file:
        for part in iter(lambda: file.read(1024*1024), b''):
            value.update(part)
    return value.hexdigest()


def update_nested(node, values):
    if isinstance(node, dict):
        for key, value in node.items():
            if key in values:
                node[key] = values[key]
            else:
                update_nested(value, values)
    elif isinstance(node, list):
        for item in node:
            update_nested(item, values)


def build(output):
    output.mkdir()
    modules = {}
    for folder, names in {
        'v310_config': ('daw_config.yml', 'davidV100_simulator_config.yml', 'ddie_noc_config.yml', 'udie_noc_config.yml'),
        'UBusSim_config': ('UBusSim_simulator_config.yml', 'UBusSim_simulator_config_topo.yml'),
    }.items():
        (output/folder).mkdir()
        for name in names:
            modules[f'{folder}/{name}'] = (V310 if folder == 'v310_config' else UBUS/'config')/name

    main = 'v310_config/davidV100_simulator_config.yml'
    net = 'UBusSim_config/UBusSim_simulator_config.yml'
    configs = {name: yaml.safe_load(path.read_text()) for name, path in modules.items()}
    scalar = configs[main]['simulator_config']['su']
    assert scalar['component'] == 'V310.ScalarUnitL1' and int(scalar['params']['lsu_level']) == 1
    configs[main]['architecture_config'].update(chip_num=WORLD, chip_dump_enable_list=[])
    assert configs[net]['architecture_config']['host_num'] >= WORLD
    jetty = dict(qp_num=PHYSICAL_QPS, jetty_num=WORLD*PHYSICAL_QPS//2, jetty_ccu_num=0,
                 jetty_sq_addr_base=BASES['sq_buffers'], jetty_cq_addr_base=BASES['cq_buffers'],
                 jetty_ctx_addr_base=BASES['sq_ctx'], jetty_cq_ctx_addr_base=BASES['cq_ctx'],
                 jetty_db_addr_base=DOORBELL_BASE, jetty_sq_depth=DEPTH, jetty_cq_depth=DEPTH,
                 wqeSize=BB_BYTES, ctxSize=configs[net]['jetty_config']['ctxSize'])
    flags = dict(V310_flag=True, UBusSim_flag=True, urma_flag=True, simt_urma_flag=False,
                 clos_router_in_fm_flag=True)
    configs[net]['jetty_config'].update(jetty)
    configs[net]['simulator_info'].update(flags)
    configs[net].setdefault('stat_config', {})['stat_host_range'] = [0, WORLD-1]
    for name, document in configs.items():
        if name.endswith('/daw_config.yml'):
            shutil.copyfile(modules[name], output/name)
            continue
        layout = {'chip_num': WORLD, 'enable_dump': 0, 'enable_dump_detail': 0}
        if name.startswith('UBusSim_config/'):
            layout = {**jetty, **flags, 'stat_trace_flag': False, 'stat_host_range': [0, WORLD-1]}
        update_nested(document, dict(layout, log_level=0, verbose=0))
        (output/name).write_text(yaml.safe_dump(document, sort_keys=False))
    installed = UBUS/'test_UBusSim.py'
    shutil.copyfile(installed, output/'installed_model.py')
    shutil.copyfile(SUPPORT/'model_entry.py', output/'test_UBusSim.py')
    snapshot = {'settings': {'simulator_root': str(SIM)},
                'resources': {'installed_model.py': {'path': str(installed), 'sha256': digest(installed)}}}
    (output/'environment.snapshot.json').write_text(json.dumps(snapshot, indent=2))
    assert digest(DECODER) == DECODER_SHA
    files = {'sst_bin': '/opt/sim/local/sstcore/libexec/sstsim.x', 'env_script': '/opt/sim/sst-env.sh',
             'decoder_library': str(DECODER)}
    environment = {**files, **{key+'_sha256': digest(path) for key, path in files.items()}}
    dependencies = list(modules.values()) + list((SIM/'v310_deployment').rglob('*.py')) + list(UBUS.rglob('*.py'))
    dependencies += [Path('/opt/sim/plugins')/name for name in
                     ('libDavinciSimulatorV310.so', 'libUBusSimulator.so', 'libStarsSubsysV310.so')]
    environment['runtime_files'] = {str(path): digest(path) for path in dependencies if path.is_file()}
    (output/'sst-environment.json').write_text(json.dumps(environment, indent=2))
    result = {str(path.relative_to(output)): digest(path) for path in output.rglob('*') if path.is_file()}
    (output/'.manifest.json').write_text(json.dumps(result, indent=2))
    return result


if __name__ == '__main__':
    print(json.dumps(build(Path(sys.argv[1]))))
