#!/usr/bin/env python3
"""Android TV Remote v2 client for testing real remote-key paths on the Streamer.

  atvremote.py pair            start pairing; then write the code shown on the TV to ~/.config/tvframe/atv_code
  atvremote.py key NAME|NUM .. send keys (e.g. key MACRO_1 or key 313), 1.2 s apart

Keys sent this way arrive as a real input device (virtual-remote), so they pass through accessibility
key filters, unlike `adb shell input keyevent`. Cert and key live in ~/.config/tvframe (0600).
"""
import asyncio, os, sys, time
from androidtvremote2 import AndroidTVRemote
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "server"))
import tvconf  # noqa: E402

HOST = tvconf.TV.split(":")[0]
CFG = os.path.expanduser("~/.config/tvframe")
CERT, KEY, CODE = f"{CFG}/atv_cert.pem", f"{CFG}/atv_key.pem", f"{CFG}/atv_code"

def remote():
    return AndroidTVRemote("tvframe", CERT, KEY, HOST)

async def pair():
    r = remote()
    if await r.async_generate_cert_if_missing():
        os.chmod(CERT, 0o600); os.chmod(KEY, 0o600)
    await r.async_start_pairing()
    print("pairing started, waiting for code file", flush=True)
    for _ in range(300):
        if os.path.exists(CODE):
            code = open(CODE).read().strip(); os.remove(CODE)
            await r.async_finish_pairing(code)
            print("paired", flush=True); return
        await asyncio.sleep(1)
    print("no code", flush=True)

async def keys(names):
    r = remote()
    await r.async_connect()
    await asyncio.sleep(1.5)   # the TV registers its virtual-remote input device first
    for n in names:
        if n.startswith("sleep"):
            await asyncio.sleep(float(n[5:] or 2)); continue
        code = int(n) if n.isdigit() else "KEYCODE_" + n.upper().removeprefix("KEYCODE_")
        r.send_key_command(code)
        print(time.strftime("%T"), "sent", n, code, flush=True)
        await asyncio.sleep(1.2)
    r.disconnect()

if __name__ == "__main__":
    if sys.argv[1] == "pair": asyncio.run(pair())
    else: asyncio.run(keys(sys.argv[2:]))
