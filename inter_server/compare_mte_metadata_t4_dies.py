"""Compare raw Die data for the split-forward variant."""

from pathlib import Path

entry = Path(__file__).with_name("compare_mte_t4_dies.py")
exec(compile(entry.read_bytes(), str(entry), "exec"))
