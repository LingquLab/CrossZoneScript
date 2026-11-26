from concurrent.futures import ThreadPoolExecutor
import json

from controller import WINDOWS
from read_case import case_transport


if __name__ == "__main__":
    manifests = list(WINDOWS.parent.glob("实机手册.md"))
    sections = {}
    if manifests:
        manual = manifests[0].read_text(encoding="utf-8-sig").lower()
        for label, terms in {
            "reservation": ("预约", "预留", "reservation"),
            "scheduler": ("调度器", "slurm", "pbs", "排队", "scheduler"),
            "exclusive": ("独占", "exclusive"),
        }.items():
            sections[label] = any(term in manual for term in terms)
    outcome, profile, _, _, transport = case_transport("bs128-baseline-off")
    script = """import json, shutil
commands = ("salloc", "squeue", "sinfo", "qstat", "qsub", "bsub", "scontrol")
print(json.dumps({"commands": [name for name in commands if shutil.which(name)]}))
"""

    def run(node):
        return transport(profile, node, script, outcome, "reservation-support", 20)

    with ThreadPoolExecutor(max_workers=2) as pool:
        print(json.dumps({"manual_found": bool(manifests), "manual_sections": sections,
                          "hosts": list(pool.map(run, (0, 1)))}, separators=(",", ":")))
