#!/usr/bin/env bash
set -eo pipefail
cd /work
split=$1
[[ "$split" == 0 || "$split" == 1 ]]
case ${2:-lr8} in
    lr8)
        option=TILEXR_ALLTOALL_LR8_SST_SPLIT_UDMA_WRITES
        target=tilexr_alltoall_urma_lr8_kernel
        elf_log=lr8-elf.log
        ;;
    lr8-queue-state)
        option=TILEXR_ALLTOALL_LR8_SST_MTE_QUEUE_STATE
        target=tilexr_alltoall_urma_lr8_kernel
        elf_log=lr8-elf.log
        ;;
    lr8-cq-gm)
        option=TILEXR_ALLTOALL_LR8_SST_CQ_GM_DOORBELL
        target=tilexr_alltoall_urma_lr8_kernel
        elf_log=lr8-elf.log
        ;;
    clos-4plus8)
        option=TILEXR_ALLTOALL_CLOS_4PLUS8_SST_SPLIT_SQ_FETCH
        target=tilexr_alltoall_urma_full_clos_4plus8_kernel
        elf_log=clos-4plus8-elf.log
        ;;
    urma_cw|urma_lr8_p16_s16_local7|urma_lr8_p16_s8_local7)
        option=TILEXR_ALLTOALL_LR8_SST_SPLIT_UDMA_WRITES
        target="tilexr_alltoall_${2}_kernel"
        elf_log="${2}-elf.log"
        ;;
    *) exit 2 ;;
esac
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
    -D"$option=$split" \
    -DBISHENG_EXECUTABLE="$toolchain/bisheng" -DTILEXR_MOONEP_CCE_LINKER="$toolchain/ld.lld" \
    >receipts/configure.log 2>&1
cmake --build /work/build --target "${target}_sst" --parallel 4 --verbose \
    >receipts/build.log 2>&1
readelf -Whs "/work/build/src/collectives/sst/${target}.o" >"receipts/$elf_log"

