#!/usr/bin/env bash
# Build only the failure-audit fixture, linked to the original profiling-off install.
set -eo pipefail
umask 077
cd "$1"
original=$2
revision=$3
exec > build.log 2>&1
trap 'rc=$?; printf "%s\n" "$rc" > build.exit' EXIT
rm -f build.exit
if [[ ! -d upstream ]]; then
    mkdir upstream
    tar xf sources.tar -C upstream
fi
if [[ ! -e tree/.git ]]; then
    git --git-dir="$PWD/upstream/tilexr.git" worktree add --detach "$PWD/tree" "$revision"
fi
[[ $(git -C tree rev-parse HEAD) == "$revision" ]]
for module in spdlog picojson tilelang-ascend; do
    if [[ ! -e "tree/3rdparty/$module/.git" ]]; then
        git --git-dir="$PWD/upstream/$module.git" worktree add --detach "$PWD/tree/3rdparty/$module" refs/heads/pinned
    fi
done
original_package="$original/installs/stages-off"
old_kernel="$original_package/lib/tilexr/kernels/tilexr_ep_ubx_combine_kernel.o"
expected=e6fd5dcfd648c11d96302d4cbf4401c723111313533202eca89cde2845dc44d8
[[ $(sha256sum "$old_kernel" | cut -d' ' -f1) == "$expected" ]]
source /usr/local/Ascend/cann-9.2.T101/set_env.sh
source tree/scripts/common_env.sh
cmake -S tree/tests/ep/ubx_full_intra -B build -G 'Unix Makefiles' \
    -DTILEXR_HOST_PREFIX="$original_package" -DASCEND_HOME_PATH="$ASCEND_HOME_PATH" \
    -DASCEND_DRIVER_PATH="$ASCEND_DRIVER_PATH" -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_INSTALL_LIBDIR=lib -DCMAKE_INSTALL_PREFIX="$PWD/install"
cmake --build build --parallel 8
cmake --install build
for lib in "$original_package"/lib/*.so*; do
    ln -sfn "$lib" "install/lib/$(basename "$lib")"
done
for kernel in "$original_package"/lib/tilexr/kernels/*; do
    ln -sfn "$kernel" "install/lib/tilexr/kernels/$(basename "$kernel")"
done
sha256sum "$PWD/install/bin/ubx_full_intra" "$PWD/install/lib/tilexr/kernels/tilexr_ep_ubx_combine_kernel.o"
