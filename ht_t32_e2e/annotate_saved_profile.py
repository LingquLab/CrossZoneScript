"""Add the StageId mapping to the previously verified Windows Perfetto export."""
import hashlib
import json
import shutil
from pathlib import Path

from profile_stage_report import annotate, write_report

case = Path("D:/project/tilexr-inter-server-sst/t4-combine-fp32-ea1ca889-profile")
trace = case / "perfetto_trace.json"
summary = case / "profile-summary.json"
assert hashlib.sha256(trace.read_bytes()).hexdigest() == "1203d70b36eb18cba27187f7359ea2e9fb558329984a1829f6c94ca948fe1f70"
assert hashlib.sha256(summary.read_bytes()).hexdigest() == "a2046860c9a68c03e291b589fa88d92cc26500d1f248cdb3acfce22ad51d8a32"
annotated = case / "perfetto_trace.with-markers.json"
report = case / "stage-report.md"
source = case / "combine_sst_fp32_profile.cpp"
shutil.copyfile(Path(__file__).with_name(source.name), source)
stats = annotate(trace, annotated)
write_report(json.loads(summary.read_bytes()), report)
stats.update(annotated=str(annotated), report=str(report), source=str(source),
             trace_sha256=hashlib.sha256(annotated.read_bytes()).hexdigest())
print(json.dumps(stats, ensure_ascii=True, separators=(",", ":")))
