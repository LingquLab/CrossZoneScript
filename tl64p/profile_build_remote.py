from pathlib import Path
import hashlib
import json
import shutil
import subprocess


SOURCE = Path('/work/src/dispatch_tilelang_64p_68d32608_f1scalar01')
BUILD = Path('/work/src/dispatch_tilelang_64p_68d32608_profile03')
SOURCE_SHA = 'b6d119082811193f155f0da3a11bae432131afec23cc5331f40065d90860bee2'
CLEAN_FUNCTION = '''
#if TILEXR_PROFILE_ACTIVE
TILEXR_PROFILE_DEVICE void CleanSimulationProfile(__gm__ uint8_t *address, uint32_t bytes)
{
    AscendC::GlobalTensor<uint8_t> global;
    global.SetGlobalBuffer(address, bytes);
    for (uint32_t offset = 0; offset < bytes; offset += 64U) {
        AscendC::DataCacheCleanAndInvalid<uint8_t, AscendC::CacheLine::SINGLE_CACHE_LINE,
            AscendC::DcciDst::CACHELINE_OUT>(global[offset]);
    }
}
#endif
'''
RECORD_CLEAN = '''        // Each lane owns its cache lines in this V310 fixture.
        CleanSimulationProfile(reinterpret_cast<__gm__ uint8_t *>(lane.timestamps), count * sizeof(uint64_t));
        CleanSimulationProfile(reinterpret_cast<__gm__ uint8_t *>(lane.eventIds), count * sizeof(uint32_t));
        lane.Commit(count, dropped);
        CleanSimulationProfile(reinterpret_cast<__gm__ uint8_t *>(lane.count), sizeof(uint32_t));
        CleanSimulationProfile(reinterpret_cast<__gm__ uint8_t *>(lane.dropped), sizeof(uint32_t));
        CleanSimulationProfile(reinterpret_cast<__gm__ uint8_t *>(lane.state), sizeof(uint32_t));'''


def build():
    previous = json.loads((SOURCE/'build-receipt.json').read_text())
    assert previous['status']=='PASS' and previous['elf_sha']==SOURCE_SHA
    assert hashlib.sha256((SOURCE/'dispatch.o').read_bytes()).hexdigest()==SOURCE_SHA
    BUILD.mkdir()
    for name in ('include','shmem-device','contract'):
        shutil.copytree(SOURCE/name,BUILD/name)
    for name in ('compile.sh','export.json','dispatch.profile.cpp','runtime-profile.json'):
        shutil.copy2(SOURCE/name,BUILD/name)
    header=BUILD/'include/tilexr/profiling/device.h'
    before=header.read_text()
    commit='        lane.Commit(count, dropped);'
    assert before.count(commit)==1
    scope='namespace TileXR { namespace Profiling {\n'
    assert before.count(scope)==1
    header.write_text(before.replace(scope,scope+CLEAN_FUNCTION).replace(commit,RECORD_CLEAN))
    with (BUILD/'build.log').open('xb') as stream:
        exit_code=subprocess.run(['bash',str(BUILD/'compile.sh')],cwd=BUILD,
                                 stdout=stream,stderr=subprocess.STDOUT,timeout=160).returncode
    if exit_code:
        errors=[line[:190] for line in (BUILD/'build.log').read_text().splitlines() if 'error:' in line]
        return {'status':'FAIL','phase':'build','errors':errors[-5:]}
    meta=json.loads((BUILD/'runtime-profile.json').read_text())
    elf=(BUILD/'dispatch.elf.txt').read_text()
    assert 'EXEC' in elf and '0x990000' in elf and meta['entry'] in elf
    result={'status':'PASS','phase':'build','revision':previous['revision'],
            'elf_sha':hashlib.sha256((BUILD/'dispatch.o').read_bytes()).hexdigest(),
            'base_elf_sha':SOURCE_SHA,'generated_source_sha':meta['device_sha256'],
            'profiler_header_sha':hashlib.sha256(header.read_bytes()).hexdigest()}
    (BUILD/'build-receipt.json').write_text(json.dumps(result,indent=2)+'\n')
    return result
