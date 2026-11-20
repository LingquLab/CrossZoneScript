from pathlib import Path
import hashlib
import json
import shutil
import subprocess


source = Path('/work/src/dispatch_tilelang_64p_68d32608_build01')
target = Path('/work/src/dispatch_tilelang_64p_68d32608_f1scalar01')
state = {'stage':'build','path':str(target),'compatibility':'F1 route normalization uses scalar bounds checking'}
try:
    target.mkdir()
    for name in ('include','shmem-device','contract'):
        shutil.copytree(source/name,target/name)
    for name in ('compile.sh','export.json'):
        shutil.copy2(source/name,target/name)
    old=(source/'dispatch.profile.cpp').read_text()
    with (source/'runtime-profile.json').open() as stream:
        metadata=json.load(stream)
    assert hashlib.sha256(old.encode()).hexdigest()==metadata['device_sha256']
    first='        auto values = data_b[0].ReinterpretCast<int>();'
    last='        if (p.GetValue(12) != (uint64_t)0) {'
    start=old.index(first)
    end=old.index(last,start)
    assert old[start:end].count('AscendC::Select(')==1
    replacement='''        for (int32_t index = 0; index < count; ++index) {
          const int routeId = route.GetValue(index);
          if (routeId < 0 || routeId >= experts) {
            route.SetValue(index, -1);
          }
        }
'''
    patched=old[:start]+replacement+old[end:]
    (target/'dispatch.profile.cpp').write_text(patched)
    metadata['compatibility']=state['compatibility']
    metadata['device_sha256']=hashlib.sha256(patched.encode()).hexdigest()
    (target/'runtime-profile.json').write_text(json.dumps(metadata,indent=2)+'\n')
    with (target/'build.log').open('xb') as stream:
        proc=subprocess.run(['bash',str(target/'compile.sh')],cwd=target,stdout=stream,stderr=subprocess.STDOUT,timeout=160)
    if proc.returncode:
        raise ValueError('BUILD_FAILED: ' + ' | '.join(line[:160] for line in (target/'build.log').read_text().splitlines() if 'error:' in line)[-900:])
    elf=(target/'dispatch.elf.txt').read_text()
    assert 'EXEC' in elf and '0x990000' in elf and metadata['entry'] in elf
    state.update(status='PASS',elf_sha=hashlib.sha256((target/'dispatch.o').read_bytes()).hexdigest(),
                 source_sha=metadata['device_sha256'],base_source_sha=hashlib.sha256(old.encode()).hexdigest(),
                 revision='68d326088bde493f5188cc8b9b2e4fdd9c290d23')
except (AssertionError,OSError,ValueError,subprocess.TimeoutExpired) as error:
    state.update(status='FAIL',reason=str(error)[:1000])
if target.is_dir() and not (target/'build-receipt.json').exists():
    (target/'build-receipt.json').write_text(json.dumps(state,indent=2)+'\n')
if state['status']=='PASS':
    exec(compile(stage_source,'stage_compat','exec'))
else:
    print(json.dumps(state,separators=(',',':')))
