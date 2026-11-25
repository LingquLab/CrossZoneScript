"""Build a pinned simulation-only SQ publication boundary compatibility variant."""
import hashlib,json,subprocess
from pathlib import Path

ROOT=Path('/work')
HEADER_SHA='f69bd5bbfbde82cafd142880746efe0e59818b17c21434a0d6d9d7c2493ce4d1'
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def main():
    header=ROOT/'source/src/moonep/combine_ht/kernels/combine_ht_kernel.h'
    raw=header.read_bytes();assert hashlib.sha256(raw.replace(b'\r\n',b'\n')).hexdigest()==HEADER_SHA
    original=raw.decode();newline='\r\n' if b'\r\n' in raw else '\n'
    text=original.replace('\r\n','\n')
    needle='''    template <bool Signal>
    __aicore__ inline uint32_t Flush(LocalTensor<int64_t> records, uint32_t count,
        uint64_t input, uint64_t remote, uint32_t bytes, uint64_t snapshotRow, GM_ADDR templates = nullptr)
    {'''
    assert text.count(needle)==1
    wrapper='''    template <bool Signal>
    __aicore__ inline uint32_t Flush(LocalTensor<int64_t> records, uint32_t count,
        uint64_t input, uint64_t remote, uint32_t bytes, uint64_t snapshotRow, GM_ADDR templates = nullptr)
    {
#if defined(TILEXR_COMBINE_HT_SIM_SEND) && TILEXR_COMBINE_HT_SIM_SEND == 1
        // SST routes a memory request to the Die selected by its first address.
        // Keep each fetch frontier within one 512-byte interleave block. Drain
        // at the boundary so doorbell coalescing cannot merge two blocks.
        uint32_t done = 0;
        while (done < count) {
            const uint32_t offset = head % (512U / kWqeBytes);
            if (head != 0 && offset == 0) {
                const uint32_t status = Quiet();
                if (status) return status;
            }
            const uint32_t room = 512U / kWqeBytes - offset;
            const uint32_t piece = count - done < room ? count - done : room;
            const uint32_t status = FlushPiece<Signal>(records, piece, input, remote, bytes,
                snapshotRow + done, templates + uint64_t(done) * kWqeBytes);
            if (status) return status;
            done += piece;
        }
        return TileXR::TILEXR_UDMA_STATUS_SUCCESS;
#else
        return FlushPiece<Signal>(records, count, input, remote, bytes, snapshotRow, templates);
#endif
    }
    template <bool Signal>
    __aicore__ inline uint32_t FlushPiece(LocalTensor<int64_t> records, uint32_t count,
        uint64_t input, uint64_t remote, uint32_t bytes, uint64_t snapshotRow, GM_ADDR templates = nullptr)
    {'''
    modified=text.replace(needle,wrapper).replace('\n',newline)
    dest=ROOT/'sq-align512-a1';dest.mkdir(exist_ok=False)
    overlay=dest/'include/combine_ht_kernel.h';overlay.parent.mkdir();overlay.write_bytes(modified.encode())
    baseline=json.loads((ROOT/'profile-build/result.json').read_text())['targets']['local_ht']
    cmd=list(baseline['commands'][0]);assert cmd[-2]=='-o'
    old=cmd[-1];obj=str(dest/'local_ht.rel.o');cmd[-1]=obj;cmd.insert(1,'-I'+str(dest/'include'))
    binary=dest/'local_ht.o';link=[obj if v==old else str(binary) if v==str(ROOT/baseline['path']) else v for v in baseline['commands'][1]]
    assert link[-2:]==['-o',str(binary)]
    codes=[]
    for n,command in enumerate((cmd,link)):
        with (dest/('build%d.log'%n)).open('wb') as log:code=subprocess.run(command,stdout=log,stderr=subprocess.STDOUT,timeout=200).returncode
        codes.append(code)
        if code:break
    report=dict(candidate_commit='40596e2d9d454b7a7b00b4b34b96d49158e1c3a3',variant='sq-align512',
        exit_codes=codes,baseline_header_sha256=sha(header),normalized_header_sha256=HEADER_SHA,
        overlay_header_sha256=sha(overlay),change='SIM_SEND only: split publication at 512B SQ boundaries and drain before crossing; same WQE contents/count',
        commands=[cmd,link],object_path=str(binary))
    if codes==[0,0]:report['object_sha256']=sha(binary)
    else:report['errors']=[s[:180] for s in (dest/('build%d.log'%(len(codes)-1))).read_text(errors='replace').splitlines() if 'error:' in s][:2]
    assert sha(header)==report['baseline_header_sha256']
    (dest/'report.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps({k:v for k,v in report.items() if k!='commands'},separators=(',',':')),flush=True)
    return 0 if codes==[0,0] else 1
if __name__=='__main__':raise SystemExit(main())
