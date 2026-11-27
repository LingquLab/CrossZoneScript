"""Run the prepared single-rank cases sequentially."""
import gzip

def one(tokens, mode):
    folder = SUITE / f"t{tokens}-{mode}"
    folder.mkdir()
    mount = "/work/final-reduce"
    for name in ("v310_config", "UBusSim_config"):
        shutil.copytree(REFERENCE / name, folder / name)
    for name in ("test_UBusSim.py", "installed_model.py", "environment.snapshot.json"):
        shutil.copyfile(PLATFORM / name, folder / name)
    (folder / "probe.o").write_bytes(binary)
    (folder / "case.py").write_text(REQUEST["case"])
    (folder / "exports.py").write_text(REQUEST["exports"])
    row = generate(folder, mount, REQUEST["abi"][str(tokens)], tokens, mode)
    container = None
    try:
        container = capture("create", "--runtime=runc", "--network=none", "--cap-drop=ALL",
                            "--security-opt=no-new-privileges", "--cpus=2", "--memory=12g", "--pids-limit=1024",
                            "--mount", f"type=bind,src={folder},dst={mount}", REQUEST["image"],
                            "/bin/bash", "-lc", "exec sleep infinity").decode().strip()
        capture("start", container)
        capture("exec", container, "python3", "-c", CONFIGURE, mount)
        started = time.monotonic()
        with (folder / "sim.log").open("wb") as log:
            run = docker("exec", container, "bash", "-c", SHELL, "bash", mount,
                         stdout=log, stderr=subprocess.STDOUT, timeout=140)
        row.update(sst_exit=run.returncode, wall_seconds=round(time.monotonic() - started, 3))
        log = (folder / "sim.log").read_bytes()
        stamp = re.search(rb"Simulation is complete, simulated time: ([0-9.]+) (ns|us|ms|s)", log)
        if stamp and run.returncode == 0:
            time_value, unit = (word.decode() for word in stamp.groups())
            row["sim_us"] = float(time_value) * {"ns": 0.001, "us": 1, "ms": 1000, "s": 1000000}[unit]
            check = docker("exec", container, "python3", mount + "/case.py", mount,
                           capture_output=True, timeout=45)
            (folder / "check.log").write_bytes(check.stdout + check.stderr)
            row["check_exit"] = check.returncode
            if check.returncode == 0:
                row.update(json.loads(check.stdout))
            else:
                row["check_error"] = check.stderr.decode(errors="replace").splitlines()[-1][:160]
    except (OSError, ValueError, AssertionError, subprocess.SubprocessError) as error:
        row["error"] = type(error).__name__
        (folder / "failure.log").write_text(traceback.format_exc())
    finally:
        if container:
            docker("rm", "-f", container, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    write(folder / "result.json", row)
    return row

SUITE.mkdir(exist_ok=False)
binary = gzip.decompress(base64.b64decode(REQUEST["object"], validate=True))
assert hashlib.sha256(binary).hexdigest() == REQUEST["object_sha256"]
namespace = {"__name__": "fixture"}
exec(REQUEST["case"], namespace)
generate = namespace["generate"]
result = {"object": {key: REQUEST[key] for key in ("revision", "object_sha256")},
          "shape": {"ranks": 1, "world": 64, "active_servers": 8, "hidden": 128,
                    "aivs": 16, "dtype": "fp32", "profiling": False}, "rows": []}
for tokens in REQUEST["tokens"]:
    for mode in ("full", "window", "physical"):
        result["rows"].append(one(tokens, mode))
        write(SUITE / "result.json", result)
print(json.dumps(result, separators=(",", ":")))
