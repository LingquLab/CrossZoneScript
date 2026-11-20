# Native inter-server binary handoff

1. Blue freezes the TileXR source commit and builds the AICore object locally. Preserve compiler/version/options and build logs in a build record; keep native `ld_dev/st_dev` for the first candidate.
2. Blue uses the owned TileXR `sst/artifact.py pack` entry to write `artifact.json`, `build-record.json` and `inter_server_send.o.gz`. The object must define `tilexr_ep_ht_inter_server_send_kernel`. Both compressed and uncompressed hashes are fixed.
3. Copy that package into `inter_server/resources/native_binary/` on this session branch. Copy the reviewed native adapter into the same Git snapshot as needed. Commit/push and dispatch the exact Git-session commit, never another session's branch.
4. `inter_server/stage_binary.py` verifies/unpacks the object locally on green, transfers the compressed package over the existing SSH route, and verifies/unpacks it again in the mounted task directory. This entry neither compiles nor starts SST.
5. Prepare a fresh native case with 64 ranks, 16 AIV, registered workspace/QP images, magic ready words and guard exports. Pin model/config/fixture/checker files and the exact blue object in `case-manifest.json`.
6. Green invokes native adapter `sst/run.py` in the CPU-only runc container. It launches only SST, validates guards, reconstructs returned/completed/status, and calls the migrated fixture's `check` action. It never invokes fixture `prepare`, a compiler or a build system. Preserve run.json and all raw dumps.

No binary package or runtime case has been published yet. Do not dispatch the stage/run entries until the candidate, blue object and case resources are frozen. A separate MTE compatibility object, if needed later, must have a separate build record and object hash; do not modify production queue APIs or relabel it as the native candidate.
