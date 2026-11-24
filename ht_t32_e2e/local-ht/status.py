"""Read the retained local HT case only."""
import json
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from green_auth import askpass_environment

args = json.loads(Path(sys.argv[2]).read_text()) if sys.argv[1:2] == ["--args-file"] else {}
case = "/mnt/share/tilexr-sim-jobs/b4f4660a-6144-453c-99f7-339b40f56c54/inter-server-native/t4-local-ht-fp32-9f0f0f9a"
if args.get("poll_mask") is True:
    case = case.replace("9f0f0f9a", "09b138b3")
elif args.get("release") is True:
    case = case.replace("9f0f0f9a", "7832b693")
elif args.get("aligned_forward") is True:
    case = case.replace("9f0f0f9a", "ff82ec9c")
elif args.get("bounded") is True:
    case = case.replace("9f0f0f9a", "604c76ac")
support = Path(__file__).resolve().parents[2] / "inter_server"
common = (support / "owner_trace_remote.py").read_text().split("def doorbell_coordinates(",1)[0]
reader = "def read_observed(" + (support / "diagnose_t96_sq_remote.py").read_text().split(
    "def read_observed(",1)[1].split("def diagnose_t96(",1)[0]
code = common + reader + f"\ncase = {case!r}\n" + r"""
import json,re,time
from pathlib import Path
p=Path(case)
results={"exists":p.is_dir()}
for f in ("run.json","task-result.json","e2e-summary.json"):
    x=p/f
    if x.is_file():
        data=json.loads(x.read_bytes())
        results[f]={k:data[k] for k in ("status","returncode","run","runner_exit_code","runner_wall_seconds","executed_sst","sst_simulated_microseconds","guards","checked","cqe_log_records","stage_error","first_mismatch") if k in data}
for f in ("sim.log","e2e-failure.log","check.log","native-runner.log"):
    x=p/f
    if x.is_file():
        raw=x.read_bytes()
        results[f]={"bytes":len(raw),"age_seconds":round(time.time()-x.stat().st_mtime),
                    "cqe":raw.count(b"WrCQEResp")}
        matches=re.findall(rb"Simulation is complete, simulated time: ([0-9.]+) (ns|us|ms|s)",raw)
        if matches:
            results[f]["completion"]=[v.decode() for v in matches[-1]]
        causes={"mmio":b"MMIO","sq_db":b"DB addr","segfault":b"Segmentation fault","cqe_timeout":b"CQ_TIMEOUT","failed":b"failed","fatal":b"fatal","assert":b"AssertionError"}
        results[f]["categories"]={k:raw.lower().count(v.lower()) for k,v in causes.items() if v.lower() in raw.lower()}
        allowed=re.findall(rb"(?:ValueError|AssertionError): (rank[0-9]{2} (?:returned|completed|output|status)\[[0-9]+\] mismatch: [0-9a-f]+ != [0-9a-f]+)",raw)
        if allowed:
            results[f]["first_mismatch"]=allowed[-1].decode()
if p.is_dir():
    missing, sampled, truncated_hosts = [], 0, 0
    flags_seen, flags_missing, flag_ops = 0, [], collections.Counter()
    flags_base = 0x200000000 + 120 * 512
    for host in range(64):
        path=p/"OUTPUT"/"stat_trace"/f"Host{host}_ba_layer_trace.json"
        if not path.is_file():
            continue
        sampled += 1
        events,truncated=read_observed(path,host)
        if truncated:
            truncated_hosts += 1
        with path.open("rb") as trace:
            prefix = trace.read(32 * 1024 * 1024)
        flags_written = set()
        for entry in ADDRESS_RECORD.finditer(prefix.decode("utf-8", "replace")):
            try:
                args = json.loads(entry.group())
                action = args["busPayloadType_"]
                raw = args["oriAddr_"]
                address = int(raw, 0) if isinstance(raw, str) else int(raw)
            except (KeyError, ValueError, TypeError):
                continue
            chip, remainder = divmod(address, GM_BYTES)
            slot = (remainder - flags_base) // 512
            if chip == host and 0 <= slot < 8:
                flag_ops[action] += 1
                if action == "WR_REQ":
                    flags_written.add(slot)
        expected = set(range(8)) - {host % 8}
        flags_seen += len(flags_written & expected)
        if expected - flags_written:
            flags_missing.append([host, sorted(expected-flags_written)])
        for peer in range(host//8*8,host//8*8+8):
            if peer==host:
                continue
            qp=peer%8
            ring=peer*40+(qp%2)*20+qp//2
            cq=events["WR_REQ"].get(ring,set())
            if len(cq.intersection(range(5)))!=5:
                missing.append([host,peer,sorted(set(range(5))-cq)])
    results["ba_trace"]={"sampled":sampled,"truncated_hosts":truncated_hosts,"missing_cq_qps":len(missing),
        "cq_sample":missing[:8],"flags_seen":flags_seen,"missing_flag_ranks":len(flags_missing),
        "flag_sample":flags_missing[:8],"flag_ops":flag_ops.most_common(6)}
    log=p/"sim.log"
    if log.is_file():
        found=[]
        for line in log.read_bytes().splitlines():
            if b"WrCQEResp" in line:
                m=re.search(rb"\bSim\s*[:=]\s*(\d+)",line)
                if m: found.append(int(m[1]))
        if found:
            results["last_cqe_sim"]=max(found)
if (p / "actual" / "rank00").is_dir():
    from struct import unpack
    diff_counts = collections.Counter()
    loss = collections.Counter()
    first = {}
    for rank in range(64):
        name = f"rank{rank:02d}"
        root = p / "actual" / name
        golden = p / "fixture-data" / name
        for field,size in (("returned",4),("completed",512),("output",4)):
            observed = (root / f"{field}.bin").read_bytes()
            expected = (golden / f"golden-{field}.bin").read_bytes()
            assert len(observed) == len(expected)
            diff_counts.setdefault(field,0)
            for n in range(0,len(expected),size):
                got,want = observed[n:n+size], expected[n:n+size]
                if got != want:
                    diff_counts[field] += 1
                    first.setdefault(field,[rank,n//size,want[:8].hex(),got[:8].hex()])
                    if field == "output" and n % 512 == 0:
                        token = n//512
                        d = unpack("<f",want)[0] - unpack("<f",got)[0]
                        if d and d == round(d) and 0 <= (d-token)/8 < 8 and (d-token)%8 == 0:
                            loss[int((d-token)/8)] += 1
                        else:
                            loss["other"] += 1
        raw = (root / "status.bin").read_bytes()
        diff_counts["nonzero_send_status"] += sum(v != 0 for v in unpack("<8I",raw[:32]))
        diff_counts["status_guard_errors"] += sum(v != 0xa5 for v in raw[32:])
    results["data_comparison"] = {"mismatches":diff_counts,"first":first,"lost_sender_on_h0":loss}
print(json.dumps(results,separators=(",",":")))
"""
ssh = ["ssh", "-T", "-o", "StrictHostKeyChecking=yes", "-o", "ConnectTimeout=8", "-o", "BatchMode=no",
       "-o", "NumberOfPasswordPrompts=1", "-o", "PreferredAuthentications=password",
       "-o", "PubkeyAuthentication=no", "root@141.61.94.58", "python3", "-"]
with askpass_environment() as env:
    proc = subprocess.run(ssh,input=code.encode(),env=env,capture_output=True,timeout=50)
    if proc.returncode:
        print(json.dumps({"ssh_exit":proc.returncode,"stderr_bytes":len(proc.stderr)}))
    else:
        print(json.dumps(json.loads(proc.stdout),separators=(",",":")))
