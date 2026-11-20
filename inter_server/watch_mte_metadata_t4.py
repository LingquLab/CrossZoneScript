"""Read current or terminal evidence for the split-forward candidate."""

from pathlib import Path

entry = Path(__file__).with_name("read_mte_t4.py")
exec(compile(entry.read_bytes(), str(entry), "exec"))
