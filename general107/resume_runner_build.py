"""Continue only the unbuilt runner/ELFs; preserve the audited failed build."""
from pathlib import Path
import json
import os
import sys
import zipfile

ROOT = Path('/root/tilelang-107-general-64ed5828-20261117-run02')
sys.path.insert(0, str(ROOT))
from operations import CANN, integrity, process_gate, require, run, save, sha

CANDIDATE = '64ed582898e1321a0e93a2c4e0e78a6deb2f4868'
FAILURE_SHA = '5ceed229076436b8badd99e43f7a7d54bd77ff6379391043d45fb2d82440b10e'


def configure_command(source, build, install, runtime_include):
    return ['cmake', '-S', source / 'tools/tilelang_runtime_experiment', '-B', build,
            '-DCMAKE_BUILD_TYPE=Release', '-DCMAKE_EXPORT_COMPILE_COMMANDS=ON',
            '-DCMAKE_CXX_FLAGS=-D_GLIBCXX_USE_CXX11_ABI=0',
            '-DCMAKE_INSTALL_PREFIX=' + str(install), '-DCMAKE_INSTALL_LIBDIR=lib64',
            '-DTL_CANN_ROOT=' + str(CANN), '-DTL_COMM_PREFIX=' + str(ROOT / 'core-install'),
            '-DTL_BACKEND_ROOT=' + str(ROOT / 'deps/backend'),
            '-DTL_RUNTIME_RAW_DIR=' + str(ROOT / 'exports/device'),
            '-DTL_RUNTIME_PROFILE_DIR=' + str(ROOT / 'exports/profile'),
            '-Druntime_include:PATH=' + str(runtime_include),
            '-DCMAKE_INSTALL_RPATH=' + str(ROOT / 'core-install/lib64') + ';' + str(CANN / 'lib64')]


def main():
    require(os.geteuid() == 0 and sha('/etc/machine-id') == '78020a81dd869c8ca5a7ba9f4a18b25a9fead957b807f74e65793fced37b9af0', 'Host identity mismatch')
    failure = ROOT / 'build-summary.json'
    require(sha(failure) == FAILURE_SHA, 'Audited failure changed; do not replay')
    previous = json.loads(failure.read_text())
    require(previous['candidate'] == CANDIDATE and previous['npu_launch'] is False and
            previous['error_detail'] == 'First failed command: runner-configure', 'Unexpected prior effects')
    require(not any((ROOT / p).exists() for p in ('correctness', 'capture', 'correctness-launch', 'capture-launch')), 'Device phase exists')
    for label in ('query', 'query-compile', 'core-configure', 'core-build', 'core-install'):
        receipt = json.loads((ROOT / (label + '.receipt.json')).read_text())
        require(receipt['exit_code'] == 0 and not receipt.get('timed_out'), 'Required completed phase failed')
    require((ROOT / 'query.log').read_text().splitlines() == ['0 0 64', '1 0 64'], 'Saved core count changed')
    support = ROOT / 'runner-build03'
    support.mkdir(exist_ok=False)
    with (support / 'original-build-summary.json').open('xb') as stream:
        stream.write(failure.read_bytes())
    result = {'schema': 'tilelang_general_107_build.v1', 'candidate': CANDIDATE,
              'status': 'FAIL_OR_UNVERIFIED', 'npu_launch': False, 'device_queries': False,
              'clock': 'NOT_VERIFIED', 'directory': str(ROOT), 'continuation_directory': str(support),
              'original_failure_sha256': FAILURE_SHA, 'core_rebuilt': False,
              'source_modified': False, 'export_modified': False}
    try:
        process_gate(support)
        result['integrity'] = integrity()
        manifest = json.loads((ROOT / 'payload-manifest.json').read_text())
        require(manifest['candidate'] == CANDIDATE, 'Payload candidate mismatch')
        for name, digest in manifest['files'].items():
            require(sha(ROOT / name) == digest, 'Payload changed: ' + name)
        source = ROOT / 'source'
        source_zip = ROOT / 'general-64ed5828-source.zip'
        require(sha(source_zip) == '7f5ce28f4a494db6353d965083ada7f8e26532042b52ce20832106718abd87de', 'Source archive changed')
        with zipfile.ZipFile(source_zip) as archive:
            for info in archive.infolist():
                if not info.is_dir():
                    require((source / info.filename).read_bytes() == archive.read(info), 'Frozen source changed: ' + info.filename)
        for name, digest in manifest['files'].items():
            if name.startswith('runner/'):
                require(sha(source / 'tools/tilelang_runtime_experiment' / name[len('runner/'):]) == digest, 'Runner source changed')
        # The legacy, successfully built direct runner searches this extra level.
        # Verify the real target header before supplying the cache override.
        runtime_include = CANN / 'aarch64-linux/pkg_inc/runtime'
        headers = [runtime_include / 'runtime/kernel.h', runtime_include / 'runtime/rt.h']
        for header in headers:
            require(header.is_file(), 'Required installed runtime header absent: ' + str(header))
        result['runtime_headers'] = {str(p): sha(p) for p in headers}
        core = ROOT / 'core-install/lib64/libtile-comm.so'
        core_sha = sha(core)
        result['reused_core_sha256'] = core_sha
        build = ROOT / 'build03'; install = ROOT / 'install03'
        require(not build.exists() and not install.exists(), 'Runner continuation already exists')
        run(support, 'runner03-configure', configure_command(source, build, install, runtime_include), 60)
        cache = (build / 'CMakeCache.txt').read_text()
        require('CMAKE_CXX_FLAGS:STRING=-D_GLIBCXX_USE_CXX11_ABI=0' in cache and
                'runtime_include:PATH=' + str(runtime_include) in cache, 'ABI0/header configuration not applied')
        run(support, 'runner03-build', ['cmake', '--build', build, '--verbose', '-j4'], 300)
        run(support, 'runner03-install', ['cmake', '--install', build], 30)
        runner = install / 'bin/tilelang_runtime_runner'
        run(support, 'runner03-ldd', ['ldd', runner], 15)
        ldd = (support / 'runner03-ldd.log').read_bytes()
        require(b'not found' not in ldd and b'devlib' not in ldd, 'Bad runtime library resolution')
        raw = list((install / 'lib64/tilexr/kernels').glob('*_raw.o'))
        profile = list((install / 'lib64/tilexr/kernels').glob('*_profile.o'))
        require(len(raw) == len(profile) == 1, 'Expected one raw/profile linked ELF')
        artifacts = [runner, core, *raw, *profile, *list(build.rglob('*_rel.o'))]
        require(len(artifacts) == 6, 'Expected Host/core/two linked/two relocatable artifacts')
        for path in artifacts:
            data = path.read_bytes()
            require(data[:4] == b'\x7fELF' and data[5] == 1, 'Invalid ELF')
            if path.suffix == '.o':
                require(int.from_bytes(data[16:18], 'little') == (1 if path.name.endswith('_rel.o') else 2), 'Device ELF type')
            else:
                require(int.from_bytes(data[18:20], 'little') == 183, 'Host architecture mismatch')
        require(sha(core) == core_sha, 'Reused core changed')
        hashes = {str(path.relative_to(ROOT)): sha(path) for path in artifacts}
        result.update(status='PASS', cores=[64, 64], core_count_evidence='saved query receipt; no repeated runtime query',
                      core_cxx11_abi=0, artifacts=hashes, runner=str(runner), raw_elf=str(raw[0]),
                      profile_elf=str(profile[0]), runtime_validation='NOT_RUN', qualification_reused=True,
                      compiled_capacity=json.loads((ROOT / 'exports/device/runtime-device.json').read_text())['capacity_limits'])
        result['integrity_after'] = integrity()
        save(support / 'artifact-hashes.json', hashes)
    except Exception as error:
        result.update(status='FAIL_OR_UNVERIFIED', error_type=type(error).__name__, error_detail=str(error)[:350])
    save(support / 'summary.json', result)
    if result['status'] == 'PASS':
        require(sha(failure) == FAILURE_SHA, 'Original failure changed during continuation')
        save(ROOT / 'artifact-hashes.json', result['artifacts'])
        save(failure, result)
    print(json.dumps(result, ensure_ascii=True, separators=(',', ':')))
    return 0 if result['status'] == 'PASS' else 1


if __name__ == '__main__':
    raise SystemExit(main())
