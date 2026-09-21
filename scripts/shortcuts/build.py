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

    def trim(self, ref, regex):
        """Strip pasted whitespace / trailing slashes with a regex replace."""
        return self.add("text.replace", {
            "WFInput": tokstr(ref), "WFReplaceTextFind": regex, "WFReplaceTextReplace": "",
            "WFReplaceTextRegularExpression": True}, "Updated Text")

    def cond(self, mode, gid, ref=None):
        p = {"GroupingIdentifier": gid, "WFControlFlowMode": mode}
        if ref is not None:  # 100 = "has any value"
            p.update({"WFCondition": 100, "WFInput": {"Type": "Variable", "Variable": tokref(ref)}})
        self.add("conditional", p)

    def if_value(self, ref, then, otherwise):
        gid = str(uuid.uuid4()).upper()
        self.cond(0, gid, ref)
        then()
        self.cond(1, gid)
        otherwise()
        self.cond(2, gid)

    def notify(self, *body):
        self.add("notification", {"WFNotificationActionTitle": "Brain Cockpit",
                                  "WFNotificationActionBody": tokstr(*body)})

    def prologue(self, test):
        """Actions 0 and 1 are the import-question targets — keep them first."""
        base = self.text(test[0] if test else "https://")
        token = self.text(test[1] if test else "")
        self.setvar("BaseURL", self.trim(out(base), r"^\s+|[\s/]+$"))
        self.setvar("Token", self.trim(out(token), r"^\s+|\s+$"))

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
        try silently landed returns the same note instead of a duplicate."""
        now = self.add("date", {}, "Date")
        fmt = self.add("format.date", {"WFDate": tokref(out(now)), "WFDateFormatStyle": "Custom",
                                       "WFDateFormat": "yyyyMMddHHmmss"}, "Formatted Date")
        rnd = self.add("number.random", {"WFRandomNumberMinimum": 100000,
                                         "WFRandomNumberMaximum": 999999999}, "Random Number")
        self.setvar("CaptureKey", self.add("gettext", {"WFTextActionText": tokstr(out(fmt), "-", out(rnd))}, "Text"))

    def post_with_retry(self, url_parts, body_params, content_type=None):
        """POST, then once more after 2s if no note id came back, then notify.
        A network failure aborts the shortcut with iOS's own alert — Shortcuts
        has no try/catch — but a server error comes back as JSON and lands in
        the notification, showing the server's own `todo` sentence."""
        headers = [("Authorization", ["Bearer ", self.v("Token")]), ("X-Capture-Key", [self.v("CaptureKey")])]
        if content_type:
            headers.append(("Content-Type", [content_type]))

        def post():
            return self.add("downloadurl", {"WFURL": tokstr(*url_parts), "WFHTTPMethod": "POST",
                                            "WFHTTPHeaders": fields(headers), **body_params},
                            "Contents of URL")

        def note_id(resp):
            return self.add("getvalueforkey", {"WFInput": tokref(out(resp)), "WFDictionaryKey": "id",
                                               "WFGetDictionaryValueType": "Value"}, "Dictionary Value")

        def second_try():
            self.add("delay", {"WFDelayTime": 2})
            resp = post()
            self.if_value(note_id(resp), lambda: self.notify("Captured."), lambda: report(resp))

        def report(resp):
            err = self.add("getvalueforkey", {"WFInput": tokref(out(resp)), "WFDictionaryKey": "error",
                                              "WFGetDictionaryValueType": "Value"}, "Dictionary Value")
            todo = self.add("getvalueforkey", {"WFInput": tokref(out(err)), "WFDictionaryKey": "todo",
                                               "WFGetDictionaryValueType": "Value"}, "Dictionary Value")
            self.if_value(todo, lambda: self.notify(out(todo)),
                          lambda: self.notify("Couldn't capture that. Check the server address and token, then try again."))

        first = post()
        self.if_value(note_id(first), lambda: self.notify("Captured."), second_try)

    def plist(self, questions, icon_color):
        return {
            "WFWorkflowClientVersion": "2605.0.5",
            "WFWorkflowMinimumClientVersion": 900,
            "WFWorkflowMinimumClientVersionString": "900",
            "WFWorkflowIcon": {"WFWorkflowIconStartColor": icon_color, "WFWorkflowIconGlyphNumber": 59511},
            "WFWorkflowTypes": ["ActionExtension"] if self.share else [],
            "WFWorkflowInputContentItemClasses": (
                ["WFImageContentItem", "WFURLContentItem", "WFSafariWebPageContentItem",
                 "WFStringContentItem", "WFRichTextContentItem", "WFArticleContentItem"]
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
    s.post_with_retry([s.v("BaseURL"), "/api/capture"],
                      json_body(text=[out(thought)], tag=[out(tag)]))
    return s.plist(import_questions(test), 4282601983)


def build_voice(test):
    """B — Action Button: record, send. No prompts and no tag, so it can run
    from a locked phone (any prompt needs an unlock); the classifier routes it."""
    s = Shortcut()
    s.prologue(test)
    if test:  # the test rig feeds a file in instead of recording
        audio = tokref(EXT_INPUT)
    else:
        rec = s.add("recordaudio", {"WFRecordingStart": "Immediately", "WFRecordingCompression": "Normal"},
                    "Recorded Audio")
        audio = tokref(out(rec))
    s.capture_key()
    s.post_with_retry([s.v("BaseURL"), "/api/capture/audio"],
                      {"WFHTTPBodyType": "File", "WFRequestVariable": audio}, "audio/m4a")
    return s.plist(import_questions(test), 4292093695)


def build_share(test):
    """A — share sheet: an image goes as a raw JPEG, a link or text as JSON."""
    s = Shortcut(share=True)
    s.prologue(test)
    images = s.add("detect.images", {"WFInput": tokref(EXT_INPUT)}, "Images")

    def image_branch():
        jpeg = s.add("image.convert", {"WFInput": tokref(out(images)), "WFImageFormat": "JPEG",
                                       "WFImagePreserveMetadata": False}, "Converted Image")
        small = s.add("image.resize", {"WFImage": tokref(out(jpeg)), "WFImageResizeKey": "Width",
                                       "WFImageResizeWidth": "2048"}, "Resized Image")
        thought = s.ask("Add a thought?", test, "shortcut test")
        tag = s.pick_tag(test)
        enc = s.add("urlencode", {"WFInput": tokref(out(thought)), "WFEncodeMode": "Encode"}, "URL Encoded")
        s.capture_key()
        s.post_with_retry(
            [s.v("BaseURL"), "/api/capture/image?tag=", out(tag), "&name=", out(enc), "&insight=", out(enc)],
            {"WFHTTPBodyType": "File", "WFRequestVariable": tokref(out(small))}, "image/jpeg")

    def text_branch():
        thought = s.ask("Add a thought?", test, "shortcut test")
        tag = s.pick_tag(test)
        s.capture_key()
        # thought first, then the link: the shape the pipeline's link detection expects
        s.post_with_retry([s.v("BaseURL"), "/api/capture"],
                          json_body(text=[out(thought), "\n\n", EXT_INPUT], tag=[out(tag)]))

    s.if_value(images, image_branch, text_branch)
    return s.plist(import_questions(test), 4292093695)


BUILDS = {"Brain Text": build_text, "Brain Voice": build_voice, "Brain Share": build_share}


def sign(data: dict, dest: Path):
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "in.shortcut"  # the CLI rejects an input without this extension
        src.write_bytes(plistlib.dumps(data, fmt=plistlib.FMT_BINARY))
        subprocess.run(["shortcuts", "sign", "--mode", "anyone", "-i", str(src), "-o", str(dest)], check=True)


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
