#!/usr/bin/env python3
"""A terminal recording (script -q -c "./tvframe setup" setup.txt) as a PNG for the README.

Replays the cursor moves Docker Compose uses for its progress lines, keeps bold, dim and colour, swaps the
recording machine's address and home folder for example ones, and renders the result with headless Chrome.
    python3 terminal_shot.py setup.txt out.png [--from "TV Frame setup"] [--sub OLD=NEW ...] [--drop REGEX ...]
"""
import argparse, html, os, re, subprocess, sys, tempfile

COLORS = {"31": "#ff7b72", "32": "#7ee787", "33": "#e3b341", "34": "#79c0ff", "36": "#76e3ea"}


def replay(text):
    """The screen after the recording: a list of lines, each a list of (char, style) pairs."""
    lines, row, col, style = [[]], 0, 0, ()
    for tok in re.findall(r"\x1b\[[0-9;?]*[A-Za-z]|\x1b\][^\x07]*\x07|\r|\n|[^\x1b\r\n]", text):
        if tok == "\n":
            row += 1; col = 0
            while len(lines) <= row: lines.append([])
        elif tok == "\r":
            col = 0
        elif tok.startswith("\x1b["):
            n, cmd = tok[2:-1], tok[-1]
            if cmd == "A": row = max(0, row - int(n or 1))
            elif cmd == "G": col = max(0, int(n or 1) - 1)
            elif cmd == "K": del lines[row][col:]
            elif cmd == "m":
                codes = [c for c in n.split(";") if c] or ["0"]
                for c in codes:
                    style = () if c == "0" else tuple(x for x in style if not (c.startswith("3") and x.startswith("3"))) + (c,)
        elif not tok.startswith("\x1b"):
            ln = lines[row]
            while len(ln) < col: ln.append((" ", ()))
            if col < len(ln): ln[col] = (tok, style)
            else: ln.append((tok, style))
            col += 1
    return lines


def to_html(lines):
    out = []
    for ln in lines:
        s, cur, buf = "", None, ""
        for ch, st in ln + [("", None)]:
            if st != cur:
                if buf:
                    css = []
                    for c in cur or ():
                        if c == "1": css.append("font-weight:700")
                        elif c == "2": css.append("opacity:.6")
                        elif c in COLORS: css.append(f"color:{COLORS[c]}")
                    s += f'<span style="{";".join(css)}">{html.escape(buf)}</span>' if css else html.escape(buf)
                cur, buf = st, ""
            buf += ch
        out.append(s)
    return "\n".join(out)


def main():
    a = argparse.ArgumentParser()
    a.add_argument("rec"); a.add_argument("png")
    a.add_argument("--from", dest="start", default=None, help="drop everything above the first line containing this")
    a.add_argument("--host", default=None, help="this machine's address, shown as 192.168.1.20")
    a.add_argument("--sub", action="append", default=[], help="OLD=NEW, replaced everywhere (test ports, test folders)")
    a.add_argument("--drop", action="append", default=[], help="leave out the lines that match this regex")
    a.add_argument("--width", type=int, default=1200)
    o = a.parse_args()
    text = open(o.rec, encoding="utf-8", errors="replace").read()
    text = re.sub(r"^Script (started|done).*\n", "", text, flags=re.M)
    if o.host: text = text.replace(o.host, "192.168.1.20")
    for sub in o.sub:
        old, new = sub.split("=", 1); text = text.replace(old, new)
    text = text.replace(os.path.expanduser("~"), "/home/you")
    lines = replay(text)
    lines = [ln for ln in lines if not any(re.search(d, "".join(c for c, _ in ln)) for d in o.drop)]
    while lines and not "".join(c for c, _ in lines[-1]).strip(): lines.pop()
    if o.start:
        k = next(i for i, ln in enumerate(lines) if o.start in "".join(c for c, _ in ln))
        lines = lines[k:]
    page = f"""<!doctype html><meta charset="utf-8"><style>
body{{margin:0;background:transparent}}
.w{{margin:0;background:#0d1117;overflow:hidden;width:{o.width}px}}
.t{{height:34px;background:#161b22;display:flex;align-items:center;gap:8px;padding:0 14px}}
.t i{{width:12px;height:12px;border-radius:50%;background:#30363d;display:block}}
pre{{margin:0;padding:16px 20px 20px;color:#e6edf3;font:14px/1.5 "DejaVu Sans Mono",Menlo,monospace;white-space:pre-wrap}}
</style><div class="w"><div class="t"><i></i><i></i><i></i></div><pre>{to_html(lines)}</pre></div>"""
    with tempfile.NamedTemporaryFile("w", suffix=".html", delete=False) as f:
        f.write(page)
    height = 34 + 36 + int(len(lines) * 21) + 8
    subprocess.run([os.environ.get("CHROME", "google-chrome"), "--headless=new", "--hide-scrollbars",
                    "--force-device-scale-factor=2",
                    f"--window-size={o.width},{height}", f"--screenshot={os.path.abspath(o.png)}", f.name],
                   check=True, capture_output=True)
    os.unlink(f.name)
    print(o.png, len(lines), "lines")


if __name__ == "__main__":
    sys.exit(main())
