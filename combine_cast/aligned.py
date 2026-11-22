"""Run the aligned vector-cast and in-UB round-trip probe."""
from pathlib import Path
import runpy
import sys

sys.argv.append("--aligned")
runpy.run_path(str(Path(__file__).with_name("local_suite.py")),run_name="__main__")
