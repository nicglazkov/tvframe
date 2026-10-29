#!/usr/bin/env python3
"""Write real/curate_meta.json (photo id -> date) for the scoring run, from real/items.json."""
import json, os
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", "server"))
import tvconf  # noqa: E402

T = tvconf.ROOT
items = json.load(open(f"{T}/real/items.json"))
meta = {i["id"]: {"date": i["date"]} for i in items if i["kind"] == "photo" and i.get("date")}
json.dump(meta, open(f"{T}/real/curate_meta.json", "w"), indent=1)
