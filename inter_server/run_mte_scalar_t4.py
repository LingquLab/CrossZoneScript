"""Run the HT SST variant that reads read-only forward records with scalar GM loads."""

from pathlib import Path

entry = Path(__file__).with_name("run_mte_t4.py")
exec(compile(entry.read_bytes(), str(entry), "exec"))
