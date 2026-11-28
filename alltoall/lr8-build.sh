#!/usr/bin/env bash
set -eo pipefail
cd /work
split=$1
[[ "$split" == 0 || "$split" == 1 ]]
mkdir receipts
source /opt/sim/cann-env.sh
export ASCEND_HOME_PATH=/opt/sim/Ascend/cann-9.1.0
export ASCEND_DRIVER_PATH=/usr/local/Ascend/driver
export TILEXR_SOC_NAME=Ascend950
toolchain="$ASCEND_HOME_PATH/tools/bisheng_compiler/bin"
{
    "$toolchain/bisheng" --version
    "$toolchain/ld.lld" --version
    sha256sum "$toolchain/bisheng" "$toolchain/ld.lld"
} >receipts/compiler.log 2>&1
cmake -S /work/source -B /work/build -DTILEXR_BUILD_COLLECTIVES=ON \
    -DTILEXR_BUILD_UNITEST=OFF -DTILEXR_BUILD_EP=OFF -DBUILD_TESTING=OFF \
    -DTILEXR_DIAGNOSTICS_SOC_TYPE=Ascend950 -DTILEXR_COLLECTIVES_SOC_TYPE=Ascend950 \
    -DTILEXR_COLLECTIVES_ENABLE_PROFILING=OFF \
    -DTILEXR_ALLTOALL_LR8_SST_SPLIT_UDMA_WRITES="$split" \
    -DBISHENG_EXECUTABLE="$toolchain/bisheng" -DTILEXR_MOONEP_CCE_LINKER="$toolchain/ld.lld" \
    >receipts/configure.log 2>&1
cmake --build /work/build --target tilexr_alltoall_urma_lr8_kernel_sst --parallel 4 --verbose \
    >receipts/build.log 2>&1
readelf -Whs /work/build/src/collectives/sst/tilexr_alltoall_urma_lr8_kernel.o >receipts/lr8-elf.log

