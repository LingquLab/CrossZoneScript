#!/usr/bin/env bash
# Build only the standalone read fixtures. Reuse the matching installed runtimes.
set -euo pipefail
umask 077
cd "$(dirname "$0")"
base=$PWD
exec >>"$base/build.log" 2>&1
exec 9>"$base/build.lock"
flock -n 9
trap 'printf "%s\n" "$?" > "$base/build.exit"' EXIT
rm -f "$base/build.exit"
for branch in stages dispatch sleep; do
    ln -sfn "$base/prior/trees/$branch" "$base/trees/$branch"
done
for variant in off on; do
    for branch in stages sleep; do
        label=$branch-$variant
        test -f "$base/prior/installs/$label/provenance.txt"
        ln -sfn "$base/prior/installs/$label" "$base/installs/$label"
        touch "$base/ready/$label"
    done
done
if [[ ! -d upstream ]]; then
    mkdir -p upstream.part
    tar xf sources.tar -C upstream.part
    mv -T upstream.part upstream
fi
set +u
source /usr/local/Ascend/cann-9.2.T101/set_env.sh
set -u
for mode in off on; do
    label=stages-one-$mode
    [[ ! -e "$base/ready/$label" ]] || continue
    tree="$base/trees/$label"
    source_revision=$(python3 -c 'import json,sys;print(json.load(open("upstream/manifest.json"))["revisions"][sys.argv[1]]["commit"])' "$label")
    if [[ ! -e "$tree/.git" ]]; then
        git --git-dir="$base/upstream/tilexr.git" worktree add --detach "$tree" "$source_revision"
    fi
    [[ $(git -C "$tree" rev-parse HEAD) = "$source_revision" ]]
    for module in picojson spdlog tilelang-ascend; do
        revision=$(git -C "$tree" ls-tree HEAD "3rdparty/$module" | awk '{print $3}')
        module_source="$base/prior/trees/stages/3rdparty/$module"
        [[ $(git -C "$module_source" rev-parse HEAD) = "$revision" ]]
        if [[ ! -e "$tree/3rdparty/$module/.git" ]]; then
            rmdir "$tree/3rdparty/$module"
            ln -s "$module_source" "$tree/3rdparty/$module"
        fi
    done
    runtime="$base/prior/installs/stages-$mode"
    install="$base/installs/$label"
    (
        cmake -S "$tree/tests/ep/ubx_full_intra" -B "$base/builds/$label" \
            -DTILEXR_HOST_PREFIX="$runtime" -DASCEND_HOME_PATH="$ASCEND_HOME_PATH" \
            -DASCEND_DRIVER_PATH=/usr/local/Ascend/driver -DCMAKE_BUILD_TYPE=Release \
            -DCMAKE_INSTALL_LIBDIR=lib -DCMAKE_INSTALL_PREFIX="$install"
        cmake --build "$base/builds/$label" --parallel 8
        cmake --install "$base/builds/$label"
        test -x "$install/bin/ubx_full_intra"
        {
            printf '%s\n' "fixture_revision=$source_revision" "cann=$ASCEND_HOME_PATH" "kernel_profiling=$mode"
            cat "$runtime/provenance.txt"
            sha256sum "$install/bin/ubx_full_intra"
        } > "$install/provenance.txt"
    ) > "$base/build-$label.log" 2>&1
    touch "$base/ready/$label"
done
