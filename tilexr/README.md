# TileXR retained-build deployment

`deploy_verified_build.py` is the green Windows entry point. It reads the JSON file
provided by Bridge's `--args-file`, selects one host in a task-local tssh inventory, and streams the retained
remote Python helper through Bash. Required arguments are `host`, `revision`,
`pr`, `session`, `source_archive_sha256`, and `expected_artifacts` (build-relative
paths mapped to SHA256). `green_code_root` defaults to the operator's established
`D:\works\code\ai\crosszone` directory. SSH uses the existing `npass_id_rsa` file
without reading or returning its contents.

The Linux helper requires a previously successful core and CCU build under
`~/works/code/ai/crosszone/TileXR-pr<pr>-<session-prefix>-<revision-prefix>`.
It verifies the retained provenance and all five explicitly pinned artifact
hashes. It installs the core library and public headers, CCU probe binaries,
separate AIV Host library and kernel into
`~/works/code/ai/crosszone/TileXR-deploy/releases/pr<pr>-<revision-prefix>`.
The release includes `set_env.sh`, `REVISION`, and a complete file hash manifest.

After ELF, dependency and environment checks pass, the helper atomically replaces
`TileXR-deploy/current`. Previous releases remain available. An existing release
is reused only after its manifest and pinned artifact hashes pass verification;
conflicting contents stop deployment. A failed staging directory is retained for
inspection. The helper uses a nonblocking deployment lock and does not replace a
regular file/directory or a link pointing outside its own release parent.

No TileXR program, NPU test, kernel launch, system service, global library
configuration or shell startup file is executed or modified. Dynamic validation
uses `readelf` and `ldd -r`. Detailed output and the result are saved in the green
session's `deploy-pr<pr>-<host>-<revision-prefix>` evidence directory. If dispatch
fails or its effects are unknown, inspect those saved files and the remote
`last-deployment.json`/current link before considering another invocation.

For an operator-requested move, pass `operation: "relocate"` and
`destination_parent: "/home/c00996840/ai"` with the original pinned deployment arguments.
Set `attempt` to the next positive integer when a verified failure needs another
attempt, preserving the previous attempt's evidence. The helper copies
only the verified deployment tree, preserves its release manifest and relative
current link, changes copied entries to the existing destination directory's
numeric UID/GID, and validates environment and dynamic linking with that identity before removing the exact old
deployment tree. An existing destination or unrelated entries stop the move.
The retained source and CMake build directories are recorded as provenance and
are not moved, since their generated build files contain absolute paths.

A directory name does not establish a system account. A failed attempt found
`/home/c00996840/ai` present while `pwd.getpwnam("c00996840")` was absent; requiring
that lookup stopped relocation before any file changes. Use the existing parent
directory's numeric ownership, including when the UID has no passwd entry; do
not create accounts or infer ownership from the path spelling.

## Authorized AIV-to-CCU point-to-point retest

`run_aiv_p2p.py` uses the session's retained SSH setup for 141.61.73.107 and
streams `run_aiv_p2p_remote.py`. Pass `host`, `revision`, `devices: [6, 7]`, a
unique `run_id`, `runner_sha256`, `guard_sha256`, and release-relative
`artifact_hashes`. It runs the PR's canonical two-rank AIV fanout runner with
rank 0 on NPU 6 and rank 1 on NPU 7, reusing verified deployed binaries.
Four loop requests send 128, 256, 384, and 512 bytes from rank 0 to rank 1.

The helper checks selected-device occupancy immediately before launching and
rejects busy or indeterminate devices. `allowed_health` defaults to OK/Alarm.
For the requested short retest on the already selected devices, it can also
contain Warning; the helper records each actual health value and uses the
runner's task-local health override only after checking the allowed values.
Fault and unrecognized states cannot be allowed. Each rank has a 90-second deadline;
the enclosing runner has a 135-second deadline and only its own process group
is terminated on timeout. Result assertions cover both device mappings,
payload correctness, four completed rounds, exactly one sender-side Host/CCU/AIV
launch, synchronization results, and final device occupancy. Logs stay under
`/home/c00996840/ai/TileXR-test/<run_id>` and the matching green session directory.
Do not replay a failed or interrupted hardware run without inspecting those
records and device state. This is a correctness smoke test, not a bandwidth claim.

## Project-local rootinfo and prepare diagnostics

`debug_prepare.py` reads the retained prepare failure and invokes the installed
official `mindcluster-tools rootinfo --output` generator in a new directory under
`/home/c00996840/ai/TileXR-deploy/config/<run_id>`. It does not use the system
rootinfo as an input or seed. It copies the generator's referenced topology into
the same project directory, rewrites that reference, verifies that devices 6 and
7 have endpoints and a topology edge, and saves configuration hashes. Generated
addresses, raw topology and generator output remain on green. If the generator
is unavailable, the diagnostic explicitly reports `rootinfo_prepared: false`;
there is no fallback to `/etc/hccl_rootinfo.json`.

The hardware helper now requires `rootinfo`, `rootinfo_sha256`, and
`topology_sha256`. It verifies both files are inside the project configuration
directory and sets the CCU/UDMA overrides after clearing inherited test variables.
An environment-cleaning wrapper otherwise discards a shell's rootinfo override
and silently restores the runtime's system default. Configuration preparation
does not run an NPU workload or prove communication correctness.
