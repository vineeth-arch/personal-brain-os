#!/usr/bin/env python3
"""Repo gate for the plugin-built Brain shortcuts. Stdlib only.

    python3 scripts/shortcuts/check_shortcuts.py              # src/*.xml + dist/
    python3 scripts/shortcuts/check_shortcuts.py --dir DIR    # any folder of .xml

Every shortcut is built by the shortcuts-playground plugin (see README.md).
This script re-checks the things that plugin can't know about: no secret or
address baked in, import questions on the right actions, write-ahead outbox
order, an X-Capture-Key on every POST, and the plugin validator passing apart
from iOS 27 identifiers we verified ourselves in Probe27 (PROBE27.md).
"""
from __future__ import annotations

import plistlib
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
FFFC = "￼"
A = "is.workflow.actions."
OUTBOX = {"Brain Text", "Brain Share", "Brain Voice"}
SAVE_IDS = {A + "documentpicker.save"}  # ponytail: widen if PROBE27.md shows a different Save File id
UNKNOWN = re.compile(r"Unknown (?:action|AppIntent|third-party) identifier at index \d+: (\S+)")


def probed_ids() -> set[str]:
    md = HERE / "PROBE27.md"
    return set(re.findall(r"`((?:is\.workflow\.actions|com\.apple)\.[\w.]+)`", md.read_text())) if md.exists() else set()


def validator() -> Path:
    found = sorted(Path.home().glob(
        ".claude/plugins/cache/shortcuts-playground/shortcuts-playground/*/bin/validate-shortcut"))
    if not found:
        raise SystemExit("validate-shortcut not found: install the shortcuts-playground plugin")
    return found[-1]


def text_of(v) -> str:
    if isinstance(v, str):
        return v
    if isinstance(v, dict):
        inner = v.get("Value", v)
        if isinstance(inner, dict) and "string" in inner:
            return inner["string"]
    return ""


def header_items(params):
    h = params.get("WFHTTPHeaders") or {}
    for it in (h.get("Value") or {}).get("WFDictionaryFieldValueItems", []):
        yield text_of(it.get("WFKey")), text_of(it.get("WFValue"))


def check(xml: Path, dist: Path | None, probed: set[str]) -> list[str]:
    errs: list[str] = []
    data = plistlib.loads(xml.read_bytes())
    acts = data.get("WFWorkflowActions", [])
    ident = [a.get("WFWorkflowActionIdentifier", "") for a in acts]
    params = [a.get("WFWorkflowActionParameters", {}) for a in acts]

    # (a) every control-flow group opens once and closes once
    groups: dict[str, list[int]] = {}
    for p in params:
        if "GroupingIdentifier" in p and "WFControlFlowMode" in p:
            groups.setdefault(p["GroupingIdentifier"], []).append(p["WFControlFlowMode"])
    for gid, modes in groups.items():
        if modes.count(0) != 1 or modes.count(2) != 1:
            errs.append(f"(a) group {gid} has modes {modes}, need exactly one start (0) and one end (2)")

    # (b) import questions point at Text actions; Q1/Q2 hold only placeholders
    qs = data.get("WFWorkflowImportQuestions", [])
    if len(qs) < 2:
        errs.append("(b) expected at least 2 import questions (address, token)")
    for n, q in enumerate(qs):
        i = q.get("ActionIndex", -1)
        if not (0 <= i < len(acts)) or ident[i] != A + "gettext":
            errs.append(f"(b) import question {n} points at index {i}, which is not a Text action")
    if len(qs) >= 2 and all(0 <= q.get("ActionIndex", -1) < len(acts) for q in qs[:2]):
        q1 = text_of(params[qs[0]["ActionIndex"]].get("WFTextActionText"))
        q2 = text_of(params[qs[1]["ActionIndex"]].get("WFTextActionText"))
        if q1 != "https://":
            errs.append(f"(c) address Text action must be exactly 'https://', found {q1!r}")
        if q2 != "PASTE-TOKEN":
            errs.append("(c) token Text action must be exactly 'PASTE-TOKEN' (never a real token)")

    # (c) no address or token baked in anywhere else
    allowed = {id(params[q["ActionIndex"]]) for q in qs[:1] if 0 <= q.get("ActionIndex", -1) < len(acts)}
    for i, p in enumerate(params):
        if id(p) in allowed:
            continue
        if "https://" in plistlib.dumps(p).decode("utf-8", "replace"):
            errs.append(f"(c) literal https:// at action {i} ({ident[i]}): addresses come from the import question only")
        for k, v in header_items(p):
            if k.lower() == "authorization" and not v.startswith("Bearer " + FFFC):
                errs.append(f"(c) Authorization at action {i} is not 'Bearer <token variable>'")

    # (d) write-ahead: outbox shortcuts save before any network call
    if xml.stem in OUTBOX:
        first_save = next((i for i, x in enumerate(ident) if x in SAVE_IDS), None)
        first_net = next((i for i, x in enumerate(ident) if x == A + "downloadurl"), None)
        if first_save is None or (first_net is not None and first_save > first_net):
            errs.append(f"(d) save at {first_save} must come before first network call at {first_net}")

    # (e) every POST carries an idempotency key
    for i, (x, p) in enumerate(zip(ident, params)):
        if x == A + "downloadurl" and p.get("WFHTTPMethod") == "POST":
            if not any(k == "X-Capture-Key" for k, _ in header_items(p)):
                errs.append(f"(e) POST at action {i} has no X-Capture-Key header")

    # (f) plugin validator: clean, or only identifiers we verified in Probe27
    r = subprocess.run([str(validator()), str(xml)], capture_output=True, text=True)
    if r.returncode != 0:
        lines = [l[2:] for l in (r.stdout + r.stderr).splitlines() if l.startswith("- ")]
        bad = [l for l in lines if not ((m := UNKNOWN.search(l)) and m.group(1) in probed)]
        if bad or not lines:
            errs.append("(f) validator: " + ("; ".join(bad) if bad else (r.stdout + r.stderr).strip()))

    # (g) a signed copy sits in dist/
    if dist is not None:
        signed = dist / f"{xml.stem}.shortcut"
        if not signed.is_file() or signed.stat().st_size == 0 or signed.read_bytes()[:4] != b"AEA1":
            errs.append(f"(g) {signed.name} missing, empty or not signed (no AEA1 header)")
    return errs


def main() -> int:
    argv = sys.argv[1:]
    src = Path(argv[argv.index("--dir") + 1]) if "--dir" in argv else HERE / "src"
    dist = None if "--dir" in argv else HERE / "dist"
    files = sorted(src.glob("*.xml"))
    if not files:
        print(f"no .xml shortcuts in {src}")
        return 1
    probed, failed = probed_ids(), 0
    for f in files:
        errs = check(f, dist, probed)
        print(f"{'FAIL' if errs else 'ok  '} {f.name}")
        for e in errs:
            print(f"     {e}")
        failed += bool(errs)
    print(f"{len(files) - failed}/{len(files)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
