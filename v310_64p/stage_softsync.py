def stage_softsync(case, manifest, binary_sha256):
    import hashlib
    import json
    from pathlib import Path
    import shutil

    import yaml

    variant = Path('/work/results/v310-native-64p-softsync-build-20261119-02/tilexr_ep_dispatch_memory_kernel.o')
    verify = lambda src: hashlib.sha256(src).hexdigest()
    assert verify(variant.read_bytes()) == binary_sha256 and binary_sha256 != manifest['kernel_sha256']
    config_names = ('workload.yml', 'workload_config.yml', 'v310_config/workload_config.yml')
    workload = yaml.safe_load((case/config_names[0]).read_bytes())
    chips = workload['workload_config']
    workspace = bytes(64 * 32)

    def find_param(data):
        if not isinstance(data, dict):
            return []
        return [data['kernel_param']] if 'kernel_param' in data else [
            item for child in data.values() for item in find_param(child)
        ]

    def format_number(previous, number):
        if isinstance(previous, str):
            return hex(number) if previous.startswith('0x') else str(number)
        return number

    def parse_number(value):
        return int(value, 0) if isinstance(value, str) else value

    assert len(manifest['rank_layouts']) == 64
    for rank in manifest['rank_layouts']:
        number = rank['rank']
        assert rank['block_dim'] == 64
        assert rank['abi'][5] == ['dynamicScalesOutGM', 'pointer', 0]
        assert ['quantMode', 'scalar', 0] in rank['abi']
        end = max(row['addr'] + row['capacity_bytes'] for row in rank['buffers'].values())
        address = (end + 4095) // 4096 * 4096
        assert address + len(workspace) <= rank['peer_base']
        relative = f'input/rank{number}/sim_sync_workspace.bin'
        (case/relative).write_bytes(workspace)
        params, = find_param(chips[f'chip{number}'])
        if isinstance(params, dict):
            row = params['dynamicScalesOutGM']
        else:
            row, = [item for item in params if parse_number(item['param_offset']) == 40]
        assert parse_number(row['value']) == 0 and 'input_bin' not in row
        row['value'] = format_number(row['value'], address)
        row['input_bin'] = str(case/relative)
        rank['abi'][5][2] = address
        rank['buffers']['dynamicScalesOutGM'] = {'addr': address, 'capacity_bytes': len(workspace),
                                                 'input': relative}
        manifest['files'].append({'path': relative, 'rank': number, 'role': 'input',
                                  'gm_address': address, 'gm_address_hex': hex(address),
                                  'size_bytes': len(workspace), 'allocation_bytes': len(workspace),
                                  'sha256': verify(workspace)})

    updated = yaml.safe_dump(workload, sort_keys=False, allow_unicode=True).encode()
    for name in config_names:
        (case/name).write_bytes(updated)
    destination = case/'tilexr_ep_dispatch_memory_kernel.o'
    shutil.copy2(variant, destination)
    assert verify(destination.read_bytes()) == binary_sha256
    original_kernel_sha = manifest['kernel_sha256']
    manifest['kernel_sha256'] = binary_sha256
    manifest['kernel_size_bytes'] = destination.stat().st_size
    manifest['workload_sha256'] = verify(updated)
    manifest['simulation_sync_variant'] = {'type': 'GM_SOFTWARE_SYNC', 'baseline_kernel_sha256': original_kernel_sha,
                                          'bytes_per_rank': len(workspace), 'initialized': True}
    (case/'manifest.json').write_text(json.dumps(manifest, indent=2)+'\n')
    return manifest['simulation_sync_variant']
