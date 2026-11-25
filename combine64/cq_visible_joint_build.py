"""Build the joint SQ/CQ variant and a strided leader-read comparison."""
import hashlib,json,subprocess
from pathlib import Path
ROOT=Path('/work')
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def use_strided_leader_reads(source):
    first=source.index('__aicore__ bool JointBarrier(')
    end=source.index('__aicore__ void JointCopyPartial(',first)
    barrier=source[first:end]
    replacements=(
        ('pipe.InitBuffer(read,block==0?8192:32);','pipe.InitBuffer(read,block==0?16*32:32);'),
        ('if(block==0) DataCopy(snapshot,gm,16*128);',
         'if(block==0) DataCopyPad(snapshot,gm,DataCopyExtParams(16,4,512-4,0,0),\n'
         '                                DataCopyPadExtParams<uint32_t>{false,0,0,0});'),
        ('snapshot.GetValue(lane*128)','snapshot.GetValue(lane*8)'),
    )
    for old,new in replacements:
        assert barrier.count(old)==1
        barrier=barrier.replace(old,new)
    return source[:first]+barrier+source[end:]


def main(tools,strided=False):
    tools=Path(tools)
    prior=json.loads((ROOT/'cases/64p-t16-pipeline-a3/phase-result.json').read_text())
    assert prior['run']['status']=='timed_out' and prior['kernel_compatibility_variant']=='sq-align-all512'
    api=json.loads((ROOT/'cq-compiler-barrier-a2/report.json').read_text())
    assert api['exit_codes']==[0,0] and not api['text_identical']
    api_root=ROOT/'cq-compiler-barrier-a2/include'
    assert sha(api_root/'tilexr/comm/udma/device/api.h')==api['overlay_header_sha256']
    assert api['overlay_header_sha256']=='bd9c87e965d2fe712693048c5a5dd3f2f58ee202f67c64bf5f95ced280f049f3'
    assert sha(ROOT/'source/include/tilexr/comm/udma/device/api.h')==api['baseline_header_sha256']
    baseline=json.loads((ROOT/'pipeline-four-module-sq-align-all512-a1/build-result.json').read_text())
    source=ROOT/'inter-sq-align512-a1/joint.cpp';assert sha(source)==baseline['source_sha256']
    assert sha(ROOT/'inter-sq-align512-a1/inter_server_send_kernel.cpp')==baseline['compatibility_inter_source_sha256']
    local_root=ROOT/'sq-align512-a1/include'
    assert sha(local_root/'combine_ht_kernel.h')==baseline['compatibility_header_sha256']
    variant='four-module-sq-align-all512-cq'
    barrier_algorithm='leader_release'
    original_source_sha256=sha(source)
    if strided:
        original_build=ROOT/'pipeline-four-module-sq-align-all512-cq-a1'
        original=json.loads((original_build/'build-result.json').read_text())
        assert original['source_sha256']==original_source_sha256
        original_wrapper=original_build/'pipeline_kernel.cpp'
        assert sha(original_wrapper)==original_source_sha256
        assert original['compatibility_api_header_sha256']==api['overlay_header_sha256']
        snapshot=json.loads((ROOT/'cases/64p-t16-pipeline-a4-snapshot240us-a1/diagnostic.json').read_text())
        assert snapshot['snapshot_available'] and snapshot['queue_totals']['pending_queues']==0
        assert snapshot['status_histograms']['inter-send']=={'0':1024}
        binary=(original_build/'kernel.o').read_bytes()
        assert hashlib.sha256(binary+bytes(10485760-len(binary))).hexdigest()==snapshot['kernel_sha256']
        variant+='-strided'
        barrier_algorithm='leader_release_strided'
        source=ROOT/'pipeline-four-module-sq-align-all512-cq-strided.cpp'
        with source.open('x') as output:
            output.write(use_strided_leader_reads(original_wrapper.read_text()))
    target=ROOT/('pipeline-'+variant+'-a1');assert not target.exists()
    script=(tools/'pipeline_build.sh').read_text()
    assert script.count('target=/work/pipeline-build-40596e2-a1')==1
    assert script.count('includes=("-I$(dirname "$1")"')==1
    script=script.replace('target=/work/pipeline-build-40596e2-a1','target='+str(target))
    script=script.replace('includes=("-I$(dirname "$1")"','includes=("-I'+str(local_root)+'" "-I'+str(api_root)+'" "-I'+str(tools)+'" "-I$(dirname "$1")"')
    builder=ROOT/('pipeline-'+variant+'-build.sh');assert not builder.exists();builder.write_text(script)
    with (ROOT/('pipeline-'+variant+'-compile.log')).open('wb') as log:
        code=subprocess.run(['bash',str(builder),str(source)],stdout=log,stderr=subprocess.STDOUT,timeout=300).returncode
    if code:return code
    report=json.loads((target/'build-result.json').read_text())
    report.update(source_sha256=sha(source),baseline_pipeline_object_sha256=baseline['sha256'],
        pipeline_build_variant=variant,compiler_scalar_dcci=False,
        stage_isolation_noinline=True,barrier_algorithm=barrier_algorithm,extra_round_fence=False,
        kernel_compatibility_variant='sq-align-all512-cq-barrier'+('-strided' if strided else ''),
        compatibility_header_sha256=baseline['compatibility_header_sha256'],
        compatibility_baseline_header_sha256=baseline['compatibility_baseline_header_sha256'],
        compatibility_inter_source_sha256=baseline['compatibility_inter_source_sha256'],
        compatibility_api_header_sha256=api['overlay_header_sha256'],compatibility_baseline_api_header_sha256=api['baseline_header_sha256'])
    (target/'build-result.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,separators=(',',':')),flush=True);return 0
if __name__=='__main__':
    import sys
    raise SystemExit(main(sys.argv[1],strided='--strided' in sys.argv[2:]))
