def stage_sync_probe(case, manifest, package):
    import hashlib
    import json
    from pathlib import Path
    import shutil
    import subprocess

    digest = lambda data: hashlib.sha256(data).hexdigest()
    check = subprocess.run(['sha256sum', '-c', '--quiet', 'SHA256SUMS'],
                           cwd=package, capture_output=True, timeout=20)
    assert check.returncode == 0, 'SOURCE_PACKAGE_SHA'
    copied = case/'probe-source'
    shutil.copytree(package, copied)
    relative = 'kernel/source/src/ep/kernels/tilexr_ep_dispatch_memory_kernel.cpp'
    source = copied/relative
    baseline = source.read_text()
    signature_end = ')\n{\n'
    assert baseline.count(signature_end) == 1
    signature, _ = baseline.split(signature_end)
    candidate = signature + signature_end + '    AscendC::SyncAll();\n}\n'
    source.write_text(candidate)
    sums = copied/'SHA256SUMS'
    lines = sums.read_text().splitlines()
    entries = [i for i, line in enumerate(lines) if line.split()[-1] == relative]
    assert len(entries) == 1
    lines[entries[0]] = digest(source.read_bytes()) + '  ' + relative
    sums.write_text('\n'.join(lines) + '\n')
    product = copied/'tilexr_ep_dispatch_memory_kernel.o'
    launch = ['bash', '-lc', 'source /opt/sim/cann-env.sh && exec bash "$1" --out "$2" --format linked',
              'build', str(copied/'kernel/build.sh'), str(product)]
    with (case/'sync-probe-build.log').open('wb') as log:
        build = subprocess.run(launch, stdout=log, stderr=subprocess.STDOUT, timeout=160)
    assert build.returncode == 0, 'SYNC_PROBE_BUILD_FAILED'
    binary = case/'tilexr_ep_dispatch_memory_kernel.o'
    shutil.copy2(product, binary)
    manifest['baseline_kernel_sha256'] = manifest['kernel_sha256']
    manifest['kernel_sha256'] = digest(binary.read_bytes())
    assert manifest['kernel_sha256'] != manifest['baseline_kernel_sha256']
    manifest['kernel_size_bytes'] = binary.stat().st_size
    manifest['synchronization_probe'] = 'HARDWARE_SYNCALL_ONLY'
    (case/'manifest.json').write_text(json.dumps(manifest, indent=2)+'\n')
    return manifest['kernel_sha256']
