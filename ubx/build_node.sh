#!/usr/bin/env bash
# Build pinned TileXR fixtures and save separate installs for kernel profiling off/on.
set -euo pipefail
umask 077
cd "$(dirname "$0")"
base=$PWD
kind=${1:-base}
branches=(stages dispatch sleep)
configs=(stages-off dispatch-off dispatch-on sleep-off)
tree_suffix=
if [[ "$kind" = profile ]]; then
    base=${PWD%-profile}
    branches=(stages sleep)
    configs=(stages-on sleep-on)
    tree_suffix=-on
elif [[ "$kind" = dispatch-baseline ]]; then
    branches=(dispatch dispatch-fixture)
    configs=(dispatch-off dispatch-on)
fi
exec >> "$PWD/build.log" 2>&1
exec 9>"$PWD/build.lock"
flock -n 9
trap 'rc=$?; printf "%s\n" "$rc" > "$PWD/build.exit"' EXIT
rm -f "$PWD/build.exit"
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
set +u
source /usr/local/Ascend/cann-9.2.T101/set_env.sh
set -u
export ASCEND_DRIVER_PATH=/usr/local/Ascend/driver
mkdir -p "$base"/{trees,builds,installs,ready}
for branch in "${branches[@]}"; do
    expected=$(python3 -c 'import json,sys; print(json.load(open("upstream/manifest.json"))["revisions"][sys.argv[1]]["commit"])' "$branch")
    [[ $(git --git-dir="$PWD/upstream/tilexr.git" rev-parse "refs/heads/$branch") = "$expected" ]]
    tree="$base/trees/$branch$tree_suffix"
    if [[ ! -e "$tree/.git" ]]; then
        git --git-dir="$PWD/upstream/tilexr.git" worktree add --detach "$tree" "$expected"
    fi
    [[ $(git -C "$tree" rev-parse HEAD) = "$expected" ]]
    for module in spdlog picojson tilelang-ascend; do
        revision=$(git -C "$tree" ls-tree HEAD "3rdparty/$module" | awk '{print $3}')
        [[ $(git --git-dir="$PWD/upstream/$module.git" rev-parse refs/heads/pinned) = "$revision" ]]
        if [[ ! -e "$tree/3rdparty/$module/.git" ]]; then
            git --git-dir="$PWD/upstream/$module.git" worktree add --detach "$tree/3rdparty/$module" "$revision"
        fi
        [[ $(git -C "$tree/3rdparty/$module" rev-parse HEAD) = "$revision" ]]
    done
done

# The native task profiler runs for both builds. The profiling switch controls
# the kernel's own instrumentation and captures.
for config in "${configs[@]}"; do
    [[ ! -e "$base/ready/$config" ]] || continue
    branch=${config%-*}
    mode=${config##*-}
    profiling=${mode^^}
    tree="$base/trees/$branch$tree_suffix"
    build="$base/builds/$config"
    install="$base/installs/$config"
    (
        set +u
        source "$tree/scripts/common_env.sh"
        set -u
        if command -v ninja >/dev/null; then
            generator=Ninja
            generator_tag=ninja
        else
            generator='Unix Makefiles'
            generator_tag=make
        fi
        runtime="$build/runtime-$generator_tag"
        fixture_build="$build/fixture-$generator_tag"
        cmake -S "$tree" -B "$runtime" -G "$generator" -DBUILD_TESTING=OFF \
            -DTILEXR_BUILD_UNITEST=OFF -DTILEXR_BUILD_EP=ON -DTILEXR_BUILD_DIAGNOSTICS=ON \
            -DTILEXR_EP_ENABLE_PROFILING="$profiling" -DTILEXR_EP_UBX_PACKET_TRACE=OFF \
            -DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_LIBDIR=lib -DCMAKE_INSTALL_PREFIX="$install"
        cmake --build "$runtime" --parallel 8
        cmake --install "$runtime"
        fixture=ubx_full_intra
        [[ "$branch" != sleep ]] || fixture=ubx_combine_write
        fixture_tree="$tree"
        if [[ "$kind" = dispatch-baseline ]]; then fixture_tree="$base/trees/dispatch-fixture"; fi
        cmake -S "$fixture_tree/tests/ep/$fixture" -B "$fixture_build" -G "$generator" \
            -DTILEXR_HOST_PREFIX="$install" -DASCEND_HOME_PATH="$ASCEND_HOME_PATH" \
            -DASCEND_DRIVER_PATH="$ASCEND_DRIVER_PATH" -DCMAKE_BUILD_TYPE=Release \
            -DCMAKE_INSTALL_LIBDIR=lib -DCMAKE_INSTALL_PREFIX="$install"
        cmake --build "$fixture_build" --parallel 8
        cmake --install "$fixture_build"
        test -x "$install/bin/$fixture"
        {
            printf '%s\n' "revision=$(git -C "$tree" rev-parse HEAD)" "cann=$ASCEND_HOME_PATH" "kernel_profiling=$mode"
            find "$install/lib" -type f \( -name '*.so*' -o -path '*/tilexr/kernels/*ubx*' -o -path '*/tilexr/kernels/*clock*' \) -print0 | sort -z | xargs -0 sha256sum
            printf '%s\n' "fixture_revision=$(git -C "$fixture_tree" rev-parse HEAD)"
            sha256sum "$install/bin/$fixture"
        } > "$install/provenance.txt"
        if [[ "$kind" = dispatch-baseline ]]; then
            ln -sfn "$install" "$base/installs/dispatch-one-$mode"
            touch "$base/ready/dispatch-one-$mode"
        fi
        touch "$base/ready/$config"
    ) > "$base/build-$config.log" 2>&1
done
