"""State files, read safely and written atomically.

A write goes to a temp file in the same folder and is swapped in with os.replace, so a job
killed mid-write can never leave a half-written state file (which would silently reset the
dedupe, the forecast history or the gap log the next morning).
"""
import json, os, tempfile


def read(path, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def write(path, obj, **kw):
    d = os.path.dirname(path) or "."
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".tmp-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(obj, f, default=str, **kw)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def text(path):
    with open(path, encoding="utf-8-sig") as f:
        return f.read()
