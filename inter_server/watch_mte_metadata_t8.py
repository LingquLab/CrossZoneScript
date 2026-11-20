"""Read the T8 split-forward run and output checks."""

from pathlib import Path

entry = Path(__file__).with_name("read_mte_t4.py")
exec(compile(entry.read_bytes(), str(entry), "exec"))
