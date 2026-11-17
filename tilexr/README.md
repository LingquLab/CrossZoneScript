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
