#!/usr/bin/env bash
set -e -o pipefail
bind=$1
name=$2
image=$3
shift 3
cd "$bind"
test "$(docker image inspect --format '{{.Id}} {{.Architecture}}' "$image")" = "$image arm64"
id=$(docker run -dt --name "$name" --runtime=ascend --network none --ipc=shareable --shm-size=4g \
    --mount "type=bind,src=$bind,dst=/work" "$image" bash -l)
printf '%s\n' "$id" > container-id.txt
docker exec -i "$id" bash -s -- "$@" <<'BUILD' > build-control.log 2>&1
set -eo pipefail
cd /work
mkdir receipts
source /opt/sim/cann-env.sh
export ASCEND_HOME_PATH=/opt/sim/Ascend/cann-9.1.0 ASCEND_DRIVER_PATH=/usr/local/Ascend/driver TILEXR_SOC_NAME=Ascend950
toolchain="$ASCEND_HOME_PATH/tools/bisheng_compiler/bin"
cmake -S /work/source -B /work/build -DTILEXR_BUILD_COLLECTIVES=ON \
  -DTILEXR_BUILD_UNITEST=OFF -DTILEXR_BUILD_EP=OFF -DBUILD_TESTING=OFF \
  -DTILEXR_DIAGNOSTICS_SOC_TYPE=Ascend950 -DTILEXR_COLLECTIVES_SOC_TYPE=Ascend950 \
  -DTILEXR_COLLECTIVES_ENABLE_PROFILING=OFF -DBISHENG_EXECUTABLE="$toolchain/bisheng" \
  -DTILEXR_MOONEP_CCE_LINKER="$toolchain/ld.lld" >receipts/configure.log 2>&1
cmake --build /work/build --target "$@" --parallel 4 --verbose >receipts/build.log 2>&1
BUILD
