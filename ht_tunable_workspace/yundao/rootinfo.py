"""Summarize RootInfo field types without returning fabric addresses."""
from collections import Counter
import json
import os
from pathlib import Path
import re


def objects(node):
    if isinstance(node, dict):
        yield node
        for child in node.values():
            yield from objects(child)
    elif isinstance(node, list):
        for child in node:
            yield from objects(child)


rootinfo = Path(os.environ.get("TILEXR_UDMA_ROOTINFO_PATH") or "/etc/hccl_rootinfo.json")
rows = list(objects(json.loads(rootinfo.read_text())))
device_rows = [row for row in rows if "device_id" in row]
address_fields = Counter()
for device in device_rows:
    for row in objects(device):
        if "addr" not in row:
            continue
        addr, ports = row["addr"], row.get("ports")
        addr_format = type(addr).__name__
        if isinstance(addr, str):
            if re.fullmatch(r"[0-9a-fA-F]{32}", addr):
                addr_format = "hex32"
            elif re.fullmatch(r"0x[0-9a-fA-F]{32}", addr, re.I):
                addr_format = "prefixed_hex32"
            else:
                addr_format = f"other_str_{len(addr)}"
        port_format = type(ports).__name__
        if isinstance(ports, list):
            port_format = ",".join(sorted({type(p).__name__ for p in ports})) or "empty"
        address_fields[(addr_format, port_format, ",".join(sorted(row)))] += 1
topologies = [row["topo_file_path"] for row in rows if isinstance(row.get("topo_file_path"), str)]
result = dict(device_count=len(device_rows), address_fields=[(*k, n) for k, n in address_fields.items()],
              topology_readable=bool(topologies and os.access(topologies[0], os.R_OK)))
print("TILEXR_RESULT " + json.dumps(result, ensure_ascii=True, separators=(",", ":")))
