"""Photo ids for library/: <album prefix>-<file name slug> (tools/real_meta.py). The prefix of every album
folder is kept in real/album_prefix.json, so a folder renamed on the photos manager keeps its prefix, and its photos
keep their ids, scores and settings. A folder renamed in Finder gets a new prefix, so its photos are rebuilt."""
import json, os, re, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "server"))
import tvconf  # noqa: E402

T = tvconf.ROOT
MAP = f"{T}/real/album_prefix.json"
FIRST = tvconf.get_json("TVFRAME_ALBUM_PREFIXES", {})     # fixed prefixes for folders that must keep older ids


def slug(s):
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", s.lower())).strip("-")


def load():
    try:
        return {**FIRST, **json.load(open(MAP))}
    except (OSError, ValueError):
        return dict(FIRST)


def save(m):
    tmp = MAP + ".tmp"
    json.dump(m, open(tmp, "w"), indent=1, sort_keys=True)
    os.replace(tmp, MAP)


def prefix(album, m):
    """The album's prefix, assigning a new unique one (added to m) the first time a folder is seen."""
    if album not in m:
        base = re.sub(r"[^a-z0-9]+", "", album.lower())[:16] or "a"
        used, p, i = set(m.values()) | {"lib"}, base, 2
        while p in used:
            p, i = f"{base}{i}", i + 1
        m[album] = p
    return m[album]


def photo_id(album, file, m):
    """The id a file gets in an album, before the -b suffix real_meta adds when two names slug the same."""
    return f"{prefix(album, m)}-{slug(os.path.splitext(file)[0])}"


def rename(old, new):
    m = load()
    if old in m:
        m[new] = m.pop(old)
        save(m)
