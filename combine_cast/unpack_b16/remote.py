"""Run one single-chip case on the Cast-fixed image, retaining logs and dumps."""
import gzip

result = {"artifact_dir": str(SUITE), "image": REQUEST["image"], "object_sha256": REQUEST["object_sha256"]}
mount = "/work/unpack-b16"
container = None
try:
    SUITE.mkdir(exist_ok=False)
    binary = gzip.decompress(base64.b64decode(REQUEST["object"], validate=True))
    assert hashlib.sha256(binary).hexdigest() == REQUEST["object_sha256"]
    (SUITE / "probe.o").write_bytes(binary)
    (SUITE / "case.py").write_text(REQUEST["case"])
    (SUITE / "exports.py").write_text(REQUEST["exports"])
    subprocess.run(["python3", str(SUITE / "case.py"), "generate", str(SUITE), "--mount", mount], check=True)
    for directory in ("v310_config", "UBusSim_config"):
        shutil.copytree(REFERENCE / directory, SUITE / directory)
    for name in ("test_UBusSim.py", "installed_model.py", "environment.snapshot.json"):
        shutil.copyfile(PLATFORM / name, SUITE / name)
    shutil.copyfile(SUITE / "workload.yml", SUITE / "v310_config/workload_config.yml")
    container = capture("create", "--runtime=runc", "--network=none", "--cap-drop=ALL",
                        "--security-opt=no-new-privileges", "--cpus=2", "--memory=12g", "--pids-limit=1024",
                        "--mount", f"type=bind,src={SUITE},dst={mount}", REQUEST["image"],
                        "/bin/bash", "-lc", "exec sleep infinity").decode().strip()
    capture("start", container)
    capture("exec", container, "python3", "-c", CONFIGURE, mount)
    started = time.monotonic()
    with (SUITE / "sim.log").open("wb") as log:
        run = docker("exec", container, "bash", "-c", SHELL, "bash", mount,
                     stdout=log, stderr=subprocess.STDOUT, timeout=200)
    result.update(sst_exit=run.returncode, sst_wall_seconds=round(time.monotonic() - started, 3))
    raw = (SUITE / "sim.log").read_bytes()
    names = {Path(path.decode()).name for path in re.findall(rb"calling init: ([^\r\n]+\.so)\s*$", raw, re.M)}
    libraries = {path.name for path in LIB_DIR.glob("*.so")}
    assert len(libraries) == 7
    result["loaded_repair_libraries"] = len(names & libraries)
    done = re.search(rb"Simulation is complete, simulated time: ([0-9.]+) (ns|us|ms|s)", raw)
    result["sim"] = [word.decode() for word in done.groups()] if done else None
    if run.returncode == 0:
        check = docker("exec", container, "python3", mount + "/case.py", "check", mount,
                       capture_output=True, timeout=45)
        (SUITE / "check.log").write_bytes(check.stdout + check.stderr)
        result["check_exit"] = check.returncode
        if check.stdout:
            result.update(json.loads(check.stdout))
        if (SUITE / "actual-raw.bin").is_file():
            result["raw_sha256"] = digest(SUITE / "actual-raw.bin")
except (OSError, ValueError, AssertionError, subprocess.SubprocessError) as error:
    result.update(error=type(error).__name__, line=traceback.extract_tb(error.__traceback__)[-1].lineno)
    if SUITE.is_dir():
        (SUITE / "failure.log").write_text(traceback.format_exc())
finally:
    if container is not None:
        docker("rm", "-f", container, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
if SUITE.is_dir() and not (SUITE / "result.json").exists():
    write(SUITE / "result.json", result)
print(json.dumps(result, separators=(",", ":")))
