"""Build isolated local/inter SQ boundary fixes after verified joint stage capture."""
import hashlib,json,subprocess
from pathlib import Path
ROOT=Path('/work')
SOURCE_SHA='1a223f5aff4ecc5f3adc1d8d0ea3aaf090d43103c4e9e7b3c81dcf168f2ec38e'
JOINT_SHA='1b3b99dcfdc77181e1937eec90c78285c478433ba312116127417747ebd41253'
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def modify(text):
    helper='''#ifdef TILEXR_EP_HT_SST_SPLIT_WRITES
    __aicore__ uint32_t DrainSqBoundary(uint32_t peer, uint32_t qp)
    {
        if (!TileXR::UDMAQueueOperationValid(args_, peer, qp)) {
            return TransportError;
        }
        auto info = TileXR::GetUDMAInfo(args_);
        auto context = TileXR::UDMAGetWQCtx(info, peer, qp);
        if (!context || context->bufAddr % kSlotBytes != 0) {
            return TransportError;
        }
        const uint32_t head = queue_.Load(context->headAddr);
        if (head != 0 && head % (kSlotBytes / TileXR::TILEXR_UDMA_WQE_BB_BYTES) == 0) {
            TileXR::UDMARingDoorbell(head, context);
            TileXR::UDMAIgnoreCompletion observer;
            if (TileXR::UDMAPollCQUntilSqTail(info, peer, qp, head, observer, queue_)) {
                return TransportError;
            }
        }
        return Success;
    }
#endif

'''
    needle='    __aicore__ uint32_t PostWrite(const Descriptor &d, uint32_t qp, uint64_t offset, uint64_t bytes)\n    {'
    assert text.count(needle)==1
    text=text.replace(needle,helper+needle+'''
#ifdef TILEXR_EP_HT_SST_SPLIT_WRITES
        if (DrainSqBoundary(d.peer, qp) != Success) {
            return TransportError;
        }
#endif''')
    needle='''        const uint32_t qp = kLanes * owner_ + lane;
        auto source = reinterpret_cast<__gm__ uint64_t *>'''
    assert text.count(needle)==1
    text=text.replace(needle,'''        const uint32_t qp = kLanes * owner_ + lane;
#ifdef TILEXR_EP_HT_SST_SPLIT_WRITES
        if (DrainSqBoundary(peer, qp) != Success) {
            return TransportError;
        }
#endif
        auto source = reinterpret_cast<__gm__ uint64_t *>''')
    needle='''        TileXR::UDMAPublishWqe(scratch, context, head, count);
        const uint32_t wqeCount = queue_.Load(context->wqeCntAddr);
        queue_.Store(context->headAddr, head + count);
        queue_.Store(context->wqeCntAddr, wqeCount + count);
        touched_[lane] |= PeerBit(peer);
        TileXR::UDMARingDoorbell(head + count, context);'''
    assert text.count(needle)==1
    text=text.replace(needle,'''#ifdef TILEXR_EP_HT_SST_SPLIT_WRITES
        const uint32_t wqeCount = queue_.Load(context->wqeCntAddr);
        uint32_t done = 0;
        while (done < count) {
            if (DrainSqBoundary(peer, qp) != Success) {
                return TransportError;
            }
            const uint32_t room = kSlotBytes / TileXR::TILEXR_UDMA_WQE_BB_BYTES -
                (head + done) % (kSlotBytes / TileXR::TILEXR_UDMA_WQE_BB_BYTES);
            const uint32_t piece = count - done < room ? count - done : room;
            TileXR::UDMAPublishWqe(scratch[done * TileXR::TILEXR_UDMA_WQE_BB_BYTES], context, head + done, piece);
            queue_.Store(context->headAddr, head + done + piece);
            queue_.Store(context->wqeCntAddr, wqeCount + done + piece);
            touched_[lane] |= PeerBit(peer);
            TileXR::UDMARingDoorbell(head + done + piece, context);
            done += piece;
        }
#else
'''+needle+'''
#endif''')
    return text
def main(tools):
    tools=Path(tools)
    local_report=json.loads((ROOT/'sq-align512-a1/report.json').read_text())
    assert sha(ROOT/'sq-align512-a1/include/combine_ht_kernel.h')==local_report['overlay_header_sha256']
    assert sha(ROOT/'source/src/moonep/combine_ht/kernels/combine_ht_kernel.h')==local_report['baseline_header_sha256']
    snap=json.loads((ROOT/'cases/64p-t16-pipeline-sq-align-snapshot200us-a1/diagnostic.json').read_text())
    assert snap['candidate_commit']=='40596e2d9d454b7a7b00b4b34b96d49158e1c3a3'
    assert snap['snapshot_available'] and snap['status_histograms']['local']=={'0':512} and snap['status_histograms']['return']=={'0':512}
    source=ROOT/'source/src/ep/combine_ht/inter_server_send_kernel.cpp';raw=source.read_bytes()
    text=raw.replace(b'\r\n',b'\n').decode();assert hashlib.sha256(text.encode()).hexdigest()==SOURCE_SHA
    modified=modify(text)
    dest=ROOT/'inter-sq-align512-a1';dest.mkdir(exist_ok=False)
    overlay=dest/'inter_server_send_kernel.cpp';overlay.write_text(modified)
    baseline=json.loads((ROOT/'profile-build/result.json').read_text())['targets']['inter_server_send']
    assert sha(ROOT/baseline['path'])==baseline['sha256']
    instrument=(ROOT/'profile-build/inter_server_send.cpp').read_text()
    prefix=text[:text.rindex('extern "C" __global__ __aicore__ void ')]
    newprefix=modified[:modified.rindex('extern "C" __global__ __aicore__ void ')]
    assert instrument.count(prefix)==1
    wrapper=dest/'instrumented.cpp';wrapper.write_text(instrument.replace(prefix,newprefix))
    cmd=list(baseline['commands'][0]);at=cmd.index('-c');cmd[at+1]=str(wrapper)
    old=cmd[-1];obj=str(dest/'inter.rel.o');cmd[-1]=obj;binary=dest/'inter.o'
    link=[obj if x==old else str(binary) if x==str(ROOT/baseline['path']) else x for x in baseline['commands'][1]]
    codes=[]
    for n,command in enumerate((cmd,link)):
        with (dest/('build%d.log'%n)).open('wb') as log:code=subprocess.run(command,stdout=log,stderr=subprocess.STDOUT,timeout=200).returncode
        codes.append(code)
        if code:break
    report=dict(candidate_commit='40596e2d9d454b7a7b00b4b34b96d49158e1c3a3',variant='sq-align-all512',exit_codes=codes,
        baseline_inter_sha256=sha(source),normalized_inter_sha256=SOURCE_SHA,overlay_inter_sha256=sha(overlay),commands=[cmd,link])
    if codes==[0,0]:report['inter_object_sha256']=sha(binary)
    (dest/'report.json').write_text(json.dumps(report,indent=2)+'\n')
    if codes!=[0,0]:return 1
    original=ROOT/'pipeline-four-module-joint-source.cpp';assert sha(original)==JOINT_SHA
    joint=original.read_text();needle='/work/source/src/ep/combine_ht/inter_server_send_kernel.cpp';assert joint.count(needle)==1
    generated=dest/'joint.cpp';generated.write_text(joint.replace(needle,str(overlay)))
    target=ROOT/'pipeline-four-module-sq-align-all512-a1';assert not target.exists()
    script=(tools/'pipeline_build.sh').read_text()
    assert script.count('target=/work/pipeline-build-40596e2-a1')==1
    assert script.count('includes=("-I$(dirname "$1")"')==1
    script=script.replace('target=/work/pipeline-build-40596e2-a1','target='+str(target))
    script=script.replace('includes=("-I$(dirname "$1")"','includes=("-I/work/sq-align512-a1/include" "-I'+str(tools)+'" "-I$(dirname "$1")"')
    builder=dest/'joint-build.sh';builder.write_text(script)
    with (dest/'joint-build.log').open('wb') as log:code=subprocess.run(['bash',str(builder),str(generated)],stdout=log,stderr=subprocess.STDOUT,timeout=300).returncode
    if code:return code
    build=json.loads((target/'build-result.json').read_text());local=json.loads((ROOT/'sq-align512-a1/report.json').read_text())
    build.update(source_sha256=sha(generated),baseline_source_sha256=JOINT_SHA,pipeline_build_variant='four-module-sq-align-all512',
        compiler_scalar_dcci=False,stage_isolation_noinline=True,barrier_algorithm='leader_release',extra_round_fence=False,
        kernel_compatibility_variant='sq-align-all512',compatibility_header_sha256=local['overlay_header_sha256'],
        compatibility_baseline_header_sha256=local['baseline_header_sha256'],compatibility_inter_source_sha256=sha(overlay))
    (target/'build-result.json').write_text(json.dumps(build,indent=2)+'\n')
    report.update(joint_object_sha256=build['sha256'],joint_source_sha256=sha(generated))
    assert sha(source)==report['baseline_inter_sha256']
    (dest/'report.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps({k:v for k,v in report.items() if k!='commands'},separators=(',',':')),flush=True);return 0
if __name__=='__main__':
    import sys
    raise SystemExit(main(sys.argv[1]))
