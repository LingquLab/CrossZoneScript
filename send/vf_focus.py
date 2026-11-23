"""Read selected IR edges from the completed decode task, without compilation."""
import hashlib
import json
from pathlib import Path
import re


def blocks(text):
    return list(re.finditer(r"(?ms)^define\b(.*?)^}", text))


def operands(text):
    depth, begin, values = 0, 0, []
    for index, char in enumerate(text):
        depth += (char in "([{<") - (char in ")]}>")
        if char == "," and depth == 0:
            values.append(text[begin:index].strip())
            begin = index + 1
    return values + [text[begin:].strip()]


def inspect(root):
    result = json.loads((root / "vf-decode-result.json").read_text())
    assert result["ir_available"] and result["source_modified"] is False
    out = {"read_only": True, "recompiled": False, "simulation_started": False, "cases": [], "source_snippets": []}
    for record in result["cases"]:
        path = root / "vf-decode-inspection-1" / record["case"] / "decoded.ll"
        assert path.stat().st_size == record["ir_bytes"]
        blob = path.read_bytes()
        text = blob.decode()
        name = record["functions"][0]["name"]
        functions = blocks(text)
        selected = next(match[0] for match in functions if "@" + name + "(" in match[0].splitlines()[0])
        lines = selected.splitlines()
        row = {"case": record["case"], "ir_sha256": hashlib.sha256(blob).hexdigest(), "function": name}
        if "gather-24" in record["case"]:
            adds = [line.strip() for line in lines if "call" in line and "@llvm.hivm.vadds.u.x.v64i32(" in line]
            stores = [line.strip() for line in lines if "call" in line and "@llvm.hivm.vstsx1.post.v64i32(" in line]
            row.update(add_calls=len(adds), store_calls=len(stores))
            definitions = {match[1]: match[0].strip() for match in re.finditer(r"(?m)^\s*(%[-.a-zA-Z0-9_]+)\s*=.*$", selected)}
            producers = []
            row["last_store_sources"] = []
            for ordinal, line in list(enumerate(stores, 1))[-2:]:
                value = re.search(r"<64 x i32>\s+(%[-.a-zA-Z0-9_]+)", line)
                producer = definitions.get(value[1], "") if value else ""
                if producer:
                    producers.append(producer)
                row["last_store_sources"].append({"store_ordinal": ordinal,
                    "producer_is_vector_add_scalar": "@llvm.hivm.vadds.u.x.v64i32(" in producer,
                    "scalar_constants": [int(value) for value in re.findall(r"\bi32 (-?\d+)\b", producer)][:3]})
            out["source_snippets"].extend(line[:450] for line in (producers or adds[-2:]) + stores[-2:])
        if "interleave-22" in record["case"]:
            accesses = [line.strip() for line in lines if re.search(r"\b(?:load|store)\b.*\bptr %3(?:,|\s|$)", line)]
            row["generic_parameter3_access_count"] = len(accesses)
            out["source_snippets"].extend(line[:450] for line in accesses[:2])
            for caller in functions:
                call = next((line.strip() for line in caller[0].splitlines()
                             if "call" in line and "@" + name + "(" in line), None)
                if not call:
                    continue
                begin = call.index("@" + name + "(") + len(name) + 2
                depth, end = 1, begin
                while end < len(call) and depth:
                    depth += (call[end] == "(") - (call[end] == ")")
                    end += 1
                arguments = operands(call[begin:end - 1])
                row["caller_argument_count"] = len(arguments)
                row["generic_parameter3_argument"] = arguments[3][:100]
                value = re.search(r"(%[-.a-zA-Z0-9_]+)\s*$", arguments[3])
                definitions = {match[1]: match[0].strip() for match in re.finditer(r"(?m)^\s*(%[-.a-zA-Z0-9_]+)\s*=.*$", caller[0])}
                origin = definitions.get(value[1], "") if value else ""
                row["parameter3_origin_is_alloca"] = bool(re.search(r"=\s*alloca\b", origin))
                out["source_snippets"].extend(line[:450] for line in (origin, call) if line)
                break
        out["cases"].append(row)
    out["source_snippets"] = out["source_snippets"][:8]
    return out


if __name__ == "__main__":
    root = Path("/work")
    value = inspect(root)
    (root / "vf-focus-result.json").write_text(json.dumps(value, indent=2))
    print(json.dumps(value, separators=(",", ":")))
