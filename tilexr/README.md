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
streams `run_aiv_p2p_remote.py`. Pass `host`, `revision`, `devices: [2, 3]`, a
unique `run_id`, `runner_sha256`, `guard_sha256`, and release-relative
`artifact_hashes`. It runs the PR's canonical two-rank AIV fanout runner with
rank 0 on NPU 2 and rank 1 on NPU 3, reusing verified package binaries. The
operator changed the selected devices after the original 6/7 run. An optional
`release` selects a separately built candidate package without activating it.
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

## Candidate preparation fix

`check_aiv_prepare.py --repo <path>` compiles the real preparation components and
`aiv_prepare_regression.cpp` into a temporary CPU executable. It excludes unused
runtime-submit entry points at link time and checks one/three destination plans
for sender preparation and participation-only preparation. The original PR
fails with zero remote XNs; the candidate allocates distinct channel XNs and
reconciles peer proofs. These are development checks, separate from TileXR's
submitted test surface.

`build_debug.py` takes the pinned candidate `revision`, `devices: [2, 3]`, unique
`run_id`, host, and existing project-local rootinfo path/hashes. Green fetches the
exact commit into a worktree under its code root, packages that tree with the
already available spdlog/picojson dependencies, and streams it over the retained
SSH setup. `build_debug_remote.py` builds and installs the core in a new
`/home/c00996840/ai/TileXR-pr229-debug-<revision-prefix>` directory. Its separate
package rebuilds the Host smoke probe from the candidate source and reuses AIV
artifacts only after their kernel, launcher, loader and local header sources match
the retained baseline. It records source and binary hashes, runs the Host
preparation check against the new core, and checks ELF dependencies and device occupancy.
It does not activate the package or launch an NPU workload. Inspect saved build
evidence before any retry after failure or interruption.

`inspect_debug_build.py` reads that saved result and bounded compiler diagnostics
for a pinned `revision` and build `run_id`. It does not rebuild or launch a workload.

If packaging stopped because `ASCEND_DRIVER_PATH` was unset after the core was
built, `build_debug.py` accepts `resume_from_run` naming that inspected run. It
checks the original request/result, all retained source hashes and the matching
built/installed/packaged core copies, preserves the failed result, and resumes
probe compilation and validation. The driver path uses the smoke runner's default
`/usr/local/Ascend/driver` when the environment does not provide one.

GNUInstallDirs may default to `lib64` while the retained package loads `lib`.
Copy the exact built core to the package's selected path, compare hashes, and
verify the Host check's `ldd` resolution before interpreting a regression result.
`resume_debug.py` resumes only the inspected successful build whose packaging
selected the old library. It checks retained source/library hashes, preserves the
failed check, finishes package validation, and runs the requested NPU 2/3 smoke
once with the project-local rootinfo. It does not rebuild the completed core.

## One-time CCU environment qualification

After searching retained matching records, `hccl_baseline.py` can run the installed
official HCCL Test once on NPU 2/3 in `ccu_ms` mode. It uses existing MPICH and
128/256/384/512-byte cases with result checks. A private mount namespace binds the
project rootinfo over HCCL's fixed path for this child process tree only. It
checks that both paths identify the same inode and mounts that view read-only;
the system file and global mounts remain unchanged. The referenced topology
stays inside the project configuration directory. No system rootinfo is copied
or used as input. The script retains binary hashes, environment metadata,
command, correctness lines and final selected-device occupancy. Do not rerun a
completed matching qualification; a failed platform baseline stops TileXR
data-plane debugging until that environment is repaired or replaced.

The installed HCCL Test parses `HCCL_TEST_USE_DEVS` before calling
`aclrtSetDevice(dev_id)`. Set it to `2,3`; a framework's visible-device variable
alone does not establish the physical devices selected by this native binary.

CANN 9.1 rejects `HCCL_CONNECT_TIMEOUT=60` before `HcclGetRootInfo` can complete;
its accepted range starts at 120 seconds. Use 120 for the HCCL connect/execute
settings and retain a separate outer process deadline. A rejected setup with no
communication submitted is not a failed communication qualification; preserve
its logs, correct only that setup, and complete the intended qualification.

After the matching CCU qualification succeeds, reuse it. A TileXR smoke may set
`trace: true` to collect endpoint/plan trace and after-AIV state in a fresh work
directory. Returned summaries omit endpoint identities, addresses and token
values; complete logs remain green-local. Keep trace runs distinct from the
correctness/performance baseline.

`inspect_aiv_trace.py` reads an existing run by `run_id` on the selected host.
It returns numeric endpoint, PFE, jetty, channel and first-transfer fields plus
bounded plog error codes. It never reruns a workload, and omits endpoint IDs,
addresses, handles and tokens from returned fields. Check the actual two-rank
route path: the peer-specific path is normally selected only above two ranks,
unless the explicit peer-endpoint diagnostic switch is enabled.

For a two-rank route comparison, `run_aiv_p2p.py` accepts the explicit boolean
`force_peer_endpoints`. It sets the existing runtime diagnostic switch after
clearing inherited variables. This exercises the peer-specific topology path
without changing the candidate binary; keep its result distinct from the default
two-rank loop/RTP path and require full payload checks before calling it a fix.

For completion debugging, `single_round: true` retains only the first 128-byte
request. The normal four-round sequence later overwrites its destination, so a
final mismatch count alone cannot establish whether that first write arrived.
The single-round run checks the full destination buffer against that one write;
overall success still requires sender completion, synchronization and launch checks.

An explicit `install_die` of 0 or 1 selects the existing runtime install-die
override for a bounded comparison. Record it alongside the endpoint's runtime
die, channel/jetty I/O die and CQE cross-die setting. A stream timeout alone
does not establish which die field is wrong; compare an unchanged candidate
with matching endpoint and mission placement before changing the implementation.
