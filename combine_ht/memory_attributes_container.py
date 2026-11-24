"""Read the exact doorbell DAW attributes and relevant UBus routing selectors."""
import json
from pathlib import Path
import re
import subprocess
import yaml

CASE = Path("/work/cases/8p-bs16-a0840f02-a1/send-only")
ADDRESS = 0x401C00000A80
out = dict(ok=True, diagnostic_only=True, read_only=True, address=hex(ADDRESS),
           matching_windows=[], selectors=[], ubus_structure={})


def number(value):
    try:
        return int(value, 0) if isinstance(value, str) else int(value) if type(value) is int else None
    except ValueError:
        return None


def visit(node, path, daw=False):
    if isinstance(node, dict):
        begin = next((number(node[k]) for k in ("begin_addr", "start_addr", "addr_start", "base_addr", "begin", "start") if k in node), None)
        end = next((number(node[k]) for k in ("end_addr", "addr_end", "end", "limit_addr") if k in node), None)
        if begin is not None and end is not None and begin <= ADDRESS < end:
            if not daw or any("/chip%d/" % chip in path for chip in (0, 3)):
                values = {str(k): v for k, v in node.items() if isinstance(v, (str, int, float, bool))}
                out["matching_windows"].append(dict(path=path, attributes=values))
        for key, value in node.items():
            if key in ("host_gm_size", "host_ta_doorbell_id", "jetty_db_addr_base", "V310_flag", "UBusSim_flag", "urma_flag", "simt_urma_flag"):
                out["selectors"].append(dict(path=path + "/" + str(key), value=value))
            visit(value, path + "/" + str(key), daw)
    elif isinstance(node, list):
        if len(node) >= 3:
            begin, end = number(node[0]), number(node[1])
            if begin is not None and end is not None and begin <= ADDRESS < end:
                out["matching_windows"].append(dict(path=path, entry=str(node)[:500]))
        for index, value in enumerate(node):
            visit(value, path + "/" + str(index), daw)


path = CASE / "v310_config/daw_config.yml"
visit(yaml.safe_load(path.read_text()), "daw_config.yml", True)
for path in sorted((CASE / "UBusSim_config").glob("*.yml")):
    document = yaml.safe_load(path.read_text())
    out["ubus_structure"][path.name] = {str(k): list(v)[:24] if isinstance(v, dict) else type(v).__name__ for k, v in document.items()}
    visit(document, path.name)
out["matching_windows"] = out["matching_windows"][:24]
out["selector_count"] = len(out["selectors"])
out["selectors"] = out["selectors"][:40]
library = Path("/opt/sim/plugins/libDavinciSimulator.so")
p = subprocess.run(["nm", "-D", "-S", "-C", str(library)], capture_output=True, text=True, timeout=30)
assert p.returncode == 0, "address_symbols_unavailable"
out["analyzer_symbols"] = [line.split(maxsplit=3)[-1][:220] for line in p.stdout.splitlines()
                           if re.search(r"AddrAnalyzer.*::|Daw.*::|MemAttr|MemType", line)][:32]
print(json.dumps(out))
