from pathlib import Path
import hashlib
import io
import json
import re
import subprocess
import tarfile
import time


def digest(data):
    return hashlib.sha256(data).hexdigest()


root = Path('/work/src/dispatch_tilelang_64p_68d32608_build01')
state = {'stage': 'build', 'path': str(root), 'revision': manifest['revision']}
try:
    root.mkdir()
    assert digest(archive) == manifest['archive'], 'ARCHIVE_SHA'
    with tarfile.open(fileobj=io.BytesIO(archive), mode='r:gz') as bundle:
        assert len(bundle.getmembers()) == len(manifest['files']), 'ARCHIVE_COUNT'
        for item in bundle:
            content = bundle.extractfile(item).read() if item.isfile() else None
            assert content is not None and digest(content) == manifest['files'][item.name], 'MEMBER_SHA'
            path = root / item.name
            assert path.resolve().is_relative_to(root), 'MEMBER_PATH'
            path.parent.mkdir(exist_ok=True, parents=True)
            with path.open('xb') as f:
                f.write(content)
    (root/'compile.sh').write_bytes(compile_source)
    (root/'source-manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    metadata = json.loads((root/'runtime-profile.json').read_text())
    assert metadata['abi_version'] == 3 and metadata['cores'] == 64 and metadata['args_bytes_aarch64'] == 112
    assert digest((root/'dispatch.profile.cpp').read_bytes()) == metadata['device_sha256']
    entry = metadata['entry']
    start = time.monotonic()
    with (root/'build.log').open('xb') as log:
        result = subprocess.run(['bash',str(root/'compile.sh')], cwd=root, stdout=log,
                                stderr=subprocess.STDOUT, timeout=410)
    state.update(exit=result.returncode, wall_s=round(time.monotonic()-start,3))
    if result.returncode:
        messages = (root/'build.log').read_text(errors='replace').splitlines()
        state['compiler_errors'] = [line[:360] for line in messages if re.search(r'error:|not found|unrecognized|undefined reference',line,re.I)][-12:]
        raise ValueError('TARGET_BUILD_FAILED')
    report = (root/'dispatch.elf.txt').read_text()
    assert re.search(r'^\s*Type:\s+EXEC\b',report,re.M) and re.search(r'^\s*Flags:.*0x990000',report,re.M), 'ELF_TYPE'
    assert any(re.search(r'\bFUNC\b',l) and ' UND ' not in l and l.split()[-1]==entry for l in report.splitlines() if l.strip()), 'ELF_ENTRY'
    state.update(status='PASS', entry=entry, elf_sha=digest((root/'dispatch.o').read_bytes()),
                 source_sha=metadata['device_sha256'], archived_members=len(manifest['files']))
except (AssertionError,ValueError,OSError,subprocess.TimeoutExpired) as error:
    state.update(status='FAIL',reason=str(error)[:240])
if root.is_dir() and not (root/'build-receipt.json').exists():
    (root/'build-receipt.json').write_text(json.dumps(state,indent=2)+'\n')
print(json.dumps(state,separators=(',',':')))
