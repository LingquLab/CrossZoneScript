"""Apply the TL prefix repair to fresh source/exports; compile only, no NPU query."""
from pathlib import Path
import hashlib
import json
import os
import shutil
import sys
import zipfile

ROOT = Path('/root/tilelang-107-general-64ed5828-20261117-run02')
SUPPORT = ROOT / 'runner-build04'
sys.path.insert(0, str(ROOT))
from operations import CANN, integrity, process_gate, require, run, save, sha

CANDIDATE = '64ed582898e1321a0e93a2c4e0e78a6deb2f4868'
REVISION = 'dce8baa3572fd7f5b6ee31c523985f52ff9e0fe3'
OLD_FAILURE = '5ceed229076436b8badd99e43f7a7d54bd77ff6379391043d45fb2d82440b10e'
BUILD03_FAILURE = '113275dbdd2f4c2893e85048048b1a915043b8b81a0ebd3ca2390794723a3afc'
ARCHIVE_SHA = 'd7af4a5ea1c73fa747b9f294dcdc0e972e20a81159cf593ea54e198353e6fada'
MANIFEST_SHA = 'a90e13176171e714d14da2e56404b766851a111bc1cd158601bfbf693eb7741a'


def prerequisite():
    require(os.geteuid() == 0 and sha('/etc/machine-id') == '78020a81dd869c8ca5a7ba9f4a18b25a9fead957b807f74e65793fced37b9af0', 'Host identity mismatch')
    require(sha(ROOT / 'build-summary.json') == OLD_FAILURE, 'Original build summary changed')
    require(sha(ROOT / 'runner-build03/summary.json') == BUILD03_FAILURE, 'Audited build03 changed')
    previous = json.loads((ROOT / 'runner-build03/summary.json').read_text())
    require(previous['error_detail'] == 'First failed command: runner03-build' and not previous['npu_launch'], 'Prior effects unexpected')
    require(not any((ROOT / p).exists() for p in ('correctness', 'capture', 'correctness-launch', 'capture-launch')), 'Device phase exists')
    for label in ('core-build', 'core-install', 'query'):
        require(json.loads((ROOT / (label + '.receipt.json')).read_text())['exit_code'] == 0, 'Completed prerequisite failed')
    require((ROOT / 'query.log').read_text().splitlines() == ['0 0 64', '1 0 64'], 'Saved cores changed')


def main():
    require(len(sys.argv) == 2 and sys.argv[1] in ('prepare', 'build'), 'Unknown action')
    prerequisite()
    if sys.argv[1] == 'prepare':
        SUPPORT.mkdir(exist_ok=False)
        print(json.dumps({'status': 'PREPARED', 'npu_launch': False, 'directory': str(SUPPORT)}))
        return 0
    require(SUPPORT.is_dir() and not (SUPPORT / 'summary.json').exists(), 'Missing preparation or already completed')
    result = dict(schema='tilelang_general_107_build.v1', candidate=CANDIDATE,
                  experiment_revision=REVISION, status='FAIL_OR_UNVERIFIED', npu_launch=False,
                  device_queries=False, clock='NOT_VERIFIED', directory=str(ROOT),
                  continuation_directory=str(SUPPORT), core_rebuilt=False,
                  source_modified=True, export_modified=True,
                  first_cann_failure_sha256=BUILD03_FAILURE)
    try:
        process_gate(SUPPORT)
        result['integrity'] = integrity()
        original = json.loads((ROOT / 'payload-manifest.json').read_text())
        for name, digest in original['files'].items():
            require(sha(ROOT / name) == digest, 'Original payload changed: ' + name)
        require(sha(ROOT / 'general-64ed5828-source.zip') == '7f5ce28f4a494db6353d965083ada7f8e26532042b52ce20832106718abd87de', 'Frozen source archive changed')
        with zipfile.ZipFile(ROOT / 'general-64ed5828-source.zip') as frozen:
            for info in frozen.infolist():
                if not info.is_dir():
                    require((ROOT / 'source' / info.filename).read_bytes() == frozen.read(info), 'Frozen source modified: ' + info.filename)
        archive = SUPPORT / 'prefix-fix04.zip'
        require(archive.stat().st_size == 107162 and sha(archive) == ARCHIVE_SHA, 'Repair archive changed')
        with zipfile.ZipFile(archive) as z:
            manifest_bytes = z.read('repair-manifest.json')
            require(hashlib.sha256(manifest_bytes).hexdigest() == MANIFEST_SHA, 'Repair manifest changed')
            manifest = json.loads(manifest_bytes)
            require(manifest['candidate'] == CANDIDATE and manifest['experiment_revision'] == REVISION, 'Revision mismatch')
            require(set(z.namelist()) == set(manifest['files']) | {'repair-manifest.json'}, 'Archive members mismatch')
            source = ROOT / 'source04'; exports = ROOT / 'exports04'
            require(not source.exists() and not exports.exists(), 'Repair source already exists')
            shutil.copytree(ROOT / 'source', source, symlinks=True)
            for name, digest in manifest['files'].items():
                relative = Path(name)
                require(not relative.is_absolute() and '..' not in relative.parts, 'Unsafe archive member')
                data = z.read(name)
                require(hashlib.sha256(data).hexdigest() == digest, 'Repair member changed')
                if name.startswith('source-patch/'):
                    suffix = name[len('source-patch/'):]
                    target = source / suffix
                    require(sha(target) == manifest['source_old_sha256'][suffix], 'Source base changed: ' + suffix)
                else:
                    require(name.startswith('exports/'), 'Unexpected repair member')
                    target = exports / name[len('exports/'):]
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
            (SUPPORT / 'repair-manifest.json').write_bytes(manifest_bytes)
        # Validate every declared TL source dependency before invoking CANN.
        metadata = json.loads((exports / 'raw/export.json').read_text())
        for name, digest in metadata['source_hashes'].items():
            require(sha(source / name) == digest, 'TL export provenance mismatch: ' + name)
        runtime_include = CANN / 'aarch64-linux/pkg_inc/runtime'
        for header in ('runtime/kernel.h', 'runtime/rt.h'):
            require((runtime_include / header).is_file(), 'Runtime header absent')
        core = ROOT / 'core-install/lib64/libtile-comm.so'; core_sha = sha(core)
        build = ROOT / 'build04'; install = ROOT / 'install04'
        require(not build.exists() and not install.exists(), 'Build04 already exists')
        configure = ['cmake', '-S', source / 'tools/tilelang_runtime_experiment', '-B', build,
                     '-DCMAKE_BUILD_TYPE=Release', '-DCMAKE_EXPORT_COMPILE_COMMANDS=ON',
                     '-DCMAKE_CXX_FLAGS=-D_GLIBCXX_USE_CXX11_ABI=0',
                     '-DCMAKE_INSTALL_PREFIX=' + str(install), '-DCMAKE_INSTALL_LIBDIR=lib64',
                     '-DTL_CANN_ROOT=' + str(CANN), '-DTL_COMM_PREFIX=' + str(ROOT / 'core-install'),
                     '-DTL_BACKEND_ROOT=' + str(ROOT / 'deps/backend'),
                     '-DTL_RUNTIME_RAW_DIR=' + str(exports / 'device'),
                     '-DTL_RUNTIME_PROFILE_DIR=' + str(exports / 'profile'),
                     '-Druntime_include:PATH=' + str(runtime_include),
                     '-DCMAKE_INSTALL_RPATH=' + str(ROOT / 'core-install/lib64') + ';' + str(CANN / 'lib64')]
        run(SUPPORT, 'runner04-configure', configure, 60)
        cache = (build / 'CMakeCache.txt').read_text()
        require('CMAKE_CXX_FLAGS:STRING=-D_GLIBCXX_USE_CXX11_ABI=0' in cache, 'ABI0 flag absent')
        run(SUPPORT, 'runner04-build', ['cmake', '--build', build, '--verbose', '-j4'], 300)
        run(SUPPORT, 'runner04-install', ['cmake', '--install', build], 30)
        runner = install / 'bin/tilelang_runtime_runner'
        run(SUPPORT, 'runner04-ldd', ['ldd', runner], 15)
        ldd = (SUPPORT / 'runner04-ldd.log').read_bytes()
        require(b'not found' not in ldd and b'devlib' not in ldd, 'Library resolution failed')
        raw = list((install / 'lib64/tilexr/kernels').glob('*_raw.o'))
        profile = list((install / 'lib64/tilexr/kernels').glob('*_profile.o'))
        require(len(raw) == len(profile) == 1, 'Expected one raw/profile linked ELF')
        artifacts = [runner, core, *raw, *profile, *list(build.rglob('*_rel.o'))]
        require(len(artifacts) == 6, 'Expected six compiled artifacts')
        for path in artifacts:
            data = path.read_bytes()
            require(data[:4] == b'\x7fELF' and data[5] == 1, 'Invalid ELF')
            if path.suffix == '.o':
                require(int.from_bytes(data[16:18], 'little') == (1 if path.name.endswith('_rel.o') else 2), 'Device ELF type mismatch')
            else:
                require(int.from_bytes(data[18:20], 'little') == 183, 'Host ELF architecture mismatch')
        require(sha(core) == core_sha, 'Core library changed')
        # Later device gates verify all repaired source/export bytes as well as ELF bytes.
        artifacts.extend([source / name for name in manifest['source_old_sha256']])
        artifacts.extend(p for p in exports.rglob('*') if p.is_file())
        artifacts.append(SUPPORT / 'repair-manifest.json')
        hashes = {str(path.relative_to(ROOT)): sha(path) for path in artifacts}
        result.update(status='PASS', cores=[64, 64], core_count_evidence='saved query; not repeated',
                      core_cxx11_abi=0, artifacts=hashes, runner=str(runner), raw_elf=str(raw[0]),
                      profile_elf=str(profile[0]), qualification_reused=True,
                      runtime_validation='NOT_RUN', compiled_capacity=metadata['capacity_limits'])
        result['integrity_after'] = integrity()
    except Exception as error:
        result.update(error_type=type(error).__name__, error_detail=str(error)[:350])
    save(SUPPORT / 'summary.json', result)
    if result['status'] == 'PASS':
        prerequisite()
        save(ROOT / 'artifact-hashes.json', result['artifacts'])
        save(ROOT / 'build-summary.json', result)
    compact = {k: v for k, v in result.items() if k != 'artifacts'}
    compact['summary_sha256'] = sha(SUPPORT / 'summary.json')
    if 'artifacts' in result:
        compact['compiled_artifacts'] = {k: v for k, v in result['artifacts'].items() if k.endswith(('.o', '.so')) or k.endswith('/tilelang_runtime_runner')}
    print(json.dumps(compact, ensure_ascii=True, separators=(',', ':')))
    return 0 if result['status'] == 'PASS' else 1


if __name__ == '__main__':
    raise SystemExit(main())
