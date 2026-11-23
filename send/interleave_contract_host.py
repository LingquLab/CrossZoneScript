"""Select bounded evidence from the previous session's saved preprocessing."""
import bisect
import json
from pathlib import Path
import re
import socket
import sys

PRIOR = Path("/var/tmp/tilexr-combine-ht-05d1d635-d6d7-4dd3-af34-421e32bb209c-v11")


def collect():
    assert socket.gethostname().split(".")[0] == sys.argv[1] == "host-94-26"
    path = PRIOR / "mte-interleave-1/compiler-evidence-1/input.ii"
    assert 0 < path.stat().st_size < 16000000
    text = path.read_text(errors="strict")
    directives = list(re.finditer(r'(?m)^#\s+[0-9]+\s+"([^"\n]+)"', text))
    offsets = [match.start() for match in directives]
    functions = []
    for match in re.finditer(r"\b(DataCopy\w*|Interleave\w*)\s*\(", text):
        brace = text.find("{", match.end())
        semicolon = text.find(";", match.end())
        if brace < 0 or brace - match.end() > 1600 or (semicolon >= 0 and semicolon < brace):
            continue
        signature = " ".join(text[text.rfind("\n", 0, match.start()) + 1:brace].split())
        depth, end = 1, brace + 1
        while end < len(text) and depth and end - brace < 18000:
            depth += (text[end] == "{") - (text[end] == "}")
            end += 1
        if depth:
            continue
        body = text[brace + 1:end - 1]
        store = "__ubuf__" in signature and re.search(r"postUpdateStride|postStride|postMode", signature + body)
        interleave = match[1].startswith("Interleave") and "srcReg" in signature
        if not (store or interleave):
            continue
        # Restrict store inspection to UB destinations, not the load overloads.
        first_arg = text[match.end():brace].split(",", 1)[0]
        if store and "__ubuf__" not in first_arg:
            continue
        index = bisect.bisect_right(offsets, match.start()) - 1
        source = directives[index][1] if index >= 0 else "unknown"
        callees = sorted(set(re.findall(r"\b([A-Za-z_]\w*)\s*(?:<[^;\n]{1,180}>)?\s*\(", body)))
        relevant = [line.strip() for line in body.splitlines()
                    if re.search(r"vsts|vst_|postUpdateStride|postStride|interleave|vzip", line, re.I)]
        functions.append({"name": match[1], "source": source, "signature": signature,
                          "callees": callees[:12], "relevant": relevant})
    # Prefer the installed implementation and keep at most eight source lines.
    selected = sorted(functions, key=lambda row: (not any("vsts" in name for name in row["callees"]),
                                                "Impl" not in row["name"]))[:8]
    snippets, summaries = [], []
    for row in selected:
        summaries.append({key: row[key] for key in ("name", "source", "callees")})
        if len(snippets) < 8:
            snippets.append(row["signature"][:650])
        for line in row["relevant"][:2]:
            if len(snippets) < 8:
                snippets.append(line[:650])
    saved = json.loads((PRIOR / "simd-result.json").read_text())
    result_keys = ("ok", "variant", "report", "object_sha256", "build_completed", "returncode",
                   "wqe_verified", "checked_wqes", "checked_status_words", "status_mismatch_count",
                   "status_words", "first_mismatch", "backward_record_bytes", "stop_boundary")
    results = [{key: row[key] for key in result_keys if key in row} for row in saved.get("results", [])[:2]]
    constants = {}
    for name in ("kWqeBytes", "kWqeU32Words", "kBatchRows", "TILEXR_UDMA_WQE_BB_BYTES"):
        matches = re.findall(r"\b" + name + r"\s*=\s*([^;\n]+)", text)
        constants[name] = list(dict.fromkeys(matches))[:2]
    return {"evidence_kind": "retained_preprocessing_and_results", "preprocess_bytes": path.stat().st_size,
            "contract_functions": summaries, "source_snippets": snippets,
            "layout_constants": constants, "prior_results": results, "simulation_started": False}


if __name__ == "__main__":
    print(json.dumps(collect(), separators=(",", ":")))
