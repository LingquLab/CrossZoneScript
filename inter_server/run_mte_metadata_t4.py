"""Run the HT SST candidate with forward record reads split at stripe boundaries."""

from pathlib import Path

entry = Path(__file__).with_name("run_mte_t4.py")
exec(compile(entry.read_bytes(), str(entry), "exec"))
