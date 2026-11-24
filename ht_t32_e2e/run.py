"""Transfer the pinned T32 fused FP32 workload when the green platform recovers."""
import base64
import gzip
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
from contextlib import nullcontext

from green_auth import askpass_environment

root = Path(__file__).resolve().parent
args = json.loads(Path(sys.argv[2]).read_text()) if sys.argv[1:2] == ["--args-file"] else {}
object_name = "inter_server_send.o.gz"
identity_name = "fixture-identity.json"
fixture_name = "fixture-images.tar.gz"
profile_t16 = args.get("t16_b512_profile") is True
profile = args.get("fused_profile") is True or profile_t16
if profile_t16:
    identity_name = "t16-b512-profile-identity.json"
    fixture_name = "t16-b512-fixture-images.tar.gz"
    object_name = "t16-b512-profile.o.gz"
elif profile:
    identity_name = "t4-profile-identity.json"
    fixture_name = "t4-fixture-images.tar.gz"
    object_name = "t4-profile.o.gz"
elif args.get("sq_stripe") is True:
    object_name = "sq-stripe-a7286b7a.o.gz"
    identity_name = "sq-stripe-identity.json"
elif args.get("small_t4") is True:
    identity_name = "t4-identity.json"
    fixture_name = "t4-fixture-images.tar.gz"
elif args.get("t16_b512") is True:
    identity_name = "t16-b512-identity.json"
    fixture_name = "t16-b512-fixture-images.tar.gz"
    object_name = "t16-b512.o.gz"
elif args.get("spread_gateways") is True:
    identity_name = "t32-spread-identity.json"
    fixture_name = "t32-spread-fixture-delta.tar.gz"
elif args.get("t32_peer_first") is True:
    identity_name = "t32-peer-first-identity.json"
    fixture_name = "t32-peer-first-fixture-images.tar.gz"
    object_name = "t32-peer-first.o.gz"
elif args.get("local_ht") is True:
    identity_name = "local-ht/fixture-identity.json"
    fixture_name = "local-ht/fixture-images.tar.gz"
    object_name = "local-ht/inter_server_send.o.gz"
    if args.get("local_ht_release") is True:
        identity_name = "local-ht/release-identity.json"
        object_name = "local-ht/release.o.gz"
    elif args.get("local_ht_aligned_forward") is True:
        identity_name = "local-ht/aligned-forward-identity.json"
        object_name = "local-ht/aligned-forward.o.gz"
    elif args.get("local_ht_cq_gate") is True:
        identity_name = "local-ht/cq-gate-identity.json"
        object_name = "local-ht/cq-gate.o.gz"
    elif args.get("bounded_local_ht") is True:
        identity_name = "local-ht/bounded-identity.json"
        object_name = "local-ht/bounded.o.gz"
identity = json.loads((root / identity_name).read_text())
fixture = (root / fixture_name).read_bytes()
object_package = (root / object_name).read_bytes()
delta = args.get("spread_gateways") is True
digest_field = "delta_archive_sha256" if delta else "input_archive_sha256"
assert hashlib.sha256(fixture).hexdigest() == identity[digest_field]
assert hashlib.sha256(gzip.decompress(object_package)).hexdigest() == identity["kernel_sha256"]
entry = identity["entry"]
assert entry == ("tilexr_moonep_combine_ht_kernel" if args.get("local_ht") else "tilexr_ep_ht_combine_sst_fp32")
build = dict(entry=entry, variant="sst-mte-fused-fp32", candidate_commit=identity["commit"],
             kernel_sha256=identity["kernel_sha256"], compiled_in="blue")
checker = root / ("local-ht/check.py" if args.get("local_ht") else "check.py")
request = dict(build=build, identity=identity, archive=base64.b64encode(fixture).decode(),
               object=base64.b64encode(object_package).decode(), checker=checker.read_text(),
               exports=(root/"exports.py").read_text(), spread_gateways=delta,
               bounded_local_ht=bool(args.get("bounded_local_ht") or args.get("local_ht_cq_gate") or args.get("local_ht_aligned_forward")))
if delta:
    manifest = (root / "t32-spread-delta-manifest.json").read_bytes()
    assert hashlib.sha256(manifest).hexdigest() == identity["delta_manifest_sha256"]
    request["delta_manifest"] = base64.b64encode(manifest).decode()
profile_remote = ""
if profile:
    request["profile"] = (root / "fused_profile.py").read_text()
    profile_remote = (root / "fused_profile_remote.py").read_text()
runner = (root.parent/"inter_server/run_mte_t4_remote.py").read_text().rsplit("if __name__ == ", 1)[0]
assert runner.count("assert tokens == 4") == 1
runner = runner.replace("assert tokens == 4", "assert tokens in (4, 16, 32)")
runner = runner.replace('"hidden": 256,', '"hidden": request["hidden"],')
source = "\n".join((runner, profile_remote, (root/"prepare.py").read_text(), (root/"remote.py").read_text(),
                    f"print(json.dumps(run_e2e({request!r}), separators=(',', ':')))" ))
compile(source, "<t32_fused_remote>", "exec")
password_auth = args.get("local_auth_note") is True
ssh = ["ssh", "-T", "-o", "StrictHostKeyChecking=yes", "-o", "ConnectTimeout=8"]
if password_auth:
    ssh.extend(["-o", "BatchMode=no", "-o", "NumberOfPasswordPrompts=1",
                "-o", "PreferredAuthentications=password", "-o", "PubkeyAuthentication=no"])
else:
    ssh.extend(["-o", "BatchMode=yes"])
ssh.extend(["root@141.61.94.58", "python3", "-"])


def run(environment):
    deadline = time.monotonic() + 540
    failures = []
    while True:
        try:
            reply = subprocess.run(ssh, input=source.encode(), env=environment,
                                   capture_output=True, timeout=2200)
        except subprocess.TimeoutExpired:
            return {"transport_error": "command_timed_out", "check_retained_run": True}
        if reply.returncode == 0:
            try:
                return json.loads(reply.stdout)
            except ValueError:
                return {"transport_error": "invalid_receipt", "check_retained_run": True}
        stderr = reply.stderr.decode(errors="replace").lower()
        if reply.returncode == 255 and "permission denied" in stderr:
            return {"transport_error": "authentication_denied"}
        reason = next((phrase for phrase in ("connection refused", "connection timed out") if phrase in stderr), None)
        if reply.returncode == 255 and reason is not None:
            failures.append(reason)
            if time.monotonic() < deadline:
                time.sleep(25)
                continue
            return {"transport_error": "ssh_unavailable",
                    "connect_failures": {name: failures.count(name) for name in set(failures)}}
        classes = re.findall(r"(?m)^([A-Za-z][A-Za-z0-9_.]*(?:Error|Exception|Expired)):",
                             reply.stderr.decode(errors="replace"))
        return {"ssh_exit": reply.returncode, "remote_error_types": classes[-3:],
                "stderr_bytes": len(reply.stderr), "check_retained_run": True}


def fetch_profile(result, environment):
    report = result.get("profile", {})
    if "trace_sha256" not in report:
        return
    path = result["case"].removeprefix("/work/inter-server-native/")
    assert path == f"t{identity['tokens']}-combine-fp32-{identity['kernel_sha256'][:8]}-profile"
    remote = "/mnt/share/tilexr-sim-jobs/b4f4660a-6144-453c-99f7-339b40f56c54/inter-server-native/" + path
    destination = Path("D:/project/tilexr-inter-server-sst") / path
    destination.mkdir(parents=True, exist_ok=True)
    options = ssh[2:-3]
    fields = {"perfetto_trace.json": "trace_sha256", "profile-summary.json": "summary_sha256"}
    for name, field in fields.items():
        pending = destination / (name + ".partial")
        fetch = subprocess.run(["scp", "-q", *options, f"{ssh[-3]}:{remote}/{name}", str(pending)],
                               env=environment, capture_output=True, timeout=150)
        if fetch.returncode or hashlib.sha256(pending.read_bytes()).hexdigest() != report[field]:
            result["profile_copy_error"] = "transfer_or_hash_mismatch"
            return
        pending.replace(destination / name)
    result["green_perfetto_file"] = str(destination / "perfetto_trace.json")
    result["green_profile_summary"] = str(destination / "profile-summary.json")
    if profile_t16:
        from profile_stage_report import annotate, source_stages, write_report

        source = destination / "combine_sst_fp32_profile.cpp"
        shutil.copyfile(root / "t16-b512-profile.cpp", source)
        stages = source_stages(source, wqes_per_row=1)
        annotated = destination / "perfetto_trace.with-markers.json"
        trace_stats = annotate(destination / "perfetto_trace.json", annotated, stages)
        assert trace_stats["slices"] == report["slices"]
        stage_report = destination / "stage-report.md"
        workload = f"EP64 / BS16 / TopK 6 / 256 experts / 每行 512 B，打点对象 `{identity['kernel_sha256'][:8]}`"
        write_report(report, stage_report, stages, workload)
        result["green_perfetto_file"] = str(annotated)
        result["green_stage_report"] = str(stage_report)
        result["green_profile_source"] = str(source)


try:
    with askpass_environment() if password_auth else nullcontext(None) as environment:
        result = run(environment)
        if profile:
            try:
                fetch_profile(result, environment)
            except (OSError, subprocess.TimeoutExpired) as error:
                result["profile_copy_error"] = type(error).__name__
except FileNotFoundError:
    result = {"transport_error": "configured_local_auth_note_not_found"}
print(json.dumps(result, separators=(",", ":")))
