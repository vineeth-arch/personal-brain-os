#!/usr/bin/env python3
"""Build the three Brain Cockpit iOS Shortcuts as importable .shortcut files.

Python stdlib only (plistlib + the macOS `shortcuts` CLI for signing). The
plists are generated, not hand-written: the Shortcuts format nests "token
strings" and variable references deeply, and one wrong key silently yields a
blank field on the phone rather than an import error.

    python3 scripts/shortcuts/build.py                 # -> scripts/shortcuts/dist/
    python3 scripts/shortcuts/build.py --test URL TOKEN --out DIR
        # bakes URL+TOKEN in and skips every prompt, so `shortcuts run` can
        # drive a shortcut against a local server. Never commit these.

The committed shortcuts hold NO secret: the server address and access token
are import questions the phone asks once. See README.md.

Server contract (api/main.py): POST /api/capture takes JSON; /capture/audio
and /capture/image take the RAW file as the body plus ?tag=&name=&insight=
query params. An error is {"error": {"what", "cause", "todo"}}.
"""
from __future__ import annotations

import plistlib
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
FFFC = "￼"  # placeholder char a token string uses for each embedded variable
# most-used first; the server accepts exactly these ten (or no tag at all)
TAGS = ["idea", "todo", "resource", "journal", "learning", "person",
        "decision", "project", "musing", "conversation"]
EXT_INPUT = {"Type": "ExtensionInput"}  # "Shortcut Input"


def out(h):
    return {"Type": "ActionOutput", "OutputUUID": h["uuid"], "OutputName": h["name"]}


def tokstr(*parts):
    """A text field mixing literals (str) and variable refs (dict)."""
    s, att = "", {}
    for p in parts:
        if isinstance(p, str):
            assert all(ord(c) < 0x10000 for c in p), "attachment offsets are UTF-16 based"
            s += p
        else:
            att["{%d, 1}" % len(s)] = p
            s += FFFC
    return {"Value": {"string": s, "attachmentsByRange": att},
            "WFSerializationType": "WFTextTokenString"}


def tokref(ref):
    """A field whose whole value is one variable / action output."""
    return {"Value": ref, "WFSerializationType": "WFTextTokenAttachment"}


def fields(pairs):
    """Header / JSON-body dictionary: [(key, value-parts...)] -> field value."""
    items = [{"WFItemType": 0, "WFKey": tokstr(k), "WFValue": tokstr(*v)} for k, v in pairs]
    return {"Value": {"WFDictionaryFieldValueItems": items},
            "WFSerializationType": "WFDictionaryFieldValue"}


class Shortcut:
    def __init__(self, share=False):
        self.actions = []
        self.named = {}
        self.share = share

    def add(self, ident, params=None, name="Result"):
        h = {"uuid": str(uuid.uuid4()).upper(), "name": name}
        p = dict(params or {})
        p["UUID"] = h["uuid"]
        self.actions.append({"WFWorkflowActionIdentifier": "is.workflow.actions." + ident,
                             "WFWorkflowActionParameters": p})
        return h

    def text(self, value):
        return self.add("gettext", {"WFTextActionText": value}, "Text")

    def setvar(self, name, handle):
        """Name an action's output for later use. Deliberately NOT a Set
        Variable action: named-variable references resolved to empty in
        `shortcuts run` tests, while a direct action-output reference works."""
        self.named[name] = out(handle)

    def v(self, name):
        return self.named[name]

    def notify(self, *body):
        self.add("notification", {"WFNotificationActionTitle": "Brain Cockpit",
                                  "WFNotificationActionBody": tokstr(*body)})

    def prologue(self, test):
        """Actions 0 and 1 are the import-question targets — keep them first."""
        # No tidying pass over these two: a Replace Text action anywhere in
        # the chain wedges the runner (it never returns and reports nothing),
        # so the answers are used exactly as typed. Hence "no trailing slash"
        # in the import question and the README.
        self.setvar("BaseURL", self.text(test[0] if test else "https://"))
        self.setvar("Token", self.text(test[1] if test else ""))

    def ask(self, prompt, test, fixed):
        if test:
            return self.text(fixed)
        return self.add("ask", {"WFAskActionPrompt": prompt, "WFInputType": "Text",
                                "WFAskActionAllowsMultilineText": True}, "Provided Input")

    def pick_tag(self, test):
        if test:
            return self.text("idea")
        tags = self.add("list", {"WFItems": TAGS}, "List")
        return self.add("choosefromlist", {"WFInput": tokref(out(tags)),
                                           "WFChooseFromListActionPrompt": "Tag"}, "Chosen Item")

    def capture_key(self):
        """Idempotency key: same value on both attempts, so a retry whose first
        try silently landed returns the same note instead of a duplicate.

        Two random numbers rather than a timestamp: Format Date hangs the
        runner outright (it never returns, with no error), while Random Number
        is well behaved. ~10^17 of space is far more than enough to keep two
        different captures from colliding."""
        a = self.add("number.random", {"WFRandomNumberMinimum": 100000000,
                                       "WFRandomNumberMaximum": 999999999}, "Random Number")
        b = self.add("number.random", {"WFRandomNumberMinimum": 100000000,
                                       "WFRandomNumberMaximum": 999999999}, "Random Number")
        self.setvar("CaptureKey", self.add("gettext", {"WFTextActionText": tokstr(out(a), "-", out(b))}, "Text"))

    def post(self, url_parts, body_params, content_type=None):
        """POST, then show the server's own reply.

        Deliberately flat — no If, no retry. An If action serialised by hand
        renders as "Please choose a value for each parameter" and stops the
        run dead, and Shortcuts has neither try/catch nor a readable status
        code, so there is nothing sound to branch on anyway. Showing the raw
        reply never lies: a success reads {"id": …, "status": "captured"} and
        a failure carries the server's own what/cause/todo sentences."""
        headers = [("Authorization", ["Bearer ", self.v("Token")]),
                   ("X-Capture-Key", [self.v("CaptureKey")])]
        if content_type:
            headers.append(("Content-Type", [content_type]))
        resp = self.add("downloadurl", {"WFURL": tokstr(*url_parts), "WFHTTPMethod": "POST",
                                        "WFHTTPHeaders": fields(headers), **body_params},
                        "Contents of URL")
        self.notify(out(resp))

    def plist(self, questions, icon_color):
        return {
            "WFWorkflowClientVersion": "2605.0.5",
            "WFWorkflowMinimumClientVersion": 900,
            "WFWorkflowMinimumClientVersionString": "900",
            "WFWorkflowIcon": {"WFWorkflowIconStartColor": icon_color, "WFWorkflowIconGlyphNumber": 59511},
            "WFWorkflowTypes": ["ActionExtension"] if self.share else [],
            "WFWorkflowInputContentItemClasses": (
                ["WFURLContentItem", "WFSafariWebPageContentItem", "WFStringContentItem",
                 "WFRichTextContentItem", "WFArticleContentItem", "WFAVAssetContentItem",
                 "WFGenericFileContentItem"]
                if self.share else []),
            "WFWorkflowImportQuestions": questions,
            "WFWorkflowActions": self.actions,
        }


def import_questions(test):
    if test:
        return []
    return [
        {"ActionIndex": 0, "Category": "Parameter", "ParameterKey": "WFTextActionText",
         "Text": "What is your Brain Cockpit address? (e.g. https://your-cockpit.example.com)",
         "DefaultValue": "https://"},
        {"ActionIndex": 1, "Category": "Parameter", "ParameterKey": "WFTextActionText",
         "Text": "Paste your Brain Cockpit access token (api.auth_token in config.json).",
         "DefaultValue": ""},
    ]


def json_body(**pairs):
    return {"WFHTTPBodyType": "JSON", "WFJSONValues": fields([(k, v) for k, v in pairs.items()])}


def build_text(test):
    """C — quick text: type or dictate a thought, pick a tag, send."""
    s = Shortcut()
    s.prologue(test)
    thought = s.ask("Capture a thought", test, "shortcut test")
    tag = s.pick_tag(test)
    s.capture_key()
    s.post([s.v("BaseURL"), "/api/capture"],
                      json_body(text=[out(thought)], tag=[out(tag)]))
    return s.plist(import_questions(test), 4282601983)


def build_voice(test):
    """B — Action Button: record, send. No prompts and no tag, so it can run
    from a locked phone (any prompt needs an unlock); the classifier routes it."""
    s = Shortcut()
    s.prologue(test)
    if test:  # the test rig feeds a file in instead of recording
        # declare a file input so `shortcuts run -i some.m4a` populates
        # Shortcut Input; the shipped shortcut records instead and takes none
        s.share = True
        audio = tokref(EXT_INPUT)
    else:
        rec = s.add("recordaudio", {"WFRecordingStart": "Immediately", "WFRecordingCompression": "Normal"},
                    "Recorded Audio")
        audio = tokref(out(rec))
    s.capture_key()
    s.post([s.v("BaseURL"), "/api/capture/audio"],
                      {"WFHTTPBodyType": "File", "WFRequestVariable": audio}, "audio/m4a")
    return s.plist(import_questions(test), 4292093695)


def build_share(test):
    """A — share sheet, for links, Safari pages and selected text.

    Links and text only. A photo would need the raw-file upload path plus an
    If to tell the two apart, and an If serialised by hand breaks the run —
    so photos stay with the cockpit's own camera button in the PWA, which
    already resizes and uploads them. The declared input classes leave images
    out, so iOS does not offer this shortcut for a photo in the first place."""
    s = Shortcut(share=True)
    s.prologue(test)
    thought = s.ask("Add a thought?", test, "shortcut test")
    tag = s.pick_tag(test)
    s.capture_key()
    # thought first, then the link: the shape the pipeline's link detection expects
    s.post([s.v("BaseURL"), "/api/capture"],
           json_body(text=[out(thought), "\n\n", EXT_INPUT], tag=[out(tag)]))
    return s.plist(import_questions(test), 4292093695)


BUILDS = {"Brain Text": build_text, "Brain Voice": build_voice, "Brain Share": build_share}


def sign(data: dict, dest: Path):
    """`shortcuts sign` returns before it has finished writing, and a second
    sign started in that window loses the first one's output — so wait for the
    file to actually appear, keeping the input alive until it does."""
    dest.unlink(missing_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "in.shortcut"  # the CLI rejects an input without this extension
        src.write_bytes(plistlib.dumps(data, fmt=plistlib.FMT_BINARY))
        subprocess.run(["shortcuts", "sign", "--mode", "anyone", "-i", str(src), "-o", str(dest)], check=True)
        for _ in range(100):
            if dest.exists() and dest.stat().st_size > 0:
                return
            time.sleep(0.1)
    raise SystemExit(f"shortcuts sign reported success but never wrote {dest}")


def main() -> int:
    argv = sys.argv[1:]
    test = None
    if "--test" in argv:
        i = argv.index("--test")
        test = (argv[i + 1], argv[i + 2])
    out_dir = Path(argv[argv.index("--out") + 1]) if "--out" in argv else HERE / "dist"
    out_dir.mkdir(parents=True, exist_ok=True)
    for name, build in BUILDS.items():
        data = build(test)
        # the readable XML is what a reviewer diffs; skipped for test rigs (they hold a token)
        if not test:
            (HERE / "src").mkdir(exist_ok=True)
            (HERE / "src" / f"{name}.plist").write_bytes(plistlib.dumps(data, fmt=plistlib.FMT_XML))
        dest = out_dir / f"{name}.shortcut"
        sign(data, dest)
        print(f"wrote {dest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
