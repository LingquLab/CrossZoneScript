# Copyright (c) 2026 TileXR Project.
"""Build an isolated MTE queue-state adaptation and run the retained 8p Send case."""

import hashlib
import json
from pathlib import Path
import re
import shutil
import sys

sys.path.insert(0, '/work')
import sst

BASELINE = '2e31f0e06fe776f0a0feb5232f6c9593e3ca453a33e7a5983176dc8b7b7883fd'
POLL_BEGIN = 'template <typename CompletionObserver>\n__aicore__ inline uint32_t UDMAPollCQUntilSqTail('
TRIAL_NAME = 'mte-a1'
AUTO_SYNC = True
ADDR_TRANSFORM = True


def once(text, before, after):
    assert text.count(before) == 1, 'unexpected_source_pattern: ' + before[:80]
    return text.replace(before, after)


def adapt(folder, root=Path('/work')):
    source = root / 'candidate/src/moonep/combine_ht/kernels'
    header = (source / 'combine_ht_kernel.h').read_text()
    api = (root / 'candidate/include/tilexr/comm/udma/device/api.h').read_text()
    helper = (root / 'tooling/tools/unitest/simulator/experiments/alltoall_udma/kernel/queue_state_mte.h').read_text()
    helper = helper.replace('__aicore__ inline', '__attribute__((always_inline)) __aicore__ inline')
    begin = api.index(POLL_BEGIN)
    end = api.index('\nstruct UDMAIgnoreCompletion', begin)
    poll = api[begin:end]
    poll = once(poll, 'UDMAPollCQUntilSqTail(', 'HTSimulatorPollCQUntilSqTail(')
    poll = once(poll, 'CompletionObserver &observer)',
                'CompletionObserver &observer, AscendC::LocalTensor<uint32_t> stateRead, AscendC::LocalTensor<uint32_t> stateWrite)')
    poll, loads = re.subn(r'ld_dev\((reinterpret_cast<__gm__ uint32_t\*>\([^\n]+?\)), 0\)',
                         r'TileXRSim::LoadQueueState(\1, stateRead)', poll)
    assert loads == 3
    poll = once(poll, 'UDMAPollCQUpdateInfo(cqTail, sqTail, cqCtxEntry, wqCtxEntry);',
                'TileXRSim::StoreQueueState(reinterpret_cast<__gm__ uint32_t*>(cqCtxEntry->tailAddr), cqTail, stateWrite);\n'
                '        TileXRSim::StoreQueueState(reinterpret_cast<__gm__ uint32_t*>(wqCtxEntry->tailAddr), sqTail, stateWrite);\n'
                '        TileXRSim::StoreQueueState(reinterpret_cast<__gm__ uint32_t*>(cqCtxEntry->dbAddr), cqTail & 0xFFFFFFU, stateWrite);')
    poll = poll.replace('__aicore__ inline', '__attribute__((always_inline)) __aicore__ inline')
    helper += '\nnamespace TileXR {\n' + poll + '\n}\n'
    (folder / 'ht_mte_state.h').write_text(helper)
    header = once(header, '#include <tilexr/comm/udma/device/api.h>',
                  '#include <tilexr/comm/udma/device/api.h>\n#include "ht_mte_state.h"')
    header = once(header, 'LocalTensor<uint8_t> wqes, header;',
                  'LocalTensor<uint8_t> wqes, header;\n    LocalTensor<uint32_t> stateRead, stateWrite;')
    header = once(header, 'return TileXR::UDMAPollCQUntilSqTail(info, peer, qp, head, *this);',
                  'return TileXR::HTSimulatorPollCQUntilSqTail(info, peer, qp, head, *this, stateRead, stateWrite);')
    header = once(header, 'ld_dev(reinterpret_cast<__gm__ uint32_t *>(sq->tailAddr), 0)',
                  'TileXRSim::LoadQueueState(reinterpret_cast<__gm__ uint32_t *>(sq->tailAddr), stateRead)')
    header = once(header, 'st_dev(head, reinterpret_cast<__gm__ uint32_t *>(sq->headAddr), 0);',
                  'TileXRSim::StoreQueueState(reinterpret_cast<__gm__ uint32_t *>(sq->headAddr), head, stateWrite);')
    header = once(header, 'st_dev(completions, reinterpret_cast<__gm__ uint32_t *>(sq->wqeCntAddr), 0);',
                  'TileXRSim::StoreQueueState(reinterpret_cast<__gm__ uint32_t *>(sq->wqeCntAddr), completions, stateWrite);')
    header = once(header, 'TPipe pipe;\n        TBuf<QuePosition::VECCALC> wqes, header, metadata, ranges;',
                  'TPipe pipe;\n        TBuf<QuePosition::VECCALC> wqes, header, metadata, ranges, stateReadBuffer, stateWriteBuffer;\n'
                  '        pipe.InitBuffer(stateReadBuffer, 64);\n        pipe.InitBuffer(stateWriteBuffer, 64);')
    header = once(header, 'ld_dev(reinterpret_cast<__gm__ uint32_t *>(sq->headAddr), 0)',
                  'TileXRSim::LoadQueueState(reinterpret_cast<__gm__ uint32_t *>(sq->headAddr), stateReadBuffer.Get<uint32_t>())')
    header = once(header, 'wqes.Get<uint8_t>(), header.Get<uint8_t>(), snapshot, peer, view.gateway, head,',
                  'wqes.Get<uint8_t>(), header.Get<uint8_t>(), stateReadBuffer.Get<uint32_t>(), stateWriteBuffer.Get<uint32_t>(), snapshot, peer, view.gateway, head,')
    header = once(header, 'ld_dev(reinterpret_cast<__gm__ uint32_t *>(sq->wqeCntAddr), 0)',
                  'TileXRSim::LoadQueueState(reinterpret_cast<__gm__ uint32_t *>(sq->wqeCntAddr), stateReadBuffer.Get<uint32_t>())')
    assert 'ld_dev(' not in header and 'st_dev(' not in header
    assert header.count('TileXR::UDMARingDoorbell(head, sq);') == 1
    assert header.count('TileXR::UDMAPublishWqe(wqes, sq, head, count);') == 1
    (folder / 'combine_ht_kernel.h').write_text(header)
    shutil.copyfile(source / 'combine_ht.cpp', folder / 'combine_ht.cpp')
    return {name: sst.checksum(folder / name) for name in ('combine_ht.cpp', 'combine_ht_kernel.h', 'ht_mte_state.h')}


def main():
    folder = Path('/work/resume-7de1') / TRIAL_NAME
    folder.mkdir(parents=True, exist_ok=False)
    result = dict(ok=False, diagnostic_only=True, baseline_kernel_sha256=BASELINE,
                  baseline_source_revision='16080aad58ced172c6e6682cf7eb87da4847bdad',
                  variant='MTE_GM_QUEUE_STATE_AND_CQ_MIRROR', auto_sync=AUTO_SYNC,
                  address_transform=ADDR_TRANSFORM, report=str(folder), send_verified=False)
    try:
        assert sst.checksum(sst.KERNEL) == BASELINE
        snapshot = json.loads(Path('/work/environment.snapshot.json').read_text())
        sst.verify_environment(snapshot)
        result['adapted_source_sha256'] = adapt(folder)
        binary = folder / 'tilexr_moonep_combine_ht_kernel.o'
        commands = []
        original_shell = sst.shell_command
        def build_shell(environment, stage, argv):
            actual = [arg for arg in argv if AUTO_SYNC or arg != '--cce-auto-sync']
            if not ADDR_TRANSFORM and '-cce-aicore-addr-transform' in actual:
                position = actual.index('-cce-aicore-addr-transform')
                assert actual[position - 1] == '-mllvm'
                del actual[position - 1:position + 1]
            commands.append(list(map(str, actual)))
            (folder / 'build.actual.json').write_text(json.dumps(commands, indent=2)+'\n')
            return original_shell(environment, stage, actual)
        sst.shell_command = build_shell
        try:
            with (folder / 'build.log').open('wb') as log:
                sst.build_kernel(snapshot['settings'], log, source=folder / 'combine_ht.cpp', binary=binary)
        finally:
            sst.shell_command = original_shell
        (folder / 'build.json').write_text(json.dumps(commands, indent=2)+'\n')
        result.update(build_completed=True, kernel_sha256=sst.checksum(binary))
        specs, routes = sst.fixtures.prepare(8, 4, 256, 16, 4, 40, 64)
        result['route_sha256'] = hashlib.sha256(json.dumps(routes, separators=(',', ':')).encode()).hexdigest()
        assert result['route_sha256'] == 'f5eb786c6ca0a725607b16caef0ac8a8c0efc161576b185d9c74ffa2f4127881'
        result['transfers'] = sst.transfer_statistics(specs)
        sst.KERNEL, sst.WORK = binary, folder
        args = dict(mode='send', threads=8, stage_timeout=210)
        result['send'] = sst.run_phase(folder / 'send-only', 1, specs, [s.serialize() for s in specs], snapshot, args)
        result['send_verified'] = result['send']['ok']
        result['ok'] = result['send_verified']
        log = folder / 'send-only/sim.log'
        result['simulator_errors'] = []
        if log.is_file():
            with log.open(errors='replace') as handle:
                for line in handle:
                    match = re.search(r'FATAL|Unknown low level|[Aa]ssertion|Segmentation fault', line)
                    if match:
                        result['simulator_errors'].append(line[max(0,match.start()-60):match.start()+700].strip())
                        if len(result['simulator_errors']) >= 3:
                            break
    except Exception as error:
        result.update(error_type=type(error).__name__, detail=str(error)[:600])
        log = folder / 'build.log'
        if log.exists() and not result.get('build_completed'):
            result['compile_errors'] = [line[:350] for line in log.read_text(errors='replace').splitlines() if 'error:' in line][:3]
    (folder / 'result.json').write_text(json.dumps(result, indent=2)+'\n')
    output = {k: v for k, v in result.items() if k != 'transfers'}
    if 'transfers' in result:
        output['transfers'] = {k: v for k, v in result['transfers'].items() if k != 'per_rank'}
    print(json.dumps(output, ensure_ascii=True))
    return result['ok']


if __name__ == '__main__':
    raise SystemExit(0 if main() else 1)
