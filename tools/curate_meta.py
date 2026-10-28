#!/usr/bin/env python3
"""Write real/curate_meta.json (photo id -> date) for the scoring run, from real/items.json."""
import json, os

T = os.path.expanduser("~/tvframe")
items = json.load(open(f"{T}/real/items.json"))
meta = {i["id"]: {"date": i["date"]} for i in items if i["kind"] == "photo" and i.get("date")}
json.dump(meta, open(f"{T}/real/curate_meta.json", "w"), indent=1)
