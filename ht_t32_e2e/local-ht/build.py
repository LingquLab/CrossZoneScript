from pathlib import Path
import subprocess,shlex
root=Path('/data/dhp/TileXR-combine-ht-prefill-20261124')
out=Path(__file__).resolve().parent
src=root/'src/moonep/combine_ht'
flags_file=Path('/tmp/tilexr-final-reduce-build/src/ep/CMakeFiles/tilexr_ep_ht_inter_server_send_kernel_tmp.dir/flags.make')
lines=flags_file.read_text().splitlines()
compiler=Path(next(line.split('with ',1)[1] for line in lines if line.startswith('# compile CCE with ')))
flags=[arg for line in lines if line.startswith(('CCE_DEFINES =','CCE_INCLUDES =','CCE_FLAGS =')) for arg in shlex.split(line.split('=',1)[1])]
flags=[str(root)+arg.split('/data/dhp/TileXR-combine-ht-final-reduce',1)[1] if arg.startswith('/data/dhp/TileXR-combine-ht-final-reduce/') else arg.replace('-I/data/dhp/TileXR-combine-ht-final-reduce/','-I'+str(root)+'/') for arg in flags]
unit=out/'unlinked.o'
artifact=out/'inter_server_send.o'
subprocess.run([compiler, '-I'+str(root/'include'), '-I'+str(src/'common'), '-xcce', *flags, '-DTILEXR_COMBINE_HT_SIM_SEND=1','-DTILEXR_COMBINE_HT_SST_FP32=1','-c',src/'kernels/combine_ht.cpp','-o',unit],check=True)
subprocess.run([compiler.with_name('ld.lld'),'-m','aicorelinux','-Ttext=0',unit,'--static','-o',artifact,'--allow-multiple-definition'],check=True)
with artifact.open('r+b') as stream:
    stream.truncate(10485760)
