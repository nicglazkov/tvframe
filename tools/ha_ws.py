#!/usr/bin/env python3
"""Tiny Home Assistant websocket client (stdlib only) for one-off helper setup.
usage: ha_ws.py '<json message without id>' [...]   prints each result"""
import base64, json, os, socket, struct, sys
from urllib.parse import urlsplit
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "server"))
import tvconf  # noqa: E402

_u = urlsplit(tvconf.HA_URL or "http://127.0.0.1:8123")
HA = (_u.hostname, _u.port or 8123)
TOK = tvconf.ha_token()


def connect():
    s = socket.create_connection(HA, timeout=15)
    key = base64.b64encode(os.urandom(16)).decode()
    s.sendall((f"GET /api/websocket HTTP/1.1\r\nHost: {HA[0]}:{HA[1]}\r\nUpgrade: websocket\r\n"
               f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n").encode())
    buf = b""
    while b"\r\n\r\n" not in buf:
        buf += s.recv(4096)
    head, rest = buf.split(b"\r\n\r\n", 1)
    if b" 101 " not in head.split(b"\r\n")[0]:
        raise SystemExit("no upgrade: " + head.decode(errors="replace"))
    return s, rest


class WS:
    def __init__(self):
        self.s, self.buf = connect()

    def _read(self, n):
        while len(self.buf) < n:
            d = self.s.recv(65536)
            if not d:
                raise EOFError
            self.buf += d
        out, self.buf = self.buf[:n], self.buf[n:]
        return out

    def recv(self):
        data = b""
        while True:
            b1, b2 = self._read(2)
            n = b2 & 0x7F
            if n == 126:
                n = struct.unpack(">H", self._read(2))[0]
            elif n == 127:
                n = struct.unpack(">Q", self._read(8))[0]
            payload = self._read(n)
            op = b1 & 0x0F
            # control frames: HA pings every 55 s and drops clients that do not answer
            if op == 0x9:
                self._frame(0xA, payload); continue
            if op == 0xA:
                continue
            if op == 0x8:
                raise EOFError("closed by HA")
            data += payload
            if b1 & 0x80:
                return json.loads(data)

    def send(self, obj):
        self._frame(0x1, json.dumps(obj).encode())

    def _frame(self, op, p):
        mask = os.urandom(4)
        h = bytes([0x80 | op])
        if len(p) < 126:
            h += bytes([0x80 | len(p)])
        elif len(p) < 65536:
            h += bytes([0x80 | 126]) + struct.pack(">H", len(p))
        else:
            h += bytes([0x80 | 127]) + struct.pack(">Q", len(p))
        self.s.sendall(h + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(p)))


def main():
    ws = WS()
    ws.recv()
    ws.send({"type": "auth", "access_token": TOK})
    r = ws.recv()
    if r.get("type") != "auth_ok":
        raise SystemExit("auth failed")
    for i, m in enumerate(sys.argv[1:], 1):
        msg = json.loads(m)
        msg["id"] = i
        ws.send(msg)
        print(json.dumps(ws.recv()))


if __name__ == "__main__":
    main()
