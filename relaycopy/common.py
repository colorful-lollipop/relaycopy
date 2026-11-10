"""Small, dependency-free primitives shared by the CLI and remote worker."""

import contextlib
import hashlib
import json
import os
import re
import stat
import time
from pathlib import Path

VERSION = "0.2.1"


class RelayError(Exception):
    """An actionable error that is safe to show without a traceback."""


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", value):
        raise RelayError("invalid identifier; use 1–128 ASCII letters, digits, dots, underscores or hyphens")
    return value


def remote_path(value):
    # rsync passes this path to an SSH shell. Never accept shell syntax or '..'.
    if not isinstance(value, str) or not re.fullmatch(r"/?[A-Za-z0-9_./-]+", value):
        raise RelayError("relay paths must contain only ASCII letters, digits, _, -, / and .")
    if ".." in value.split("/") or value.startswith(("//", "-")):
        raise RelayError("parent traversal is not allowed in relay paths")
    return value


def join_remote(*parts):
    return remote_path("/".join(str(p).rstrip("/") for p in parts if p not in ("", ".")))


def digest(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        while block := f.read(1024 * 1024):
            h.update(block)
    return h.hexdigest()


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = path.with_name(path.name + f".{os.getpid()}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(value, f, ensure_ascii=False, sort_keys=True)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def secret_file(path):
    path = Path(path).expanduser()
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077:
        raise RelayError(f"private key must be a regular file with mode 0600: {path}")
    return path


@contextlib.contextmanager
def locked(path):
    import fcntl

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "a") as f:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RelayError(f"transfer already running; lock: {path}") from exc
        yield


def emit(event, **fields):
    print(json.dumps({"event": event, **fields}, ensure_ascii=False), flush=True)


def fingerprint(st):
    return (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns)


def make_manifest(source, job):
    source = Path(source).expanduser().absolute()
    fd = os.open(source, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as f:
        before = os.fstat(f.fileno())
        if not stat.S_ISREG(before.st_mode):
            raise RelayError("source must be a completed regular file, not a symlink or device")
        h = hashlib.sha256()
        while block := f.read(1024 * 1024):
            h.update(block)
        if fingerprint(before) != fingerprint(os.fstat(f.fileno())):
            raise RelayError("source changed during hashing; finish writing it before transfer")
    name = source.name
    if name in ("", ".", "..") or "\\" in name or any(ord(c) < 32 for c in name):
        raise RelayError("unsupported source basename")
    return {"version": 1, "job": identifier(job), "name": name, "bytes": before.st_size,
            "sha256": h.hexdigest()}, fingerprint(before)


def validate_manifest(m, job):
    if not isinstance(m, dict) or m.get("version") != 1 or m.get("job") != identifier(job):
        raise RelayError("unsupported or mismatched manifest")
    if m.get("kind") == "batch":
        files = m.get("files")
        if not isinstance(files, list) or not 1 <= len(files) <= 10000:
            raise RelayError("invalid batch file list")
        names = set()
        for i, item in enumerate(files):
            if not isinstance(item, dict) or item.get("job") != f"{job}.{i:06d}":
                raise RelayError("invalid batch child ID")
            validate_manifest({"version": 1, "job": item["job"], "name": item.get("name"),
                               "bytes": 0, "sha256": "0" * 64}, item["job"])
            if item["name"] in names:
                raise RelayError("batch basenames must be unique")
            names.add(item["name"])
        return m
    name = m.get("name")
    if not isinstance(name, str) or name in ("", ".", "..") or "/" in name or "\\" in name or any(ord(c) < 32 for c in name):
        raise RelayError("unsafe filename in manifest")
    if type(m.get("bytes")) is not int or m["bytes"] < 0:
        raise RelayError("invalid manifest size")
    if not re.fullmatch(r"[a-f0-9]{64}", str(m.get("sha256", ""))):
        raise RelayError("invalid file digest")
    return m


def read_json(path):
    with Path(path).open(encoding="utf-8") as f:
        return json.load(f)


def now():
    return time.time()
