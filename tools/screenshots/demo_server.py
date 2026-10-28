#!/usr/bin/env python3
"""Serve a demo library (make_demo.py) and a stand-in bridge with made-up weather, for screenshots.

    python3 demo_server.py <demo-dir> [--mode glance|guests] [--port-base 9150]

Listens on port-base+1 (prod.json and media, like serve.py) and port-base+4 (the bridge: /mode, /live, /now,
/cmd, /sonos). Point the app at it with adb reverse: tcp:8151 -> tcp:9151 and tcp:8154 -> tcp:9154, and build
the app with -Ptvframe.server=http://127.0.0.1. Nothing here talks to Home Assistant or the internet."""
import json, os, sys, threading, time
from datetime import datetime, timedelta
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

DEMO = os.path.abspath(sys.argv[1])
MODE = sys.argv[sys.argv.index("--mode") + 1] if "--mode" in sys.argv else "glance"
REV = 2 if MODE == "guests" else 1     # the app only switches when rev changes
BASE = int(sys.argv[sys.argv.index("--port-base") + 1]) if "--port-base" in sys.argv else 9150


def live():
    now = datetime.now().astimezone()
    day = lambda i: (now + timedelta(days=i)).date().isoformat()
    hours = [{"t": (now.replace(minute=0) + timedelta(hours=h)).strftime("%Y-%m-%dT%H:00"),
              "temp": 61 + round(6 * (1 - abs(h - 6) / 6)), "code": 1 if h % 5 else 2} for h in range(24)]
    return {
        "where": "Demo Valley", "fetched": now.isoformat(timespec="seconds"),
        "sources": {"weather": "demo", "air": "demo", "news": "demo"},
        "weather": {"temp": 64, "feels": 63, "code": 1, "is_day": 1, "text": "Mostly sunny", "hourly": hours,
                    "days": [{"date": day(i), "hi": 68 + i, "lo": 49 + i, "rain": [0, 10, 30][i], "text": t,
                              "sunrise": f"{day(i)}T07:0{i}", "sunset": f"{day(i)}T18:1{i}"}
                             for i, t in enumerate(["Mostly sunny", "Partly cloudy", "Light rain"])]},
        "air": {"aqi": 28, "pm25": 6.1, "hourly": [{"t": h["t"], "aqi": 26 + i % 7} for i, h in enumerate(hours)]},
        "sun": {"today": {"date": day(0), "sunrise": "07:00", "golden_end": "07:38", "golden_start": "17:31", "sunset": "18:10"},
                "tomorrow": {"date": day(1), "sunrise": "07:01", "golden_end": "07:39", "golden_start": "17:30", "sunset": "18:11"}},
        "news": {"source": "Sample news", "items": [{"title": t} for t in (
            "Botanical garden opens its new desert wing this weekend",
            "Night sky viewing at the observatory returns for the fall",
            "Farmers market adds a second weekday morning")]},
        "home": {"rows": [["Inside", "71°, 44% humidity"], ["Outside", "64°"]], "ok": True},
        "mode": {"mode": MODE, "rev": REV, "ha": True}}


class Media(SimpleHTTPRequestHandler):
    def __init__(self, *a, **k):
        super().__init__(*a, directory=DEMO, **k)

    def translate_path(self, path):
        p = path.split("?")[0]
        if p.startswith("/media/"):
            path = p[len("/media"):]
        return super().translate_path(path)

    def log_message(self, *a):
        pass


class Bridge(SimpleHTTPRequestHandler):
    def send_json(self, o, code=200):
        b = json.dumps(o).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        path, _, q = self.path.partition("?")
        args = dict(x.split("=", 1) for x in q.split("&") if "=" in x)
        if path == "/mode":
            if args.get("rev") == str(REV):
                time.sleep(min(25.0, float(args.get("wait", 0))))
            return self.send_json({"mode": MODE, "rev": REV, "source": "demo", "ha": True})
        if path == "/live":
            return self.send_json(live())
        if path in ("/cmd", "/sonos"):
            time.sleep(min(25.0, float(args.get("wait", 0))))
            return self.send_json({"seq": int(args.get("seq", 0)), "cmd": None} if path == "/cmd" else {"playing": False, "v": 0})
        if path == "/now":
            return self.send_json({"now": None})
        return self.send_json({"error": "not found"}, 404)

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        return self.send_json({"ok": True})

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    for port, h in ((BASE + 1, Media), (BASE + 4, Bridge)):
        threading.Thread(target=ThreadingHTTPServer(("127.0.0.1", port), h).serve_forever, daemon=True).start()
    print(f"demo on 127.0.0.1:{BASE + 1} (media) and :{BASE + 4} (bridge), mode {MODE}", flush=True)
    threading.Event().wait()
