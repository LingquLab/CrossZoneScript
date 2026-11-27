# Copyright (c) 2026 TileXR Project.
"""Build SST copies of the five entry points with start and finish markers."""
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys

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
    body = match[3]
    if entry == 'tilexr_ht_pipeline_sst':
        # Mode 2 reuses producer blocks within this SST launch. The outer timer
        # covers production and final reduce on each physical AIV.
        body += '''
    if (args.finalPipeline == 2) {
        const auto &send = args.send;
        TileXREpHt::FinalReduceDevice::Run(reinterpret_cast<GM_ADDR>(send.workspace),
            reinterpret_cast<GM_ADDR>(args.serverMask), reinterpret_cast<GM_ADDR>(send.workspace + args.localReadyOffset),
            reinterpret_cast<GM_ADDR>(args.output), reinterpret_cast<GM_ADDR>(args.finalStatus),
            send.config, sstLane, args.finalBlocks);
    }
'''
    marked = match[1] + ', GM_ADDR sstProfile' + match[2] + begin + body + end + match[4]
    return '#include "profile_capture.h"\n' + text[:match.start()] + marked + text[match.end():]


def run(argv, log):
    with log.open('wb') as stream:
        code = subprocess.run(argv, stdout=stream, stderr=subprocess.STDOUT, timeout=210).returncode
    return code


def stage_ready_probe(target):
    path = SOURCE / 'src/ep/combine_ht/local_return_worker.h'
    text = path.read_text()
    original = '        return WaitGatewayReady(workspace_, a.config, chunk, ready_.Get<int64_t>(), readyThrough_);'
    assert text.count(original) == 1
    text = text.replace(original, """        const auto trace = a.status + uint64_t(localPeer_) * 32;
        Store<uint32_t>(trace + 8, chunk + 1);
        const auto work = ready_.Get<int64_t>();
        const auto mask = ReadMagicMask(workspace_ + ReadyOffset(a.config, 0, chunk),
            a.config.reducerCount, ReadyStride(a.config), a.config.magic, work);
        Store<uint32_t>(trace + 12, uint32_t(work.GetValue(0)));
        Store<uint32_t>(trace + 16, uint32_t(mask));
        return WaitGatewayReady(workspace_, a.config, chunk, work, readyThrough_);""")
    markers = (
        ('        SelectLocalPeer(localPeer);', '        Store<uint32_t>(a.status + uint64_t(localPeer_) * 32 + 4, 1);'),
        ('    __aicore__ Range LoadRange(uint32_t peer, uint32_t, uint32_t chunk)\n    {',
         '        Store<uint32_t>(a.status + uint64_t(localPeer_) * 32 + 20, 1);'),
        ('    __aicore__ uint32_t NotifyLocal(uint32_t, uint32_t window)\n    {',
         '        Store<uint32_t>(a.status + uint64_t(localPeer_) * 32 + 20, 2);'),
        ('    __aicore__ uint32_t Drain()\n    {',
         '        Store<uint32_t>(a.status + uint64_t(localPeer_) * 32 + 20, 3);'),
    )
    for anchor, marker in markers:
        assert text.count(anchor) == 1
        text = text.replace(anchor, anchor + '\n' + marker)
    (target / path.name).write_text(text)


def stage_joint_probe(target):
    pipeline = SOURCE / 'src/moonep/combine_ht/kernels/combine_ht_kernel.h'
    text = pipeline.read_text()
    for phase, anchor in enumerate((
        '                result = gather.Step(view, chunk, writer, local.profiling != nullptr);',
        '                result = TileXREpHt::ReturnLocalChunk(send.config, peer, chunk, back);',
        '            const uint32_t sent = gather.Drain(view);',
        '            const uint32_t drained = back.Drain();',
    ), start=1):
        assert text.count(anchor) == 1
        indent = anchor[:len(anchor) - len(anchor.lstrip())]
        chunk = 'chunk' if phase <= 2 else '0xffU'
        marker = (f'{indent}back.Store<uint32_t>(args.returnStatus + uint64_t(begin) * 32 + 4, '
                  f'({phase}U << 24) | ({chunk} << 16) | (peer << 8));')
        text = text.replace(anchor, marker + '\n' + anchor)
    (target / pipeline.name).write_text(text)

    worker = SOURCE / 'src/ep/combine_ht/local_return_worker.h'
    text = worker.read_text()
    wait = '        return WaitGatewayReady(workspace_'
    assert text.count(wait) == 1
    text = text.replace(wait, '        Store<uint32_t>(a.status + uint64_t(localPeer_) * 32 + 12, 0U);\n' + wait)
    for offset, value, anchor in (
        (8, 'chunk + 1U', wait),
        (12, '1U', '        const auto range = Read<int32_t>('),
        (12, '2U', '        const uint32_t flushed = Flush();'),
        (12, '3U', '        auto *remote = TileXR::UDMARegisteredRemoteAddr(registry_, commPeer_, target);'),
    ):
        assert anchor in text
        marker = f'        Store<uint32_t>(a.status + uint64_t(localPeer_) * 32 + {offset}, {value});'
        text = text.replace(anchor, marker + '\n' + anchor, 1)
    (target / worker.name).write_text(text)


def stage_inter_probe(target):
    worker = SOURCE / 'src/ep/combine_ht/inter_server_send_worker.h'
    text = worker.read_text()
    marker = '    __aicore__ uint32_t WaitReady(uint32_t chunk)\n'
    assert text.count(marker) == 1
    text = text.replace(marker, """    __aicore__ void BindTrace(GM_ADDR status)
    {
        if (c_.rank == 0 && (owner_ == 8 || owner_ == 11)) {
            trace_ = reinterpret_cast<uint64_t>(status) + uint64_t(owner_) * 32 + 4;
        }
    }
    __aicore__ void Trace(uint32_t step, uint32_t peer, uint32_t qp)
    {
        if (trace_) {
            queue_.Store(trace_, (step << 24) | (chunk_ << 16) | (peer << 8) | qp);
        }
    }
""" + marker)
    anchors = (
        ('        const uint32_t status = WaitGatewayReady(workspace_', '        Trace(1, 255, 255);'),
        ('        const uint32_t result = TileXR::UDMAPutNbiOnQpWithFlag<uint64_t>',
         '        Trace(3, peer, qp);'),
        ('                if (TileXR::UDMAQuietStatusOnQp(args_, peer, Qp(peer, lane), queue_)) {',
         '                Trace(9, peer, Qp(peer, lane));'),
        ('        if (!queue_.TryReadCompletion(cqe, owner, completion)) {',
         '        Trace(5, peer, qp);'),
        ('        ready = tail == head;', '        Trace(10, peer, qp);'),
        ('        const uint32_t cqTail = queue_.Load(cq->tailAddr);', '        Trace(11, peer, qp);'),
        ('                bool ready;\n                const uint32_t result = ProgressSq(peer, qp, head, ready);',
         '                Trace(12, peer, qp);'),
        ('        if (completion.error) {',
         '        Trace(6, peer, qp);'),
        ('        if (TileXR::UDMAPollCQUntilSqTail(info, peer, qp, target, observer, queue_)) {',
         '        Trace(7, peer, qp);'),
        ('        ready = target == head;',
         '        Trace(8, peer, qp);'),
    )
    for anchor, prefix in anchors:
        assert text.count('\n' + anchor) == 1, anchor
        text = text.replace('\n' + anchor, '\n' + prefix + '\n' + anchor)
    notify_wait = '        if (TileXR::UDMAPollCQUntilSqTail(info, peer, qp, queue_.Load(sq->headAddr), observer, queue_)) {'
    assert text.count(notify_wait) == 1
    text = text.replace(notify_wait, '        Trace(2, peer, qp);\n' + notify_wait)
    after_notify = '        return result ? uint32_t(TransportError) : uint32_t(Success);'
    assert text.count(after_notify) == 1
    text = text.replace(after_notify, '        Trace(4, peer, qp);\n' + after_notify)
    text = text.replace('    QueueState queue_;', '    QueueState queue_;\n    uint64_t trace_ = 0;')
    text = text.replace('    uint32_t result = TileXREpHt::Submit(config, owner, transport);',
                        '    transport.BindTrace(status);\n    uint32_t result = TileXREpHt::Submit(config, owner, transport);')
    (target / worker.name).write_text(text)


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else 'profile'
    assert mode in ('profile', 'ready-probe', 'joint-probe', 'inter-probe') and len(sys.argv) <= 2
    target = WORK / (mode + '-build')
    target.mkdir()
    entries = ENTRIES
    if mode == 'ready-probe':
        stage_ready_probe(target)
        entries = {key: value for key, value in ENTRIES.items() if 'local_return' in key}
    elif mode == 'joint-probe':
        stage_joint_probe(target)
        entries = {'tilexr_ht_pipeline_sst': ENTRIES['tilexr_ht_pipeline_sst']}
    elif mode == 'inter-probe':
        stage_inter_probe(target)
        entries = {'tilexr_ht_pipeline_sst': ENTRIES['tilexr_ht_pipeline_sst']}
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
    includes = [target, TOOL_DIR, SOURCE/'include', SOURCE/'src/ep/combine_ht',
                SOURCE/'src/moonep/combine_ht/common', SOURCE/'src/moonep/combine_ht/kernels',
                *(CANN / name for name in tool_include)]
    flags = [*options, *('-I' + str(folder) for folder in includes)]
    for entry, relative in entries.items():
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
    summary['all_compiled'] = len(summary['targets']) == len(entries) and all(
        row['exit_code'] == 0 for row in summary['targets'].values())
    (target/'build-summary.json').write_text(json.dumps(summary, indent=2) + '\n')
    return 0 if summary['all_compiled'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
