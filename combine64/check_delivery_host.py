"""Build the local and pipeline SST entry points in an isolated container."""
import base64
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import socket
import subprocess
import tarfile
import tempfile

IMAGE = 'sha256:19e063d4eebab9785ad24b175ee1567dfb24c1180698f67fe0ab32a731a19cd5'
REVISION = '4d37ae67cf2f6baa06b3efbe65c71649a6f24203'
COMMAND = r'''
set -eu
source /opt/sim/cann-env.sh
cann=/opt/sim/Ascend/cann-9.1.0
export PATH="$cann/tools/bisheng_compiler/bin:$cann/aarch64-linux/ccec_compiler/bin:$PATH"
cd /work
bisheng -xcce -O2 -DCATLASS_ARCH=3510 --cce-auto-sync --cce-aicore-arch=dav-c310-vec \
  -Wno-deprecated-declarations -mllvm -cce-aicore-stack-size=0x8000 \
  -mllvm -cce-aicore-function-stack-size=0x8000 -mllvm -cce-aicore-addr-transform \
  -mllvm -cce-aicore-dcci-insert-for-scalar=false \
  -DTILEXR_COMBINE_HT_SIM_SEND=1 -DTILEXR_COMBINE_HT_SST_FP32=1 \
  -DTILEXR_EP_HT_SST_SPLIT_WRITES=1 -DTILEXR_EP_HT_FINAL_REDUCE_SST_FP32=1 \
  -DTILEXR_EP_HT_PIPELINE_FP32=1 -DTILEXR_ENABLE_PROFILING=1 \
  --cce-aicore-only -std=gnu++17 -Iinclude -Isrc/ep/combine_ht \
  -Isrc/moonep/combine_ht/common -Isrc/moonep/combine_ht/kernels \
  -I"$cann/compiler/tikcpp" -I"$cann/compiler/tikcpp/tikcfw" \
  -I"$cann/compiler/tikcpp/tikcfw/impl" -I"$cann/compiler/tikcpp/tikcfw/interface" \
  -I"$cann/aarch64-linux/ascendc/include" -I"$cann/aarch64-linux/asc/include" \
  -I"$cann/aarch64-linux/include" -I"$cann/aarch64-linux/pkg_inc" -I"$cann/aarch64-linux/pkg_inc/runtime" \
  -c tools/unitest/simulator/experiments/inter_server_send/sst/combine_ht_pipeline_kernel.cpp -o delivery_rel.o
ld.lld -m aicorelinux -Ttext=0 delivery_rel.o --static --allow-multiple-definition -o delivery.o
for entry in tilexr_ht_local_send_timing_sst tilexr_ht_pipeline_sst; do
  readelf -Ws delivery.o | grep -E "[[:space:]]${entry}$" >/dev/null
done
sha256sum delivery.o
'''


def main(request):
    assert socket.gethostname().split('.')[0] == 'host-94-18'
    assert request['revision'] == REVISION
    raw = base64.b64decode(request['source'], validate=True)
    assert hashlib.sha256(raw).hexdigest() == request['source_sha256']
    with tempfile.TemporaryDirectory(prefix='combine-sst-delivery-') as directory:
        root = Path(directory)
        with tarfile.open(fileobj=io.BytesIO(raw), mode='r:gz') as source:
            for member in source:
                relative = PurePosixPath(member.name)
                if relative.parts[0] not in ('include', 'src', 'tools'):
                    continue
                assert not relative.is_absolute() and '..' not in relative.parts
                target = root / relative
                if member.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                elif member.isfile():
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(source.extractfile(member).read())
        result = subprocess.run(['docker', 'run', '--rm', '--runtime=runc', '--network=none', '--cap-drop=ALL',
                                 '--security-opt=no-new-privileges', '--cpus=4', '--memory=8g',
                                 '--mount', f'type=bind,src={root},dst=/work', IMAGE,
                                 'timeout', '--kill-after=10', '270', 'bash', '-c', COMMAND],
                                capture_output=True, timeout=300)
        out = result.stdout.decode(errors='replace').splitlines()
        messages = result.stderr.decode(errors='replace').splitlines()
        report = dict(revision=REVISION, compile_and_link=result.returncode == 0, exit_code=result.returncode)
        if result.returncode:
            report['errors'] = [line[:240] for line in messages if 'error:' in line.lower()][:6]
        else:
            report['object_sha256'] = out[-1].split()[0]
            report['entries'] = ['tilexr_ht_local_send_timing_sst', 'tilexr_ht_pipeline_sst']
        print(json.dumps(report))
        if result.returncode:
            (Path('/var/tmp') / ('tilexr-delivery-' + REVISION[:7] + '.stderr')).write_bytes(result.stderr)
        return result.returncode
