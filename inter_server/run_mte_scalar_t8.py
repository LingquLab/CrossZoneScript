"""Run T8 with the scalar-forward candidate after the same object passes T4."""

from pathlib import Path

entry = Path(__file__).with_name("run_mte_metadata_t8.py")
exec(compile(entry.read_bytes(), str(entry), "exec"))
