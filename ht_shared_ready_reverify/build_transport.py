import pathlib, subprocess,hashlib
from pathlib import Path

def build(root,cann,name,defines):
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
    sender = root/'src/ep/combine_ht'/name
    rel, elf = root/'rel.o', root/'kernel.o'
    subprocess.run([toolchain/'ccec', '-xcce', '-DCATLASS_ARCH=3510', *('-I'+str(p) for p in paths[:10]),
                    *options, *('-I'+str(p) for p in paths[10:]), *defines,
                    '-c', sender, '-o', rel], check=True)
    subprocess.run([toolchain/'ld.lld', '-m', 'aicorelinux', '-Ttext=0', rel, '--static',
                    '-o', elf, '--allow-multiple-definition'], check=True)
    return elf.read_bytes().ljust(10485760, b'\0')

if __name__ == '__main__':
    import sys
    root, cann = map(Path, sys.argv[1:3])
    for name in ('inter_server_send_kernel.cpp','local_return_kernel.cpp'):
        flags=['-DTILEXR_EP_HT_SST_SPLIT_WRITES=1'] if name.startswith('inter_') else []
        data=build(root,cann,name,flags)
        print(name,hashlib.sha256(data).hexdigest())
        (root/(name+'.o')).write_bytes(data)
