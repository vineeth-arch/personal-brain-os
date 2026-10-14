# Probe27: action shapes verified on macOS 27.0.1 (2026-10-14)

The shortcuts-playground knowledge base documents no parameters for the
actions below. Each shape here was generated, signed, imported into Shortcuts
on this Mac and **run** (`shortcuts run`), and its effect checked on disk.
Builders copy these shapes exactly (fresh UUIDs, rewired inputs).
Generator: `probe27.py` (run it with an output dir to regenerate the probes).

Notation: `tokref(X)` = `{"Value": <ref X>, "WFSerializationType": "WFTextTokenAttachment"}`,
`tokstr(...)` = `{"Value": {"string": "...￼...", "attachmentsByRange": {...}}, "WFSerializationType": "WFTextTokenString"}`,
`Repeat Item` ref = `{"Type": "Variable", "VariableName": "Repeat Item"}`,
Shortcut Input ref = `{"Type": "ExtensionInput"}`.

| Friendly | Identifier | Input key | Verified params | Result |
|---|---|---|---|---|
| save_file | `is.workflow.actions.documentpicker.save` | `WFInput` = tokref | `WFAskWhereToSave: false`, `WFSaveFileOverwrite: true`, `WFFileDestinationPath` = **tokstr** e.g. `"Brain Outbox/" + key + ".json"` (plain string also works) | file lands in iCloud Drive › Shortcuts › Brain Outbox, named exactly as the path |
| dictionary → file | `is.workflow.actions.dictionary` then save_file | — | `WFItems` = WFDictionaryFieldValue, items `WFItemType: 0`, key/value tokstr | saved file is valid JSON: `{"tag":"idea","text":"hello outbox"}` |
| get_folder | `is.workflow.actions.documentpicker.open` | — | `WFGetFilePath: "Brain Outbox/"`, `WFFileErrorIfNotFound: false`, `WFShowFilePicker: false` | returns the folder (path relative to the Shortcuts iCloud folder) |
| folder_contents | `is.workflow.actions.file.getfoldercontents` | `WFFolder` = tokref(get_folder) | (no others) | list of files |
| file_name | `is.workflow.actions.properties.files` | `WFInput` = tokref(Repeat Item) | `WFContentItemPropertyName: "Name"` | name **without** extension (`111-222`) |
| file_ext | `is.workflow.actions.properties.files` | `WFInput` = tokref(Repeat Item) | `WFContentItemPropertyName: "File Extension"` | extension without dot (`json`, `m4a`) |
| delete_files | `is.workflow.actions.file.delete` | `WFInput` = tokref(Repeat Item) | `WFDeleteFileConfirmDeletion: false` | deleted with no prompt |
| resize | `is.workflow.actions.image.resize` | `WFImage` = tokref | `WFImageResizeWidth: "2048"` (string) | resized (HEIC 512 test → 512 px) |
| convert_jpeg | `is.workflow.actions.image.convert` | `WFInput` = tokref | `WFImageFormat: "JPEG"`, `WFImageCompressionQuality: 0.8`, `WFImagePreserveMetadata: false` | real JPEG (sips: format jpeg), HEIC input OK |
| use_model | `is.workflow.actions.askllm` | image attached **inside** `WFLLMPrompt` tokstr | `WFGenerativeResultType: "Text"`; **omit `WFLLMModel`** (system default); output name `Response` | image described correctly (matches Apple's own gallery shortcuts, client 4018.0.4) |
| url_encode | `is.workflow.actions.urlencode` | `WFInput` = **tokstr** (NOT tokref, NOT implicit) | — | `a b&c=d/é` → `a%20b%26c%3Dd/%C3%A9`. tokref / implicit / `WFEncodeMode` variants return **empty** |
| latest_screenshot | `is.workflow.actions.getlastscreenshot` | — | `WFGetLatestPhotoCount: 1` | 1 screenshot |
| wait | `is.workflow.actions.delay` | — | `WFDelayTime: 1` | ok |
| count | `is.workflow.actions.count` | `Input` and `WFInput` = tokref (same) | `WFCountType: "Items"` | ok |
| output | `is.workflow.actions.output` | — | `WFOutput` = tokstr | `shortcuts run -o file` receives it |
| set_variable | `is.workflow.actions.setvariable` | `WFInput` = tokref | `WFVariableName` | **a named variable set inside both If branches is readable after End If** (`{"Type":"Variable","VariableName":"Branch"}`) |
| if | `is.workflow.actions.conditional` | `WFInput` = `{"Type":"Variable","Variable": tokref(X)}` | code 100 has-any-value; code 9 ends-with + `WFConditionalActionString` | both branches and Otherwise behave correctly |
| random | `is.workflow.actions.number.random` | — | `WFRandomNumberMinimum/Maximum` | keys like `245610158-821554774` |

## Traps found by the probes
- **Get Items from List › Items in Range 1–10 errors** when the list is shorter ("outside the possible range"). Never cap a loop with it. Loop the whole folder.
- **URL Encode silently returns empty** unless its input is a text token string (see table).
- **Use Model**: a model failure (Apple Intelligence off, refusal) is a run error, not empty output.

## Not verifiable headless on the Mac
`recordaudio` (`WFRecordingStart: "Immediately"`, `WFRecordingCompression: "Normal"`) and `selectphoto`
(`WFSelectMultiplePhotos: true`) need a human. They are checked in the phone test list.
All identifiers above are on the plugin's v63 allowlist, so the validator reports no unknown IDs.
