#!/usr/bin/env python3
"""TV Frame bridge: live data for the TV's Glance screen (stdlib only).

The TV app talks only to this bridge, never to Home Assistant, so the HA token stays on the server
(HA_TOKEN_FILE in config.env, mode 0600). Without HA_URL the bridge still serves everything but the home states.

  GET  /live                   site/data/live.json plus real home states from HA and the guest Wi-Fi QR
  GET  /mode                   always {"mode":"glance"}: Guests mode is gone (2026-10-26), kept for older apps
  GET  /status                 health text
  GET  /sonos?v=N&wait=S       what the Sonos speakers play (long-polls until it changes from version N); Quiet Home's card
  GET  /art?e=ENTITY           that speaker's album art, fetched from HA so the TV never needs a token
  POST /now                    the TV says what is on screen: {role, id, kind, members}; also a heartbeat every minute
  GET  /now                    that, with its age, for the photo manager's "Now on TV"
  POST /cmd    {cmd}           next or prev, from the photo manager
  GET  /cmd?seq=N&wait=S       the TV long-polls this while a show is up and acts on commands newer than N

HA changes arrive over the HA websocket (state triggers), with a REST re-read every 60 s as a safety net.
"""
import argparse, json, os, sys, threading, time, urllib.request, zlib
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from zoneinfo import ZoneInfo

HOME = os.path.expanduser("~")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tvconf  # noqa: E402
sys.path.insert(0, os.path.join(tvconf.ROOT, "tools"))
import ha_ws  # noqa: E402

LA = ZoneInfo(tvconf.TZ)
HA_URL = tvconf.HA_URL
TOKEN = tvconf.ha_token()
LIVE = os.path.join(tvconf.ROOT, "site", "data", "live.json")
STATE_FILE = os.path.join(HOME, ".config", "tvframe", "bridge_state.json")
WIFI_QR = os.path.join(HOME, ".config", "tvframe", "wifi_qr.json")   # {"ssid", "qr": rows of 0/1}, mode 0600
# Weather codes (WMO) that mean rain, drizzle, showers or thunder.
RAIN_CODES = set(range(51, 68)) | {80, 81, 82, 95, 96, 99}
# The Home tile shows only things that read fine with guests over: how it feels inside, what is playing, and
# laundry while it runs. Which Home Assistant entity plays each part is set per home in a JSON file
# (HA_ENTITIES in config.env; see home_entities.example.json). Parts without an entity are left out.
HOME_ENTITIES = tvconf.get_json("HA_ENTITIES_JSON", None) or (lambda p: __import__("json").load(open(p)) if os.path.exists(p) else {})(
    tvconf.path("HA_ENTITIES", "~/.config/tvframe/home_entities.json"))
# The speakers the Quiet Home card follows.
SONOS = ("music", "music2")

cond = threading.Condition()
state = {"mode": "glance", "rev": 1, "changed": None, "source": "default", "ha_ok": False, "ha_seen": None}
home = {}
sonos = {"v": 1, "data": {"playing": False}}
now_on = {"at": 0, "data": None}
cmds = {"seq": 0, "cmd": None, "at": 0}


def log(*a):
    print(datetime.now(LA).strftime("%m-%d %H:%M:%S"), *a, flush=True)


def save():
    try:
        tmp = STATE_FILE + ".tmp"
        json.dump({"mode": state["mode"], "rev": state["rev"], "changed": state["changed"]}, open(tmp, "w"))
        os.replace(tmp, STATE_FILE)
    except Exception as e:
        log("save failed", e)


def load():
    try:
        s = json.load(open(STATE_FILE))
        state.update(mode="glance", rev=int(s.get("rev", 1)), changed=s.get("changed"), source="fixed")
    except Exception:
        pass


def ha(method, path, body=None):
    req = urllib.request.Request(HA_URL + "/api/" + path, method=method,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Authorization": "Bearer " + TOKEN, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=8) as r:
        return json.loads(r.read() or b"null")


def home_update(eid, s):
    if not s:
        return
    for k, e in HOME_ENTITIES.items():
        if e == eid:
            home[k] = {"state": s.get("state"), "attributes": s.get("attributes", {}),
                       "updated": s.get("last_updated")}
            if k in SONOS:
                sonos_update()


def sonos_now():
    """The Sonos speaker that is playing, as the card shows it. Grouped speakers play the same thing, so the
    first one playing wins. Art goes through /art so the TV never sees an HA token."""
    for k in SONOS:
        m = home.get(k) or {}
        a = m.get("attributes", {})
        if m.get("state") == "playing" and a.get("media_title"):
            eid = HOME_ENTITIES[k]
            pic = a.get("entity_picture") or ""
            return {"playing": True, "title": a.get("media_title"), "artist": a.get("media_artist") or "",
                    "album": a.get("media_album_name") or "", "room": a.get("friendly_name") or "Sonos",
                    "art": f"/art?e={eid}&c={zlib.crc32(pic.encode())}" if pic else None}
    return {"playing": False}


def sonos_update():
    d = sonos_now()
    if d != sonos["data"]:
        with cond:
            sonos["data"] = d
            sonos["v"] += 1
            cond.notify_all()


def rest_sync():
    """Full re-read; used at start and every 60 s."""
    try:
        for e in HOME_ENTITIES.values():
            home_update(e, ha("GET", "states/" + e))
        state["ha_ok"] = True
        state["ha_seen"] = time.time()
    except Exception as e:
        if state["ha_ok"]:
            log("HA unreachable:", e)
        state["ha_ok"] = False


def rest_loop():
    while True:
        rest_sync()
        time.sleep(60)


def ws_loop():
    ids = list(HOME_ENTITIES.values())
    while True:
        try:
            w = ha_ws.WS()
            w.recv()
            w.send({"type": "auth", "access_token": TOKEN})
            if w.recv().get("type") != "auth_ok":
                raise RuntimeError("auth failed")
            w.send({"id": 1, "type": "subscribe_trigger", "trigger": {"platform": "state", "entity_id": ids}})
            w.s.settimeout(None)
            log("HA websocket subscribed")
            rest_sync()
            while True:
                msg = w.recv()
                if msg.get("type") != "event":
                    continue
                t = (msg.get("event") or {}).get("variables", {}).get("trigger", {})
                eid, new = t.get("entity_id"), t.get("to_state")
                state["ha_ok"] = True
                state["ha_seen"] = time.time()
                home_update(eid, new)
        except Exception as e:
            log("HA websocket down:", e)
            time.sleep(10)


def temp(v):
    try:
        return round(float(v))
    except Exception:
        return None


def rain_soon(live):
    """'now' or 'around 8 PM' when the forecast has rain in the next 3 hours, else None."""
    w = live.get("weather") or {}
    if w.get("code") in RAIN_CODES:
        return "now"
    now = datetime.now(LA).replace(tzinfo=None)
    for h in w.get("hourly") or []:
        try:
            t = datetime.fromisoformat(h["t"])
        except (KeyError, ValueError):
            continue
        if now.replace(minute=0, second=0, microsecond=0) < t <= now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=3) \
                and h.get("code") in RAIN_CODES:
            return "around " + t.strftime("%-I %p")
    return None


def home_rows(live):
    """Plain rows for the glance Home tile, only from real states. Empty when HA has nothing.
    Inside and outside always; rain, music and laundry only while they matter. At most 5 rows."""
    def st(k):
        v = (home.get(k) or {}).get("state")
        return None if v in (None, "", "unavailable", "unknown") else v

    rows = []
    inside = temp(st("inside"))
    if inside is None:
        inside = temp((home.get("thermostat") or {}).get("attributes", {}).get("current_temperature"))
    hum = temp(st("humidity"))
    if inside is not None:
        rows.append(["Inside", f"{inside}°" + (f", {hum}% humidity" if hum is not None else "")])
    out = (live.get("weather") or {}).get("temp")
    if out is not None:
        rows.append(["Outside", f"{out}°"])
    r = rain_soon(live)
    if r:
        rows.append(["Rain", r])
    m = home.get("music") or {}
    if m.get("state") == "playing":
        a = m.get("attributes", {})
        song = " · ".join(x for x in [a.get("media_title"), a.get("media_artist")] if x)
        if song:
            rows.append(["Playing", song])
    for k, label in (("washer", "Washer"), ("dryer", "Dryer")):
        state_ = st(k)
        left = temp(st(k + "_left"))
        if st(k + "_done") == "on":
            rows.append([label, "Done"])
        elif state_ and state_.lower() not in ("off", "idle", "standby"):
            rows.append([label, f"{max(1, round(left / 60))} min left" if left else state_])
    return rows[:5]


class H(BaseHTTPRequestHandler):
    server_version = "tvframe-bridge/1"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *a):
        pass

    def send_json(self, obj, code=200):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Access-Control-Allow-Origin", "*")     # the photo manager on :8150
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(b)

    def mode_obj(self):
        return {"mode": state["mode"], "rev": state["rev"], "changed": state["changed"],
                "source": state["source"], "ha": state["ha_ok"]}

    def do_GET(self):
        path, _, q = self.path.partition("?")
        args = dict(x.split("=", 1) for x in q.split("&") if "=" in x)
        if path == "/mode":
            try:
                rev, wait = int(args.get("rev", -1)), min(30.0, float(args.get("wait", 0)))
            except ValueError:
                rev, wait = -1, 0
            end = time.time() + wait
            with cond:
                while state["rev"] == rev and time.time() < end:
                    cond.wait(end - time.time())
            return self.send_json(self.mode_obj())
        if path == "/live":
            try:
                live = json.load(open(LIVE))
            except Exception:
                live = {}
            live["home"] = {"rows": home_rows(live), "ok": state["ha_ok"]}
            try:
                live["wifi"] = json.load(open(WIFI_QR))
            except Exception:
                pass
            live["mode"] = self.mode_obj()
            return self.send_json(live)
        if path == "/sonos":
            try:
                v, wait = int(args.get("v", -1)), min(30.0, float(args.get("wait", 0)))
            except ValueError:
                v, wait = -1, 0
            end = time.time() + wait
            with cond:
                while sonos["v"] == v and time.time() < end:
                    cond.wait(end - time.time())
            return self.send_json(dict(sonos["data"], v=sonos["v"]))
        if path == "/art":
            k = next((k for k in SONOS if HOME_ENTITIES.get(k) == args.get("e")), None)
            pic = ((home.get(k) or {}).get("attributes") or {}).get("entity_picture") if k else None
            if not pic:
                return self.send_json({"error": "no art"}, 404)
            try:
                req = urllib.request.Request(HA_URL + pic, headers={"Authorization": "Bearer " + TOKEN})
                with urllib.request.urlopen(req, timeout=8) as r:
                    b, ctype = r.read(), r.headers.get("Content-Type", "image/jpeg")
            except Exception as e:
                return self.send_json({"error": str(e)[:100]}, 502)
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(b)))
            self.send_header("Cache-Control", "max-age=3600")
            self.end_headers()
            return self.wfile.write(b)
        if path == "/now":
            return self.send_json({"now": now_on["data"], "age": round(time.time() - now_on["at"]) if now_on["at"] else None})
        if path == "/cmd":
            try:
                seq, wait = int(args.get("seq", -1)), min(30.0, float(args.get("wait", 0)))
            except ValueError:
                seq, wait = -1, 0
            end = time.time() + wait
            with cond:
                while cmds["seq"] == seq and time.time() < end:
                    cond.wait(end - time.time())
            return self.send_json(cmds)
        if path == "/status":
            b = f"ok mode={state['mode']} rev={state['rev']} ha={'up' if state['ha_ok'] else 'down'}\n".encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            return self.wfile.write(b)
        self.send_json({"error": "not found"}, 404)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_POST(self):
        path = self.path.partition("?")[0]
        try:
            n = int(self.headers.get("Content-Length") or 0)
            b = json.loads(self.rfile.read(min(n, 65536)) or b"{}")
        except Exception:
            return self.send_json({"ok": False, "error": "bad json"}, 400)
        if path == "/now":
            if b.get("id"):
                now_on["data"] = {k: b.get(k) for k in ("role", "id", "kind", "members")}
            now_on["at"] = time.time()
            return self.send_json({"ok": True})
        if path == "/cmd":
            if b.get("cmd") not in ("next", "prev"):
                return self.send_json({"ok": False, "error": "bad cmd"}, 400)
            if not now_on["at"] or time.time() - now_on["at"] > 150:
                return self.send_json({"ok": False, "error": "The TV is not showing photos right now."}, 409)
            with cond:
                cmds.update(seq=cmds["seq"] + 1, cmd=b["cmd"], at=time.time())
                cond.notify_all()
            log("cmd", b["cmd"])
            return self.send_json({"ok": True, "seq": cmds["seq"]})
        self.send_json({"error": "not found"}, 404)


class Server(ThreadingHTTPServer):
    def handle_error(self, request, client_address):
        # the TV drops idle keep-alive connections about once a minute; that is normal, not worth a traceback
        if isinstance(sys.exc_info()[1], (ConnectionResetError, BrokenPipeError)):
            return
        super().handle_error(request, client_address)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8154)
    a = ap.parse_args()
    load()
    if HA_URL and TOKEN:
        threading.Thread(target=ws_loop, daemon=True).start()
        threading.Thread(target=rest_loop, daemon=True).start()
    else:
        log("no HA_URL or token in config.env: home states and mode switching by HA are off")
    srv = Server(("0.0.0.0", a.port), H)
    srv.daemon_threads = True
    log(f"bridge on :{a.port}, mode {state['mode']} rev {state['rev']}")
    srv.serve_forever()


if __name__ == "__main__":
    main()
