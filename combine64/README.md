# combine_ht EP64 simulator validation

Session `de038086-2b21-40e2-bad9-65360186ad97`. Candidate
`b5383cfd65828a165fae9957d287967136c06260` on
`LingquLab/TileXR:codex/combine-ht-e2e-ep16-32-64`.

SIM_SEND=ON, perf model, 64 ranks, H128, topk6, FP32. Route generation
uses 256 experts and seed 20261124. Dispatch and expert computation are assumed
complete. Process bs4,16,32,64,128 in order: independent modules, producer
pipeline followed by a separate final launch, then a single pipeline with
dedicated final cores. Stop on the first failure, diagnose, fix in blue and
retest before advancing. R=8, F=8; joint block dimensions are 32 and 40.

`bootstrap_green.py` fetches the exact candidate through Git and creates this
session's own CPU-only network-isolated container. It builds the five production
objects with CANN 9.1.0. It does not start SST or reuse another task's container.
Invoke through commit-pinned Git-session EXEC with args `{"action":"build"}`.

References:
- [Prior bs32 validation](https://github.com/LingquLab/CrossZoneScript/blob/bfd1ad538cb77ea9ac33f99849a02e616f4fe682/combine64/README.md)
- [Prior joint runner](https://github.com/LingquLab/TileXR/blob/114668b7d5f01c61a5976b9ff91038b6059b4d6e/tools/unitest/simulator/experiments/inter_server_send/sst/README.md)

The current 456-byte joint ABI replaces the prior 512-byte ABI. Prior results
do not establish a pass for this candidate. Keep all baseline failures and fixes
with their candidate commits, object hashes and input identities.

`sim_green.py` accepts `action`, `tokens`, `attempt` and the SST wall-clock
`timeout` in seconds. Run `local-send`, `gateway-reduce`, `local-return`,
`inter-server-send`, and `final-independent` in order. Then run
`joint-producer` and `final-from-producer` using the producer's directory name
as `source_case`. Run `joint-final` last. Each attempt gets a distinct work
directory and checks all 64 ranks before returning success.

The fixture loads the initial workspace that production Prepare creates. SST
checks the compute launches and requested numerical outputs. Host stream
ordering and the post-round reuse fence are outside these launches.
