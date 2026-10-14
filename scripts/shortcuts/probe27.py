#!/usr/bin/env python3
"""Generate Q27 probe shortcuts that empirically verify iOS/macOS 27 action
parameter shapes the shortcuts-playground KB doesn't document.
Usage: probe27.py OUTDIR  -> writes <name>.xml (unsigned) for each probe."""
import plistlib, sys, uuid
from pathlib import Path

A = "is.workflow.actions."
FFFC = "￼"


def U():
    return str(uuid.uuid4()).upper()


def out(h): return {"Type": "ActionOutput", "OutputUUID": h["uuid"], "OutputName": h["name"]}
def var(name): return {"Type": "Variable", "VariableName": name}
EXT = {"Type": "ExtensionInput"}


def tokstr(*parts):
    s, att = "", {}
    for p in parts:
        if isinstance(p, str):
            s += p
        else:
            att["{%d, 1}" % len(s)] = p
            s += FFFC
    return {"Value": {"string": s, "attachmentsByRange": att}, "WFSerializationType": "WFTextTokenString"}


def tokref(ref): return {"Value": ref, "WFSerializationType": "WFTextTokenAttachment"}
def ifin(ref): return {"Type": "Variable", "Variable": tokref(ref)}


class S:
    def __init__(self, inputs=()):
        self.a, self.inputs = [], list(inputs)

    def add(self, ident, params=None, name="Result"):
        h = {"uuid": U(), "name": name}
        p = dict(params or {}); p["UUID"] = h["uuid"]
        self.a.append({"WFWorkflowActionIdentifier": A + ident, "WFWorkflowActionParameters": p})
        return h

    def text(self, *parts): return self.add("gettext", {"WFTextActionText": tokstr(*parts)}, "Text")

    def save(self, ref, path):
        return self.add("documentpicker.save", {"WFInput": tokref(ref), "WFAskWhereToSave": False,
                                                "WFSaveFileOverwrite": True, "WFFileDestinationPath": path},
                        "Saved File")

    def log(self, fname, *parts):
        self.save(out(self.text(*parts)), f"Brain Probe Log/{fname}")

    def plist(self):
        return {"WFWorkflowClientVersion": "4018.0.4", "WFWorkflowMinimumClientVersion": 900,
                "WFWorkflowMinimumClientVersionString": "900",
                "WFWorkflowIcon": {"WFWorkflowIconStartColor": 4282601983, "WFWorkflowIconGlyphNumber": 59511},
                "WFWorkflowTypes": [], "WFWorkflowInputContentItemClasses": self.inputs,
                "WFWorkflowImportQuestions": [], "WFWorkflowActions": self.a}


def probe_files():
    s = S()
    t = s.text("hello outbox")
    d = s.add("dictionary", {"WFItems": {"Value": {"WFDictionaryFieldValueItems": [
        {"WFItemType": 0, "WFKey": tokstr("text"), "WFValue": tokstr(out(t))},
        {"WFItemType": 0, "WFKey": tokstr("tag"), "WFValue": tokstr("idea")}]},
        "WFSerializationType": "WFDictionaryFieldValue"}}, "Dictionary")
    s.save(out(d), "Brain Outbox/111-222.json")
    s.save(out(s.text("second")), "Brain Outbox/333-444.txt")
    folder = s.add("documentpicker.open", {"WFGetFilePath": "Brain Outbox/", "WFFileErrorIfNotFound": False,
                                           "WFShowFilePicker": False}, "File")
    files = s.add("file.getfoldercontents", {"WFFolder": tokref(out(folder))}, "Contents of Folder")
    rng = files  # ponytail: no range cap — Items in Range errors when the list is shorter
    g = U()
    s.add("repeat.each", {"GroupingIdentifier": g, "WFControlFlowMode": 0, "WFInput": tokref(out(rng))})
    n = s.add("properties.files", {"WFInput": tokref(var("Repeat Item")), "WFContentItemPropertyName": "Name"}, "Name")
    e = s.add("properties.files", {"WFInput": tokref(var("Repeat Item")), "WFContentItemPropertyName": "File Extension"},
              "File Extension")
    s.log("seen.txt", "name=", out(n), " ext=", out(e))  # overwrite: last one wins; ponytail fine for a probe
    s.save(out(s.text(out(n), "|", out(e))), "Brain Probe Log/")  # also keep each
    s.add("file.delete", {"WFInput": tokref(var("Repeat Item")), "WFDeleteFileConfirmDeletion": False}, "Deleted")
    s.add("repeat.each", {"GroupingIdentifier": g, "WFControlFlowMode": 2})
    # If wrapper + named variable across End If
    abc = s.text("abc")
    gi = U()
    s.add("conditional", {"GroupingIdentifier": gi, "WFControlFlowMode": 0, "WFCondition": 100, "WFInput": ifin(out(abc))})
    s.add("setvariable", {"WFVariableName": "Branch", "WFInput": tokref(out(s.text("yes-branch")))})
    s.add("conditional", {"GroupingIdentifier": gi, "WFControlFlowMode": 1})
    s.add("setvariable", {"WFVariableName": "Branch", "WFInput": tokref(out(s.text("no-branch")))})
    s.add("conditional", {"GroupingIdentifier": gi, "WFControlFlowMode": 2})
    s.log("ifvar.txt", "branch=", var("Branch"))
    # ends-with condition (code 9) for the trailing-slash guard
    url = s.text("https://x.example/")
    gs = U()
    s.add("conditional", {"GroupingIdentifier": gs, "WFControlFlowMode": 0, "WFCondition": 9,
                          "WFConditionalActionString": "/", "WFInput": ifin(out(url))})
    s.log("slash.txt", "slash=yes")
    s.add("conditional", {"GroupingIdentifier": gs, "WFControlFlowMode": 1})
    s.log("slash.txt", "slash=no")
    s.add("conditional", {"GroupingIdentifier": gs, "WFControlFlowMode": 2})
    return s


def probe_image():
    s = S(["WFImageContentItem", "WFGenericFileContentItem"])
    r = s.add("image.resize", {"WFImage": tokref(EXT), "WFImageResizeWidth": "512"}, "Resized Image")
    c = s.add("image.convert", {"WFInput": tokref(out(r)), "WFImageFormat": "JPEG", "WFImageCompressionQuality": 0.8,
                                "WFImagePreserveMetadata": False}, "Converted Image")
    s.save(out(c), "Brain Probe Log/img.jpg")
    return s


def probe_ai():
    s = S(["WFImageContentItem", "WFGenericFileContentItem"])
    c = s.add("image.convert", {"WFInput": tokref(EXT), "WFImageFormat": "JPEG", "WFImageCompressionQuality": 0.8,
                                "WFImagePreserveMetadata": False}, "Converted Image")
    m = s.add("askllm", {"WFGenerativeResultType": "Text",
                         "WFLLMPrompt": tokstr("Describe this image in under 400 characters. Include any visible text.\n\n", out(c))},
              "Response")
    enc = s.add("urlencode", {"WFInput": tokref(out(m))}, "URL Encoded Text")
    s.log("ai.txt", out(m))
    s.log("ai-enc.txt", out(enc))
    return s


def probe_misc():
    s = S()
    s.add("delay", {"WFDelayTime": 1})
    shot = s.add("getlastscreenshot", {"WFGetLatestPhotoCount": 1}, "Latest Screenshots")
    k = s.add("count", {"WFCountType": "Items", "Input": tokref(out(shot)), "WFInput": tokref(out(shot))}, "Count")
    s.log("misc.txt", "shots=", out(k))
    s.add("output", {"WFOutput": tokstr("probe-output-ok")})
    return s


if __name__ == "__main__":
    o = Path(sys.argv[1]); o.mkdir(parents=True, exist_ok=True)
    for name, fn in {"Q27 ProbeA2": probe_files, "Q27 ProbeB": probe_image,
                     "Q27 ProbeC": probe_ai, "Q27 ProbeD": probe_misc}.items():
        (o / f"{name}.xml").write_bytes(plistlib.dumps(fn().plist()))
        print("wrote", o / f"{name}.xml")
