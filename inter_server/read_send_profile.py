"""Read the T4 phase capture status without starting SST."""

from pathlib import Path

reader = Path(__file__).with_name("read_bulk_forward.py")
exec(compile(reader.read_bytes(), str(reader), "exec"))
