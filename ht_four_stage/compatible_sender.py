"""Use the verified sender MTE object when testing its SST-compatible queue accesses."""
def compatible_sender(request):
    object_bytes = gzip.decompress(base64.b64decode(request["mte_sender"], validate=True))
    digest = hashlib.sha256(object_bytes).hexdigest()
    assert digest == "5851c89daf6e400813e2c963841d4cf1bdf6690c06ddb745999d4e566908181d"
    directory = MOUNT.parent / BUILD / "sst-compat"
    directory.mkdir(exist_ok=True)
    obj = directory / (digest[:8] + ".o")
    if not obj.exists():
        with obj.open("xb") as stream:
            stream.write(object_bytes)
    assert sha(obj) == digest
    return obj
