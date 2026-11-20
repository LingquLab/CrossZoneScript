"""Wait for the existing T4 run and read its terminal evidence."""

from pathlib import Path

entry = Path(__file__).with_name("read_mte_t4.py")
exec(compile(entry.read_bytes(), str(entry), "exec"))
