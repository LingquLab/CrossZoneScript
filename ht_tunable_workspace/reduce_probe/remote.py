"""Seed the exact retained rank-zero inputs, run both workers, and check the dumps."""
import gzip
import math
import posixpath
import struct

E2E = ROOT / "inter-server-native/ht-tunable-workspace-186dfa18-bs4-bf16-castfix"
rank = "rank00"
previous = E2E / "actual" / rank
original = E2E / "input_bin" / rank

SPECS = {
    "gateway": dict(blocks=8, entry="tilexr_probe_gateway_reduce", field="partial", column=192,
                    payloads=("gather", "gather_ready"), loads=("forward", "ranges", "masks"),
                    status_arg="local.status", status_offset=96, status_stride=4),
    "final": dict(blocks=16, entry="tilexr_probe_final_reduce", field="output", column=4,
                  payloads=("returned", "completed", "local_ready"), loads=("server_mask",),
                  status_arg="finalStatus", status_offset=0, status_stride=32),
}


def final_reference(params):
    hidden = params["send.config.hidden"]["value"]
    world = params["send.config.world"]["value"]
    stride = max(world // 8, params["send.config.topk"]["value"])
    words = [word for word, in struct.iter_unpack("<H", (previous / "returned.bin").read_bytes())]
    masks = [mask for mask, in struct.iter_unpack("<Q", (original / "server_mask.bin").read_bytes())]
    output = bytearray()
    for token, mask in enumerate(masks):
        servers = [server for server in range(stride) if mask & (1 << server)]
        assert servers == [0, 1]
        for column in range(hidden):
            values = [struct.unpack("<f", struct.pack("<I", words[(token * stride + server) * hidden + column] << 16))[0]
                      for server in servers]
            fraction, exponent = math.frexp(sum(values))
            result = math.ldexp(round(fraction * 256), exponent - 8)
            output.extend(struct.pack("<f", result)[2:])
    return output


def describe_trace(role, workspace, fields):
    def values(raw):
        return [struct.unpack("<f", struct.pack("<I", word << 16))[0]
                for word, in struct.iter_unpack("<H", raw)]

    def bf16(data):
        row = bytearray()
        for value in data:
            fraction, exponent = math.frexp(value)
            row.extend(struct.pack("<f", math.ldexp(round(fraction * 256), exponent - 8))[2:])
        return row

    def fp32(data):
        return struct.pack("<256f", *data)

    if role == "gateway":
        forward = list(struct.iter_unpack("<6i", (original / "forward.bin").read_bytes()))
        assert forward[0][:3] == (7, 0, 0)
        gather = (previous / "gather.bin").read_bytes()
        rows = [workspace[:512]]
        for _, _, _, contributor, input_row, gather_row in forward[:7]:
            source, index = (workspace, input_row) if contributor == 0 else (gather, gather_row)
            rows.append(source[index * 512:(index + 1) * 512])
        accumulator = [0.0] * 256
        snapshots = {}
        for i, raw in enumerate(rows):
            widened = values(raw)
            accumulator = [a + b for a, b in zip(accumulator, widened)]
            snapshots.update({f"input{i}": ("H", raw), f"cast{i}": ("f", fp32(widened)),
                              f"sum{i}": ("f", fp32(accumulator))})
        snapshots["narrow"] = ("H", bf16(accumulator))
        return snapshots
    assert fields["serverMask"]["value"] == fields["load_server_mask"]["value"]
    assert struct.unpack_from("<Q", (original / "server_mask.bin").read_bytes())[0] == 3
    returned = (previous / "returned.bin").read_bytes()
    first, second = returned[:512], returned[512:1024]
    a, b = values(first), values(second)
    total = [x + y for x, y in zip(a, b)]
    snapshots = {"input0": ("H", first), "cast0": ("f", fp32(a)), "sum0": ("f", fp32(a)),
                 "input1": ("H", second), "cast1": ("f", fp32(b)), "reload_sum0": ("f", fp32(a)),
                 "add": ("f", fp32(total)), "narrow": ("H", bf16(total))}
    if REQUEST["options"].get("isolate"):
        snapshots.update(unpack0=("H", first), unpack1=("H", second))
    return snapshots


def prepare_probe(role, spec, binary):
    folder = SUITE / role
    folder.mkdir()
    mount = f"/work/{role}"
    workload = json.loads((E2E / "workload.yml").read_bytes())["workload_config"]["chip0"]
    fields = workload["kernel_param"]
    assert workload["sys_va_base_addr"] == 0 and len((original / "kernel_args.bin").read_bytes()) == 552
    assert fields["send.config.tokens"]["value"] == 4 and fields["send.config.hidden"]["value"] == 256
    guard_inputs = tuple(name.removeprefix("load_") for name in fields if name.startswith("load_guard_"))
    requested = {"workspace", "output", *spec["loads"], *guard_inputs}
    retained = {name: value for name, value in fields.items()
                if not name.startswith("load_") or name.removeprefix("load_") in requested}
    workspace = bytearray((original / "workspace.bin").read_bytes())
    exports = workload["output_param"]
    base = fields["load_workspace"]["value"]
    seeds = {}
    for name in spec["payloads"]:
        payload = (previous / f"{name}.bin").read_bytes()
        address, size = exports[name]["addr"], exports[name]["size"]
        start = address - base
        assert len(payload) == size and 0 <= start <= len(workspace) - size
        assert not any(workspace[start:start + size])
        workspace[start:start + size] = payload
        seeds[name] = hashlib.sha256(payload).hexdigest()
    if role == "gateway":
        ready = (previous / "ready.bin").read_bytes()
        (folder / "golden-ready.bin").write_bytes(ready)
        golden = bytearray((E2E / "fixture-data" / rank / "golden-partial.bin").read_bytes())
    else:
        golden = final_reference(fields)
    field = spec["field"]
    (folder / f"expected-{field}.bin").write_bytes(golden)
    shutil.copyfile(previous / f"{field}.bin", folder / f"pipeline-{field}.bin")
    for name in requested:
        source = original / f"{name}.bin"
        if name == "workspace":
            (folder / source.name).write_bytes(workspace)
        else:
            shutil.copyfile(source, folder / source.name)
        assert posixpath.basename(retained[f"load_{name}"]["input_bin"]) == source.name
        retained[f"load_{name}"]["input_bin"] = f"{mount}/{source.name}"
    names = [field, "status", *guard_inputs] + (["ready"] if role == "gateway" else [])
    status_base = fields[spec["status_arg"]]["value"] - exports["status"]["addr"]
    status_offsets = [status_base + spec["status_offset"] + i * spec["status_stride"] for i in range(spec["blocks"])]
    assert min(status_offsets) >= 0 and max(status_offsets) + 4 <= exports["status"]["size"]
    workload.update(kernel_param=retained, output_param={name: exports[name] for name in names},
                    block_dim=spec["blocks"], vec_func=spec["entry"], kernel_file=f"{mount}/probe.o")
    trace_spec = {}
    options = REQUEST["options"]
    if options.get("trace") or (options.get("isolate") and role == "final"):
        trace = describe_trace(role, workspace, fields)
        base = 0x420000000
        assert all(item["addr"] + item["size"] <= base for item in workload["output_param"].values())
        size = len(trace) * 1024
        (folder / "trace.bin").write_bytes(b"\xa5" * size)
        offset = max(int(item["param_offset"]) for item in retained.values()) + 8
        retained["load_diagnostic_trace"] = dict(value=base, param_offset=str(offset), param_type="11",
                                                 input_bin=f"{mount}/trace.bin")
        workload["output_param"]["trace"] = dict(addr=base, size=size)
        for slot, (name, (dtype, data)) in enumerate(trace.items()):
            (folder / f"golden-trace-{name}.bin").write_bytes(data)
            trace_spec[name] = dict(offset=slot * 1024, dtype=dtype, size=len(data))
    (folder / "probe.o").write_bytes(binary)
    for directory in ("v310_config", "UBusSim_config"):
        shutil.copytree(REFERENCE / directory, folder / directory)
    for filename in ("test_UBusSim.py", "installed_model.py", "environment.snapshot.json"):
        shutil.copyfile(PLATFORM / filename, folder / filename)
    write(folder / "v310_config/workload_config.yml", {"workload_config": {"chip0": workload}})
    (folder / "check.py").write_text(CHECKER)
    write(folder / "check.json", dict(exports=workload["output_param"], field=field,
                                      references=("expected", "pipeline"), column=spec["column"],
                                      status_offsets=status_offsets, inputs_sha256=seeds, trace=trace_spec))
    return folder, mount


def run_probe(role, spec, binary):
    folder, mount = prepare_probe(role, spec, binary)
    result = {"blocks": spec["blocks"]}
    container = capture("create", "--runtime=runc", "--network=none", "--cap-drop=ALL",
                        "--security-opt=no-new-privileges", "--cpus=2", "--memory=12g", "--pids-limit=1024",
                        "--mount", f"type=bind,src={folder},dst={mount}", REQUEST["image"],
                        "/bin/bash", "-lc", "exec sleep infinity").decode().strip()
    try:
        capture("start", container)
        capture("exec", container, "python3", "-c", CONFIGURE, mount)
        started = time.monotonic()
        with (folder / "sim.log").open("wb") as log:
            ran = docker("exec", container, "bash", "-c", SHELL, "bash", mount,
                         stdout=log, stderr=subprocess.STDOUT, timeout=230)
        result.update(sst_exit=ran.returncode, sst_wall_seconds=round(time.monotonic() - started, 3))
        raw = (folder / "sim.log").read_bytes()
        libs = {Path(p.decode()).name for p in re.findall(rb"calling init: ([^\r\n]+\.so)\s*$", raw, re.M)}
        expected_libs = {path.name for path in LIB_DIR.glob("*.so")}
        result["loaded_repair_libraries"] = len(libs & expected_libs)
        if ran.returncode == 0:
            proc = docker("exec", container, "python3", mount + "/check.py", mount, capture_output=True, timeout=45)
            (folder / "checker.log").write_bytes(proc.stdout + proc.stderr)
            result["checker_exit"] = proc.returncode
            if proc.returncode == 0:
                result.update(json.loads(proc.stdout))
            else:
                result["checker_error"] = proc.stderr.decode(errors="replace").splitlines()[-1][:180]
    except (OSError, ValueError, AssertionError, subprocess.SubprocessError) as error:
        result["error"] = type(error).__name__
        (folder / "failure.log").write_text(traceback.format_exc())
    finally:
        docker("rm", "-f", container, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        write(folder / "result.json", result)
    return result


result = dict(artifact=REQUEST["artifact"], case=SUITE.name, image=REQUEST["image"], probes={})
try:
    SUITE.mkdir(exist_ok=False)
    binary = gzip.decompress(base64.b64decode(REQUEST["object"], validate=True))
    assert hashlib.sha256(binary).hexdigest() == REQUEST["artifact"]["object_sha256"]
    roles = REQUEST["options"].get("roles", list(SPECS))
    for role in roles:
        assert role in SPECS
    with ThreadPoolExecutor(max_workers=2) as pool:
        tasks = {role: pool.submit(run_probe, role, SPECS[role], binary) for role in roles}
        for role, task in tasks.items():
            try:
                result["probes"][role] = task.result()
            except Exception as error:
                line = traceback.extract_tb(error.__traceback__)[-1]
                result["probes"][role] = dict(error=type(error).__name__, line=line.lineno,
                                              detail=str(error)[:120])
                (SUITE / (role + "-failure.log")).write_text(traceback.format_exc())
except (OSError, ValueError, AssertionError, subprocess.SubprocessError) as error:
    result["error"] = type(error).__name__
if SUITE.is_dir():
    write(SUITE / "result.json", result)
print(json.dumps(result, separators=(",", ":")))
