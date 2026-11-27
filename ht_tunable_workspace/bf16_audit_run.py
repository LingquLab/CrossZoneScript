"""Read the retained BF16 case without running SST."""
import json
from pathlib import Path
import sys

here = Path(__file__).resolve().parent
sys.path.insert(0, str(here.parent / "alltoall"))
from ssh_transport import run

source = (here / "bf16_reference_audit.py").read_text()
compile(source, "<bf16-reference-audit>", "exec")
print(json.dumps(run(source, password=True, timeout=90), separators=(",", ":")))
