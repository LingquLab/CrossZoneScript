"""Build the test-only sender in an isolated source copy using CANN 9.1."""
import argparse
import gzip
import hashlib
from pathlib import Path
import subprocess
import tarfile
import tempfile

from phases import REVISION


def build(checkout, root, cann):
    with subprocess.Popen(['git', 'archive', REVISION], cwd=checkout, stdout=subprocess.PIPE) as proc:
        with tarfile.open(fileobj=proc.stdout, mode='r|') as archive:
            archive.extractall(root, filter='data')
        if proc.wait() != 0:
            raise RuntimeError('Could not extract the fixed TileXR source')
    here = Path(__file__).resolve().parent
    subprocess.run(['git', 'apply', '--unidiff-zero', here/'sender-sst.patch'], cwd=root, check=True)
    platform = cann / 'x86_64-linux'
    toolchain = platform / 'ccec_compiler/bin'
    paths = [root/'include', platform/'pkg_inc', platform/'pkg_inc/profiling',
             platform/'pkg_inc/runtime', platform/'include', platform/'include/experiment',
             platform/'include/experiment/runtime', platform/'include/experiment/msprof',
             root/'3rdparty/spdlog/include', root/'3rdparty']
    for sdk in (cann/'compiler', platform):
        tikcpp = sdk/'tikcpp'
        paths.extend(tikcpp/p for p in ('', 'tikcfw', 'tikcfw/impl', 'tikcfw/interface'))
    paths.extend((platform/'asc/include', platform/'pkg_inc', platform/'pkg_inc/runtime',
                  platform/'runtime/include', platform/'include', Path('/usr/local/Ascend/driver/kernel/inc'),
                  root/'3rdparty', root/'include', root/'src/ep/common', root/'src/ep/kernels'))
    options = ['-O2', '-std=gnu++17', '--cce-aicore-only', '--cce-auto-sync',
               '-Wno-deprecated-declarations', '-mllvm', '-cce-aicore-stack-size=0x8000',
               '-mllvm', '-cce-aicore-function-stack-size=0x8000', '-mllvm',
               '-cce-aicore-record-overflow=true', '-mllvm', '-cce-aicore-addr-transform',
               '-mllvm', '-cce-aicore-dcci-insert-for-scalar=false', '--cce-aicore-arch=dav-c310-vec']
    sender = root/'src/ep/combine_ht/inter_server_send_kernel.cpp'
    rel, elf = root/'rel.o', root/'kernel.o'
    subprocess.run([toolchain/'ccec', '-xcce', '-DCATLASS_ARCH=3510', *('-I'+str(p) for p in paths[:10]),
                    *options, *('-I'+str(p) for p in paths[10:]), '-DTILEXR_EP_HT_SST_SPLIT_WRITES=1',
                    '-c', sender, '-o', rel], check=True)
    subprocess.run([toolchain/'ld.lld', '-m', 'aicorelinux', '-Ttext=0', rel, '--static',
                    '-o', elf, '--allow-multiple-definition'], check=True)
    return elf.read_bytes().ljust(10485760, b'\0')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('tilexr', type=Path)
    parser.add_argument('cann', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory() as tmp:
        image = build(args.tilexr.resolve(), Path(tmp).resolve(), args.cann.resolve())
    with args.output.open('xb') as artifact:
        artifact.write(gzip.compress(image, mtime=0))
    print(hashlib.sha256(image).hexdigest())


if __name__ == '__main__':
    main()
