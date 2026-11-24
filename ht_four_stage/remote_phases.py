"""Build the last two SST cases from checked, addressed dumps of the preceding stages."""
import io
import sys

REVISION = "8882b28a4cc586f91ddcea171170bf9e304adc70"
EP_SEND = "tilexr_ep_ht_inter_server_send_kernel"
EP_REDUCE = "tilexr_ep_ht_final_reduce_sst_kernel"
BUILD_HASH = {EP_SEND: "fac901d0b7983e15595a2cf4b09fe69d7f28bb2ff51b7387f390ab9467347093",
              EP_REDUCE: "561e4bcb78c036a2bde27bb19f8e38e18e72b2488c691500151cd6eba90037d5"}
STEM = "ht-four-stage-8882b28a-"
ROW_BYTES = 512
TOKENS = 4
HIDDEN = 128
SERVERS = 8
RANKS_PER_SERVER = 8
MAGIC = 2026
AIVS = 16
EXTRA_BASES = {"serverMask": 0x404000000, "localReady": 0x405000000, "output": 0x406000000}

def partial_golden(server, token, h):
    return 8 * (server * 64 + token + h / 64) + 224

def receiver_offset(token, server):
    return (token * SERVERS + server) * ROW_BYTES

def check_predecessor(case):
    assert json.loads((case / "run.json").read_text())["status"] == "sst_and_checker_passed"
    return phase_artifacts(case)

def prepare_remote_phase(request, case, entry, object_path, golden_directory, generated, compatible):
    sys.path.insert(0, str(MOUNT.parent / BUILD /
                    "source/tools/unitest/simulator/experiments/inter_server_send/sst"))
    import loader
    import native
    import runtime
    is_final = entry == EP_REDUCE
    object_digest = sha(object_path)
    if not compatible or is_final:
        assert object_digest == BUILD_HASH[entry]
    local_case = MOUNT / (STEM + "local-reduce")
    local_hashes = check_predecessor(local_case)
    initial_ready = MOUNT / (STEM + "local-send")
    assert check_predecessor(initial_ready)["returned"] == local_hashes["returned"]
    previous = MOUNT / (STEM + ("inter-send-mte" if compatible else "inter-send"))
    previous_hashes = check_predecessor(previous) if is_final else {}
    runtime_root = "/work/inter-server-native/" + case.name
    ranks = []
    for rank, abi in enumerate(request["abis"]):
        config = native.config_values(abi)
        assert tuple(config[k] for k in ("rank", "tokens", "hidden", "topk", "inputRows", "partialRows", "chunks", "magic")) == (
            rank, 4, 256, 8, 4, 4, 4, MAGIC)
        peer = rank ^ 8
        server, remote_server = rank // RANKS_PER_SERVER, peer // RANKS_PER_SERVER
        base = rank * runtime.GM_BYTES
        pointers = dict(runtime.BASES, **EXTRA_BASES)
        location = {name: base + off for name, off in pointers.items()}
        name = f"rank{rank:02d}"
        folder = golden_directory / name
        folder.mkdir(parents=True)
        workspace_size = config["workspaceBytes"]
        workspace = bytearray(workspace_size)
        source = local_case / "actual" / name
        partial = (source / "output.bin").read_bytes()
        progress = (source / "completed.bin").read_bytes()
        assert len(partial) == TOKENS * ROW_BYTES and len(progress) == 16 * ROW_BYTES
        assert all(struct.unpack_from("<I", progress, slot * ROW_BYTES)[0] == 1 for slot in range(8, 16))
        start = config["partialOffset"]
        workspace[start:start + len(partial)] = partial
        for reducer in range(config["reducerCount"]):
            struct.pack_into("<qq", workspace, config["readyOffset"] + reducer * ROW_BYTES, MAGIC, config["chunks"])
        return_start = config["returnOffset"]
        returned = bytearray(config["signalOffset"] - return_start)
        assert len(returned) == TOKENS * SERVERS * ROW_BYTES
        completed = bytearray(config["returnOffset"] - config["completionOffset"])
        for sender in range(64):
            if sender // RANKS_PER_SERVER != server:
                for lane in range(2):
                    off = ((sender * config["chunks"] + config["chunks"] - 1) * 2 + lane) * ROW_BYTES
                    struct.pack_into("<q", completed, off, MAGIC)
        for token in range(TOKENS):
            struct.pack_into("<128f", returned, receiver_offset(token, remote_server),
                             *(partial_golden(remote_server, token, h) for h in range(HIDDEN)))
            if is_final:
                struct.pack_into("<128f", returned, receiver_offset(token, server),
                                 *(partial_golden(server, token, h) for h in range(HIDDEN)))
        local_ready = bytearray(SERVERS * ROW_BYTES)
        if is_final:
            actual = previous / "actual" / name
            remote_rows = bytearray((actual / "returned.bin").read_bytes())
            remote_complete = (actual / "completed.bin").read_bytes()
            assert len(remote_rows) == len(returned) and len(remote_complete) == len(completed)
            for token in range(TOKENS):
                offset = receiver_offset(token, server)
                assert not any(remote_rows[offset:offset + ROW_BYTES])
                begin = token * ROW_BYTES
                remote_rows[offset:offset + ROW_BYTES] = partial[begin:begin + ROW_BYTES]
            workspace[return_start:config["signalOffset"]] = remote_rows
            workspace[config["completionOffset"]:return_start] = remote_complete
            for gateway in range(RANKS_PER_SERVER):
                struct.pack_into("<q", local_ready, gateway * ROW_BYTES, MAGIC)

        width = 32 if compatible else 24
        records = bytearray(64 * config["forwardCapacity"] * width)
        counts = [0] * 64
        ranges = [0] * (64 * config["chunks"] * 2)
        counts[peer] = TOKENS
        for token in range(TOKENS):
            offset = (peer * config["forwardCapacity"] + token) * width
            struct.pack_into("<6i", records, offset, token + 1, token, token, 0, token, 0)
            index = (peer * config["chunks"] + token) * 2
            ranges[index:index + 2] = token, token + 1
        output = struct.pack("<I", 0x7FC00000) * (TOKENS * HIDDEN)
        golden_output = struct.pack("<512f", *(partial_golden(server, token, h) + partial_golden(remote_server, token, h)
                                             for token in range(TOKENS) for h in range(HIDDEN))) if is_final else output
        (folder / "golden-returned.bin").write_bytes(returned)
        (folder / "golden-completed.bin").write_bytes(completed)
        (folder / "golden-output.bin").write_bytes(golden_output)
        fields = []
        if is_final:
            for index, pointer in enumerate(("workspace", "serverMask", "localReady", "output", "status")):
                fields.append(dict(name=pointer, offset=index * 8, value=location[pointer], type="GM_ADDR"))
            for item in abi["fields"][6:]:
                field = dict(item)
                field["offset"] -= 8
                if field["name"] == "config.hidden":
                    field["value"] = HIDDEN
                fields.append(field)
        else:
            fields, _, _ = native.native_arguments(abi, {key: location[key] for key in native.POINTERS})
        images = runtime.runtime_images(abi, rank, workspace_size)
        images.update(workspace=workspace, forward=records, counts=struct.pack("<64i", *counts),
                      ranges=struct.pack("<512i", *ranges), status=native.status_image(),
                      localReady=local_ready, serverMask=struct.pack("<4Q", *((1 << server) | (1 << remote_server),) * TOKENS),
                      output=output)
        memory = [dict(name=k, addr=location[k], data=v) for k, v in images.items()]
        guards = []
        for guard, address in (("guard_before", location["workspace"] - ROW_BYTES),
                               ("guard_after", location["workspace"] + workspace_size),
                               ("guard_output_before", location["output"] - ROW_BYTES),
                               ("guard_output_after", location["output"] + len(output))):
            memory.append(dict(name=guard, addr=address, data=b"\xde" * ROW_BYTES))
            guards.append(dict(name=guard, addr=address, size=ROW_BYTES, expected_byte=0xde))
        export = {
            "returned": dict(addr=location["workspace"] + return_start, size=len(returned)),
            "completed": dict(addr=location["workspace"] + config["completionOffset"], size=len(completed)),
            "output": dict(addr=location["output"], size=len(output)),
            "status": dict(addr=location["status"], size=AIVS * 32),
        }
        ranks.append(dict(rank=rank, abi_size=152 if is_final else 160, fields=fields,
                          gm_size=runtime.GM_BYTES, sys_va_base_addr=base, ub_base_addr=0x80000,
                          stack_base_addr=0x100000, param_base_addr=base + 0x80000000,
                          binary_addr=base + 0x40000000, images=memory, exports=export, guards=guards))
    identity = dict(tokens=4, hidden=128, chunks=4, world=64, entry=entry,
                    kernel_sha256=object_digest, runtime_root=runtime_root,
                    phase=4 if is_final else 3, source_commit=REVISION)
    write_json(golden_directory / "manifest.json", identity)
    loader.prepare_loader(generated, runtime_root, object_path, object_digest, entry, ranks)
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz", compresslevel=3) as archive:
        for item in sorted(golden_directory.rglob("*.bin")) + [golden_directory / "manifest.json"]:
            archive.add(item, arcname="fixture-data/" + str(item.relative_to(golden_directory)), recursive=False)
        for item in sorted((generated / "input_bin").rglob("*.bin")):
            archive.add(item, arcname=str(item.relative_to(generated)), recursive=False)
        for item in ("workload.yml", "export-layout.json"):
            archive.add(generated / item, arcname=item, recursive=False)
    data = buffer.getvalue()
    request_data = dict(request)
    request_data.update(
        tokens=4, hidden=128, identity=dict(identity, input_archive_sha256=hashlib.sha256(data).hexdigest()),
        archive=base64.b64encode(data).decode(),
        object=base64.b64encode(gzip.compress(object_path.read_bytes())).decode(),
        build=dict(entry=entry, variant=case.name, candidate_commit=REVISION,
                   kernel_sha256=object_digest, compiled_in="green"))
    prepare_bulk(request_data, case, runtime_root)
    return {"local_output_sha256": local_hashes["output"], "local_progress_sha256": local_hashes["completed"],
            "inter_input_handoff": previous_hashes}

def run_remote_phases(request):
    compatible = request.get("compatible", False)
    build_root = MOUNT.parent / BUILD
    results = []
    for entry, filename, purpose in ((EP_SEND, "native/src/ep/" + EP_SEND + ".o", "inter-send"),
                                     (EP_REDUCE, "sst/src/ep/" + EP_REDUCE + ".o", "final-reduce")):
        suffix = "-mte" if compatible else ""
        case = MOUNT / (STEM + purpose + suffix)
        if case.exists():
            result = summarize(case)
        else:
            object_path = build_root / filename
            if entry == EP_SEND and compatible:
                object_path = compatible_sender(request)
            import tempfile
            with tempfile.TemporaryDirectory(prefix="ht-8882b28a-fold-", dir=MOUNT.parent) as temporary:
                temp = Path(temporary)
                handoff = prepare_remote_phase(request, case, entry, object_path, temp / "golden",
                                               temp / "loader", compatible)
            write_json(case / "handoff.json", handoff)
            result = execute_phase(case)
        if (case / "handoff.json").is_file():
            result["handoff"] = json.loads((case / "handoff.json").read_text())
        results.append(result)
        if result.get("status") != "sst_and_checker_passed":
            break
    return {"phases": results}
