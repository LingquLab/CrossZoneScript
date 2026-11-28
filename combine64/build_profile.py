# Copyright (c) 2026 TileXR Project.
"""Compile the pinned SIM_SEND kernel with its native profiling writer enabled."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys

WORK = Path('/work')
SOURCE = WORK / 'source'
CANN = Path('/opt/sim/Ascend/cann-9.1.0')
TOOL_DIR = Path(__file__).resolve().parent
ENTRY = 'tilexr_moonep_combine_ht_kernel'


def main():
    assert len(sys.argv) <= 2
    mode = sys.argv[1] if len(sys.argv) == 2 else 'baseline'
    assert mode in ('baseline', 'entry-pipe', 'inline-post', 'fixed-events', 'timing-only')
    explicit_pipe = mode == 'entry-pipe'
    target = WORK / {'baseline': 'profile-build', 'entry-pipe': 'profile-pipe-build',
                     'inline-post': 'profile-inline-build', 'fixed-events': 'profile-fixed-events-build',
                     'timing-only': 'profile-timing-build'}[mode]
    target.mkdir()
    source = SOURCE / 'src/moonep/combine_ht/kernels/combine_ht.cpp'
    entry_body = '    RunCombineHT(args);'
    text = source.read_text()
    if explicit_pipe:
        header = source.with_name('combine_ht_kernel.h').read_text()
        start = header.index('__aicore__ inline uint32_t RunCombineHT(')
        end = header.index('\n__aicore__ inline void PrepareCombineHT', start)
        function = header[start:end]
        assert function.count('        TPipe pipe;\n') == 2
        function = function.replace('const TileXRCombineHT::KernelArgs &args)', 'const TileXRCombineHT::KernelArgs &args, TPipe &pipe)')
        function = function.replace('        TPipe pipe;\n', '')
        (target / 'combine_ht_kernel.h').write_text(header[:start] + function + header[end:])
        text = text.replace(entry_body, '    AscendC::TPipe pipe;\n    RunCombineHT(args, pipe);')
        entry_body = '    RunCombineHT(args, pipe);'
    if mode == 'inline-post':
        header = source.with_name('combine_ht_kernel.h').read_text()
        for signature in ('__aicore__ inline void RingIntraDoorbell(',
                          '__aicore__ inline SqCopyTiming PublishIntraWqes(',
                          '__aicore__ void PublishPrefillPiece('):
            assert header.count(signature) == 1, signature
            decl = signature if ' inline ' in signature else signature.replace('__aicore__ ', '__aicore__ inline ')
            header = header.replace(signature, '__attribute__((always_inline)) ' + decl)
        (target / 'combine_ht_kernel.h').write_text(header)
    if mode == 'fixed-events':
        # For this standalone kernel the reserved event IDs are per AIV. The
        # same named hard events still order each synchronous MTE transfer.
        for filename, declaration in (
                ('api.h', 'const auto eventId = GetTPipePtr()->FetchEventID(event);'),
                ('mte_queue_state.h', 'const auto id = GetTPipePtr()->FetchEventID(event);')):
            relative = Path('tilexr/comm/udma/device') / filename
            content = (SOURCE / 'include' / relative).read_text()
            assert content.count(declaration) == 1
            alternate = target / relative
            alternate.parent.mkdir(parents=True, exist_ok=True)
            alternate.write_text(content.replace(declaration, declaration.split(' = ')[0] + ' = EVENT_ID6;'))
    assert text.count(entry_body) == 1
    wrapper = target / (ENTRY + '.cpp')
    if mode == 'timing-only':
        signature = ENTRY + '(TileXRCombineHT::KernelArgs args)'
        assert text.count(signature) == 1
        text = text.replace(signature, ENTRY + '(TileXRCombineHT::KernelArgs args, GM_ADDR sstProfile)')
        timing = '''
    TileXR::Profiling::DirectWriter sstTiming;
    const uint32_t lane = AscendC::GetBlockIdx();
    sstTiming.Initialize(sstProfile, lane);
    sstTiming.RecordAt(1U, AscendC::GetSystemCycle());
    RunCombineHT(args);
    sstTiming.RecordAt(2U, AscendC::GetSystemCycle());
    sstTiming.Publish();
    HtSstProfile::Flush(sstProfile, lane);'''
        text = text.replace(entry_body, timing.strip('\n'))
    else:
        text = text.replace(entry_body,
                            entry_body + '\n    HtSstProfile::Flush(args.profiling, AscendC::GetBlockIdx());')
    wrapper.write_text('#include "profile_capture.h"\n' + text)
    options = [
        '-xcce', '-O2', '-DCATLASS_ARCH=3510', '--cce-auto-sync', '--cce-aicore-arch=dav-c310-vec',
        '-Wno-deprecated-declarations', '-mllvm', '-cce-aicore-stack-size=0x8000',
        '-mllvm', '-cce-aicore-function-stack-size=0x8000', '-mllvm', '-cce-aicore-addr-transform',
        '-mllvm', '-cce-aicore-dcci-insert-for-scalar=false', '-DTILEXR_ENABLE_PROFILING=1',
        '-DTILEXR_COMBINE_HT_SIM_SEND=1', '-DTILEXR_COMBINE_HT_SST_FP32=1',
        '--cce-aicore-only', '-std=gnu++17',
    ]
    tool_include = (
        'compiler/tikcpp', 'compiler/tikcpp/tikcfw', 'compiler/tikcpp/tikcfw/impl', 'compiler/tikcpp/tikcfw/interface',
        'aarch64-linux/ascendc/include', 'aarch64-linux/asc/include', 'aarch64-linux/include',
        'aarch64-linux/pkg_inc', 'aarch64-linux/pkg_inc/runtime',
    )
    includes = [target, TOOL_DIR, SOURCE/'include', SOURCE/'src/ep/combine_ht',
                SOURCE/'src/moonep/combine_ht/common', SOURCE/'src/moonep/combine_ht/kernels',
                *(CANN / name for name in tool_include)]

    flags = [*options, *('-I' + str(folder) for folder in includes)]
    rel = target / (ENTRY + '.rel.o')
    binary = target / (ENTRY + '.o')
    local_options = ['-mllvm', '-cce-aicore-record-overflow=true']
    compile_command = ['bisheng', *flags, *local_options, '-c', str(wrapper), '-o', str(rel)]
    link_command = ['ld.lld', '-m', 'aicorelinux', '-Ttext=0', str(rel), '--static',
                    '--allow-multiple-definition', '-o', str(binary)]
    log = target / (ENTRY + '.log')
    with log.open('wb') as output:
        code = subprocess.run(compile_command, stdout=output, stderr=subprocess.STDOUT, timeout=210).returncode
        if code == 0:
            code = subprocess.run(link_command, stdout=output, stderr=subprocess.STDOUT, timeout=90).returncode
    row = {'exit_code': code, 'wrapper_sha256': hashlib.sha256(wrapper.read_bytes()).hexdigest()}
    if code == 0:
        row.update(sha256=hashlib.sha256(binary.read_bytes()).hexdigest(), path=str(binary.relative_to(WORK)))
    else:
        row['errors'] = [line.strip()[:250] for line in log.read_text(errors='replace').splitlines()
                         if 'error:' in line.lower()][:6]
    summary = dict(all_compiled=code == 0, explicit_pipe=explicit_pipe, variant=mode, sim_send=True, profiling=True, targets={'fp32-' + ENTRY: row})
    (target / 'build-summary.json').write_text(json.dumps(summary, indent=2) + '\n')
    return code


if __name__ == '__main__':
    raise SystemExit(main())
