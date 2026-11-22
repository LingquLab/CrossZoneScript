#!/usr/bin/env bash
# Build pinned TileXR fixtures and save separate installs for kernel profiling off/on.
set -euo pipefail
umask 077
cd "$(dirname "$0")"
base=$PWD
exec >> "$base/build.log" 2>&1
exec 9>"$base/build.lock"
flock -n 9
trap 'rc=$?; printf "%s\n" "$rc" > "$base/build.exit"' EXIT
rm -f "$base/build.exit"
deadline=$((SECONDS + 900))
until [[ -s sources.tar ]]; do
    (( SECONDS < deadline )) || exit 3
    sleep 5
done
if [[ ! -d upstream ]]; then
    mkdir -p upstream.part
    tar xf sources.tar -C upstream.part
    mv -T upstream.part upstream
fi
source /usr/local/Ascend/cann-9.2.T101/set_env.sh
export ASCEND_DRIVER_PATH=/usr/local/Ascend/driver
mkdir -p trees builds installs ready
for branch in stages dispatch sleep; do
    expected=$(python3 -c 'import json,sys; print(json.load(open("upstream/manifest.json"))["revisions"][sys.argv[1]]["commit"])' "$branch")
    [[ $(git --git-dir="$base/upstream/tilexr.git" rev-parse "refs/heads/$branch") = "$expected" ]]
    tree="$base/trees/$branch"
    if [[ ! -e "$tree/.git" ]]; then
        git --git-dir="$base/upstream/tilexr.git" worktree add --detach "$tree" "$expected"
    fi
    [[ $(git -C "$tree" rev-parse HEAD) = "$expected" ]]
    for module in spdlog picojson tilelang-ascend; do
        revision=$(git -C "$tree" ls-tree HEAD "3rdparty/$module" | awk '{print $3}')
        [[ $(git --git-dir="$base/upstream/$module.git" rev-parse refs/heads/pinned) = "$revision" ]]
        if [[ ! -e "$tree/3rdparty/$module/.git" ]]; then
            git --git-dir="$base/upstream/$module.git" worktree add --detach "$tree/3rdparty/$module" "$revision"
        fi
        [[ $(git -C "$tree/3rdparty/$module" rev-parse HEAD) = "$revision" ]]
    done
done

# The native task profiler runs for both builds. The profiling switch controls
# the kernel's own instrumentation and captures.
for config in stages-off dispatch-off dispatch-on sleep-off; do
    [[ ! -e "$base/ready/$config" ]] || continue
    branch=${config%-*}
    mode=${config##*-}
    profiling=${mode^^}
    tree="$base/trees/$branch"
    build="$base/builds/$config"
    install="$base/installs/$config"
    (
        set +u
        source "$tree/scripts/common_env.sh"
        set -u
        cmake -S "$tree" -B "$build/runtime" -G Ninja -DBUILD_TESTING=OFF \
            -DTILEXR_BUILD_UNITEST=OFF -DTILEXR_BUILD_EP=ON -DTILEXR_BUILD_DIAGNOSTICS=ON \
            -DTILEXR_EP_ENABLE_PROFILING="$profiling" -DTILEXR_EP_UBX_PACKET_TRACE=OFF \
            -DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_LIBDIR=lib -DCMAKE_INSTALL_PREFIX="$install"
        cmake --build "$build/runtime" --parallel 8
        cmake --install "$build/runtime"
        fixture=ubx_full_intra
        [[ "$branch" != sleep ]] || fixture=ubx_combine_write
        cmake -S "$tree/tests/ep/$fixture" -B "$build/fixture" -G Ninja \
            -DTILEXR_HOST_PREFIX="$install" -DASCEND_HOME_PATH="$ASCEND_HOME_PATH" \
            -DASCEND_DRIVER_PATH="$ASCEND_DRIVER_PATH" -DCMAKE_BUILD_TYPE=Release \
            -DCMAKE_INSTALL_LIBDIR=lib -DCMAKE_INSTALL_PREFIX="$install"
        cmake --build "$build/fixture" --parallel 8
        cmake --install "$build/fixture"
        test -x "$install/bin/$fixture"
        {
            printf '%s\n' "revision=$(git -C "$tree" rev-parse HEAD)" "cann=$ASCEND_HOME_PATH" "kernel_profiling=$mode"
            find "$install/lib" -type f \( -name '*.so*' -o -path '*/tilexr/kernels/*ubx*' -o -path '*/tilexr/kernels/*clock*' \) -print0 | sort -z | xargs -0 sha256sum
            sha256sum "$install/bin/$fixture"
        } > "$install/provenance.txt"
        touch "$base/ready/$config"
    ) > "$base/build-$config.log" 2>&1
done
