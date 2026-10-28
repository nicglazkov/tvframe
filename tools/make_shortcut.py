import os, plistlib, sys, uuid
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "server"))
import tvconf  # noqa: E402
U = lambda: str(uuid.uuid4()).upper()
OBJ = "￼"

def out(uuid_, name):
    return {"Type": "ActionOutput", "OutputUUID": uuid_, "OutputName": name}

def att(v):
    return {"Value": v, "WFSerializationType": "WFTextTokenAttachment"}

def text(parts):
    """parts: strings or attachment dicts -> WFTextTokenString"""
    s, rng = "", {}
    for p in parts:
        if isinstance(p, str):
            s += p
        else:
            pos = len(s.encode("utf-16-le")) // 2
            rng[f"{{{pos}, 1}}"] = p
            s += OBJ
    return {"Value": {"string": s, "attachmentsByRange": rng}, "WFSerializationType": "WFTextTokenString"}

BASE = f"http://{tvconf.HOST}:8153"
a_list, a_choose, a_enc, a_name, a_encname, a_up = U(), U(), U(), U(), U(), U()
grp = U()
item = {"Type": "Variable", "VariableName": "Repeat Item"}
actions = [
    {"WFWorkflowActionIdentifier": "is.workflow.actions.downloadurl",
     "WFWorkflowActionParameters": {"UUID": a_list, "WFURL": BASE + "/albums", "WFHTTPMethod": "GET", "ShowHeaders": False}},
    {"WFWorkflowActionIdentifier": "is.workflow.actions.choosefromlist",
     "WFWorkflowActionParameters": {"UUID": a_choose, "WFInput": att(out(a_list, "Contents of URL")),
                                    "WFChooseFromListActionPrompt": "Which folder on the TV?"}},
    {"WFWorkflowActionIdentifier": "is.workflow.actions.urlencode",
     "WFWorkflowActionParameters": {"UUID": a_enc, "WFInput": text([out(a_choose, "Chosen Item")]), "WFEncodeMode": "Encode"}},
    {"WFWorkflowActionIdentifier": "is.workflow.actions.repeat.each",
     "WFWorkflowActionParameters": {"GroupingIdentifier": grp, "WFControlFlowMode": 0, "WFInput": att({"Type": "ExtensionInput"})}},
    {"WFWorkflowActionIdentifier": "is.workflow.actions.urlencode",
     "WFWorkflowActionParameters": {"UUID": a_encname, "WFEncodeMode": "Encode",
                                    "WFInput": text([dict(item, Aggrandizements=[{"Type": "WFPropertyVariableAggrandizement", "PropertyName": "Name"}])])}},
    {"WFWorkflowActionIdentifier": "is.workflow.actions.downloadurl",
     "WFWorkflowActionParameters": {"UUID": a_up, "WFHTTPMethod": "POST", "WFHTTPBodyType": "File", "ShowHeaders": False,
                                    "WFURL": text([BASE + "/upload?sniff=1&album=", out(a_enc, "URL Encoded Text"), "&name=", out(a_encname, "URL Encoded Text")]),
                                    "WFRequestVariable": att(item)}},
    {"WFWorkflowActionIdentifier": "is.workflow.actions.repeat.each",
     "WFWorkflowActionParameters": {"GroupingIdentifier": grp, "WFControlFlowMode": 2, "UUID": U()}},
    {"WFWorkflowActionIdentifier": "is.workflow.actions.notification",
     "WFWorkflowActionParameters": {"WFNotificationActionTitle": "Sent to the TV",
                                    "WFNotificationActionBody": text(["In ", out(a_choose, "Chosen Item"), ". They show up on the TV in about 5 minutes."]),
                                    "WFNotificationActionSound": False}},
]
wf = {
    "WFWorkflowClientVersion": "2607.0.2", "WFWorkflowMinimumClientVersion": 900, "WFWorkflowMinimumClientVersionString": "900",
    "WFWorkflowIcon": {"WFWorkflowIconStartColor": 463140863, "WFWorkflowIconGlyphNumber": 59511},
    "WFWorkflowImportQuestions": [], "WFWorkflowTypes": ["ActionExtension"],
    "WFWorkflowInputContentItemClasses": ["WFImageContentItem", "WFAVAssetContentItem", "WFPhotoMediaContentItem", "WFGenericFileContentItem"],
    "WFWorkflowHasShortcutInputVariables": True,
    "WFWorkflowOutputContentItemClasses": [], "WFQuickActionSurfaces": [],
    "WFWorkflowActions": actions,
}
plistlib.dump(wf, open("Send to TV.unsigned.shortcut", "wb"), fmt=plistlib.FMT_BINARY)
print("ok", len(actions))
