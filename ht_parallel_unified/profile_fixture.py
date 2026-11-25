"""Append the SST profile address after the 464-byte unified kernel arguments."""


def configure_fused_profile(request, case, container_case, workload):
    size = 56 * 8192
    for name, chip in workload['workload_config'].items():
        rank = int(name.removeprefix('chip'))
        target = chip['sys_va_base_addr'] + 0x409000000
        relative = f'input_bin/rank{rank:02d}/profile.bin'
        (case / relative).write_bytes(bytes(size))
        parameters = chip['kernel_param']
        abi_bytes = max(int(parameter['param_offset']) for name, parameter in parameters.items()
                        if not name.startswith('load_')) + 8
        assert abi_bytes == 464
        for name, parameter in parameters.items():
            if name.startswith('load_'):
                parameter['param_offset'] = str(int(parameter['param_offset']) + 8)
        parameters['load_profile'] = dict(value=target, param_type='11', param_offset=str(abi_bytes),
                                          input_bin=f'{container_case}/{relative}')
        chip['output_param']['profile'] = dict(addr=target, size=size)
