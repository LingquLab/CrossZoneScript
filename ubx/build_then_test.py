"""Build the candidate, wait for a jointly observed idle window, and verify the two TRACE rounds."""
import runpy
from pathlib import Path
import build
if build.main():
    raise SystemExit('Candidate build failed; inspect build receipts')
runpy.run_path(str(Path(__file__).with_name('wait_run.py')),run_name='__main__')
