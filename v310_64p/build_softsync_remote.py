import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess

source_package = Path('/work/src/dispatch_memory_64p_handoff')
destination = Path('/work/results/v310-native-64p-softsync-build-20261119-02')
single_round = 'single_round_probe' in globals()
if single_round:
    destination = Path('/work/results/v310-native-64p-single-round-build-20261119-01')
relative_header = 'kernel/source/src/ep/kernels/tilexr_ep_dispatch_memory_kernel.h'
binary = destination/'tilexr_ep_dispatch_memory_kernel.o'
result = {'binary': str(binary), 'sst_started': False}

try:
    check = subprocess.run(['sha256sum', '-c', '--quiet', 'SHA256SUMS'], cwd=source_package,
                           capture_output=True, timeout=20)
    assert check.returncode == 0, 'SOURCE_PACKAGE_SHA'
    destination.mkdir()
    package = destination/'package'
    shutil.copytree(source_package, package)
    header = package/relative_header
    baseline = header.read_bytes()
    source = b'        SyncAll();\n'
    replacement = b'''        // Simulator-only workspace supplied through the unused scale pointer (quantMode=0).
        AscendC::GlobalTensor<int32_t> syncGlobal;
        syncGlobal.SetGlobalBuffer(reinterpret_cast<__gm__ int32_t *>(
            simulationSyncWorkspaceGM_), aivNum_ * 8U);
        // Both roles have finished using this scratch before the barrier, and overwrite it on reuse.
        AscendC::SyncAll(syncGlobal, subExpBuf_.Get<int32_t>(), static_cast<int32_t>(aivNum_));
'''
    assert baseline.count(source) == 1, 'SYNCALL_CALL_SITE_IDENTITY'
    declaration = b'    GlobalTensor<uint8_t> dynamicScalesOutGMTensor_;\n'
    initialization = b'    dynamicScalesOutGMTensor_.SetGlobalBuffer(reinterpret_cast<__gm__ uint8_t *>(dynamicScalesOut));\n'
    assert baseline.count(declaration) == baseline.count(initialization) == 1, 'WORKSPACE_POINTER_IDENTITY'
    changed = baseline.replace(declaration, declaration+b'    GM_ADDR simulationSyncWorkspaceGM_{nullptr};\n')
    changed = changed.replace(initialization, initialization+b'    simulationSyncWorkspaceGM_ = dynamicScalesOut;\n')
    changed = changed.replace(source, replacement)
    if single_round:
        start = b'    const bool doubleGroupEnabled = !isSmallBs_ && IsDoubleGroupRankSize() &&\n'
        end = b'    sendRoundNum_ = doubleGroupEnabled ? TileXREp::kEpMemoryDoubleGroupRoundNum : 1U;\n'
        assert baseline.count(start) == baseline.count(end) == 1
        before, tail = baseline.split(start)
        _, after = tail.split(end)
        changed = before + b'    sendRoundNum_ = 1U;\n' + after
    header.write_bytes(changed)
    sums = package/'SHA256SUMS'
    lines = sums.read_text().splitlines()
    entries = [i for i, line in enumerate(lines) if line.split()[-1] == relative_header]
    assert len(entries) == 1, 'SOURCE_MANIFEST_HEADER'
    result['header_sha256'] = hashlib.sha256(changed).hexdigest()
    lines[entries[0]] = result['header_sha256'] + '  ' + relative_header
    sums.write_text('\n'.join(lines)+'\n')
    launch = ['bash', '-lc', 'source /opt/sim/cann-env.sh && exec bash "$1" --out "$2" --format linked',
              'build', str(package/'kernel/build.sh'), str(binary)]
    build = subprocess.run(launch, cwd=destination, capture_output=True, timeout=165)
    (destination/'launcher.stdout.log').write_bytes(build.stdout)
    (destination/'launcher.stderr.log').write_bytes(build.stderr)
    result['compiler_exit'] = build.returncode
    log = binary.with_suffix('.o.build.log')
    messages = ((log.read_bytes() if log.is_file() else b'') + build.stderr)
    result['errors'] = [line[:200] for line in messages.decode(errors='replace').splitlines()
                        if re.search(r'error:|undefined symbol|not found', line, re.I)][-8:]
    if build.returncode == 0:
        result['kernel_sha256'] = hashlib.sha256(binary.read_bytes()).hexdigest()
        result['status'] = 'BUILT'
except (AssertionError, OSError, subprocess.TimeoutExpired) as error:
    result['error'] = str(error) if isinstance(error, AssertionError) else type(error).__name__
print(json.dumps(result, ensure_ascii=True, separators=(',', ':')))
