# Copyright (c) 2026 TileXR Project.
"""Build SST copies of the five entry points with start and finish markers."""
import hashlib
import json
from pathlib import Path
import re
import subprocess

WORK = Path('/work')
SOURCE = WORK / 'source'
CANN = Path('/opt/sim/Ascend/cann-9.1.0')
TOOL_DIR = Path(__file__).resolve().parent
ENTRIES = {
    'tilexr_moonep_combine_ht_kernel': 'src/moonep/combine_ht/kernels/combine_ht.cpp',
    'tilexr_ep_ht_local_return_kernel': 'src/ep/combine_ht/local_return_kernel.cpp',
    'tilexr_ep_ht_inter_server_send_kernel': 'src/ep/combine_ht/inter_server_send_kernel.cpp',
    'tilexr_ep_ht_final_reduce_kernel': 'src/ep/combine_ht/final_reduce_kernel.cpp',
    'tilexr_ht_pipeline_sst': 'tools/unitest/simulator/experiments/inter_server_send/sst/combine_ht_pipeline_kernel.cpp',
}


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def wrap(text, entry):
    pattern = r'(^extern "C" __global__ __aicore__ void ' + entry + r'\([^{}]+?)(\)\n\{\n)(.*?)(^\})'
    match = re.search(pattern, text, flags=re.M | re.S)
    assert match and 'return;' not in match[3], entry
    begin = '''
    TileXR::Profiling::DirectWriter sstTiming;
    const uint32_t sstLane = AscendC::GetBlockIdx();
    sstTiming.Initialize(sstProfile, sstLane);
    sstTiming.RecordAt(1U, AscendC::GetSystemCycle());
'''
    end = '''    sstTiming.RecordAt(2U, AscendC::GetSystemCycle());
    sstTiming.Publish();
    HtSstProfile::Flush(sstProfile, sstLane);
'''
    marked = match[1] + ', GM_ADDR sstProfile' + match[2] + begin + match[3] + end + match[4]
    return '#include "profile_capture.h"\n' + text[:match.start()] + marked + text[match.end():]


def run(argv, log):
    with log.open('wb') as stream:
        code = subprocess.run(argv, stdout=stream, stderr=subprocess.STDOUT, timeout=210).returncode
    return code


def main():
    target = WORK / 'profile-build'
    target.mkdir()
    summary = dict(all_compiled=False, sim_send=True, profiling=True, targets={})
    options = [
        '-xcce', '-O2', '-DCATLASS_ARCH=3510', '--cce-auto-sync', '--cce-aicore-arch=dav-c310-vec',
        '-Wno-deprecated-declarations', '-mllvm', '-cce-aicore-stack-size=0x8000',
        '-mllvm', '-cce-aicore-function-stack-size=0x8000', '-mllvm', '-cce-aicore-addr-transform',
        '-mllvm', '-cce-aicore-dcci-insert-for-scalar=false', '-DTILEXR_ENABLE_PROFILING=1',
        '-DTILEXR_COMBINE_HT_SIM_SEND=1', '-DTILEXR_COMBINE_HT_SST_FP32=1',
        '-DTILEXR_EP_HT_SST_SPLIT_WRITES=1', '-DTILEXR_EP_HT_FINAL_REDUCE_SST_FP32=1',
        '-DTILEXR_EP_HT_PIPELINE_FP32=1', '--cce-aicore-only', '-std=gnu++17',
    ]
    tool_include = (
        'compiler/tikcpp', 'compiler/tikcpp/tikcfw', 'compiler/tikcpp/tikcfw/impl', 'compiler/tikcpp/tikcfw/interface',
        'aarch64-linux/ascendc/include', 'aarch64-linux/asc/include', 'aarch64-linux/include',
        'aarch64-linux/pkg_inc', 'aarch64-linux/pkg_inc/runtime',
    )
    includes = [TOOL_DIR, SOURCE/'include', SOURCE/'src/ep/combine_ht',
                SOURCE/'src/moonep/combine_ht/common', SOURCE/'src/moonep/combine_ht/kernels',
                *(CANN / name for name in tool_include)]
    flags = [*options, *('-I' + str(folder) for folder in includes)]
    for entry, relative in ENTRIES.items():
        key = entry + '_kernel' if entry == 'tilexr_ht_pipeline_sst' else entry
        original = SOURCE / relative
        wrapper = target / (entry + '.cpp')
        wrapper.write_text(wrap(original.read_text(), entry))
        obj = target / (entry + '.rel.o')
        binary = target / (entry + '.o')
        compile_log = target / (entry + '.compile.log')
        link_log = target / (entry + '.link.log')
        local_options = ['-mllvm', '-cce-aicore-record-overflow=true'] if entry.startswith('tilexr_moonep_') else []
        code = run(['bisheng', *flags, *local_options, '-c', str(wrapper), '-o', str(obj)], compile_log)
        if code == 0:
            code = run(['ld.lld', '-m', 'aicorelinux', '-Ttext=0', str(obj), '--static',
                        '--allow-multiple-definition', '-o', str(binary)], link_log)
        row = dict(exit_code=code, entry=entry, original_sha256=digest(original), wrapper_sha256=digest(wrapper))
        summary['targets']['fp32-' + key] = row
        if code:
            log = link_log if link_log.is_file() else compile_log
            row['errors'] = [line.strip()[:220] for line in log.read_text(errors='replace').splitlines()
                             if re.search(r'error:|Error:', line)][:5]
            break
        row.update(sha256=digest(binary), path=str(binary.relative_to(WORK)), bytes=binary.stat().st_size)
    summary['all_compiled'] = len(summary['targets']) == len(ENTRIES) and all(
        row['exit_code'] == 0 for row in summary['targets'].values())
    (target/'build-summary.json').write_text(json.dumps(summary, indent=2) + '\n')
    return 0 if summary['all_compiled'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
