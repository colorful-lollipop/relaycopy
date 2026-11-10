"""Deliver a content-addressed zipapp through SSH stdin; no remote pip/curl."""

import base64
import hashlib
import io
import json
import subprocess
import sys
import zipfile
from pathlib import Path

from .common import RelayError, VERSION, secret_file
from .transport import ssh_args


def bundle():
    executable = Path(sys.argv[0])
    if executable.is_file() and zipfile.is_zipfile(executable):
        return executable.read_bytes()
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as z:
        files = {"__main__.py": b"from relaycopy.cli import main\nraise SystemExit(main())\n"}
        files.update({"relaycopy/" + p.name: p.read_bytes() for p in Path(__file__).parent.glob("*.py")})
        for name, data in sorted(files.items()):
            info = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            z.writestr(info, data)
    return b"#!/usr/bin/env python3\n" + stream.getvalue()


# Only this small bootstrap is run in the SSH session. The transfer detaches.
BOOTSTRAP = r'''
import base64, fcntl, hashlib, json, os, shutil, stat, subprocess, sys
from pathlib import Path
if sys.version_info < (3, 10):
    raise SystemExit("RelayCopy requires Python >=3.10")
if not shutil.which("rsync") or not shutil.which("ssh"):
    raise SystemExit("source requires existing rsync and OpenSSH; nothing was installed")
p = json.loads(base64.b64decode(PAYLOAD))
base = Path(p["remote_dir"]).expanduser().absolute()
base.mkdir(parents=True, exist_ok=True, mode=0o700)
if base.is_symlink() or base.stat().st_uid != os.getuid() or base.stat().st_mode & 0o022:
    raise SystemExit("remote tool directory must be owned by this user and not group/world writable")
data = base64.b64decode(p["bundle"])
sha = hashlib.sha256(data).hexdigest()
if sha != p["sha256"]:
    raise SystemExit("bootstrap bundle digest mismatch")
tool = base / ("relaycp-" + sha + ".pyz")
if tool.exists():
    if tool.is_symlink() or hashlib.sha256(tool.read_bytes()).hexdigest() != sha:
        raise SystemExit("existing remote bundle differs")
else:
    fd = os.open(tool, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o700)
    with os.fdopen(fd, "wb") as f:
        f.write(data)
if p.get("upload_key"):
    keydir = base / "keys"
    keydir.mkdir(mode=0o700, exist_ok=True)
    if keydir.is_symlink() or keydir.stat().st_mode & 0o077:
        raise SystemExit("remote key directory must have mode 0700")
    for field, target_field, mode in (("upload_key", "key_file", 0o600), ("upload_hosts", "known_hosts", 0o600)):
        blob = base64.b64decode(p[field])
        target = keydir / (field + "-" + hashlib.sha256(blob).hexdigest())
        if target.exists():
            if target.is_symlink() or target.read_bytes() != blob or target.stat().st_mode & 0o077:
                raise SystemExit("existing delegated credential differs or has unsafe permissions")
        else:
            fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
            with os.fdopen(fd, "wb") as f:
                f.write(blob)
        p["upload"][target_field] = str(target)
sources = [Path(s).expanduser().absolute() for s in p["sources"]]
if any(s.is_symlink() or not s.is_file() for s in sources):
    raise SystemExit("each source must be an existing regular file, not a symlink")
if len({s.name for s in sources}) != len(sources):
    raise SystemExit("batch basenames must be unique")
work = base / "jobs" / p["job"]
work.mkdir(parents=True, exist_ok=True, mode=0o700)
if work.is_symlink():
    raise SystemExit("job directory must not be a symlink")
with (work / "launch.lock").open("a") as lock:
    fcntl.flock(lock, fcntl.LOCK_EX)
    request = {"sources": [str(s) for s in sources], "job": p["job"], "relay": p["upload"], "work": str(base / "jobs"), "bwlimit": p.get("bwlimit")}
    request_file = work / "request.json"
    if request_file.exists() and json.loads(request_file.read_text()) != request:
        raise SystemExit("job already belongs to a different source/configuration")
    process_file = work / "process.json"
    if process_file.exists():
        previous = json.loads(process_file.read_text())
        proc = Path("/proc") / str(previous["pid"])
        try:
            ticks = proc.joinpath("stat").read_text().rsplit(")", 1)[1].split()[19]
            cmd = proc.joinpath("cmdline").read_bytes().split(b"\0")
            old_sha = previous.get("bundle_sha256", "")
            old_tool = str(base / ("relaycp-" + old_sha + ".pyz"))
            valid_sha = len(old_sha) == 64 and all(c in "0123456789abcdef" for c in old_sha)
            if valid_sha and previous.get("tool") == old_tool and ticks == previous["start_ticks"] and old_tool.encode() in cmd and b"_worker" in cmd:
                print(json.dumps(dict(previous, already_running=True)))
                raise SystemExit(0)
        except (FileNotFoundError, ProcessLookupError):
            pass
    fd = os.open(request_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(request, f)
    with (work / "worker.log").open("ab") as log:
        child = subprocess.Popen([sys.executable, str(tool), "_worker", str(request_file)],
            stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True, close_fds=True)
    ticks = Path("/proc", str(child.pid), "stat").read_text().rsplit(")", 1)[1].split()[19]
    record = {"job": p["job"], "pid": child.pid, "start_ticks": ticks, "sources": [str(s) for s in sources],
              "bundle_sha256": sha, "tool": str(tool), "log": str(work / "worker.log"), "version": p["version"]}
    process_file.write_text(json.dumps(record))
    print(json.dumps(record))
'''


def launch(source_config, source_paths, job, log, bwlimit=None):
    data = bundle()
    upload = source_config.get("upload")
    if not isinstance(upload, dict):
        raise RelayError("source needs an upload endpoint using its existing restricted key")
    payload = {"version": VERSION, "bundle": base64.b64encode(data).decode(),
               "sha256": hashlib.sha256(data).hexdigest(), "sources": source_paths,
               "job": job, "remote_dir": source_config.get("remote_dir", "~/.cache/relaycopy"),
               "upload": upload, "bwlimit": bwlimit}
    if source_config.get("upload_key_file"):
        key = secret_file(source_config["upload_key_file"])
        hosts = source_config.get("upload_known_hosts_file")
        if not hosts:
            raise RelayError("delegation needs upload_known_hosts_file containing an authenticated relay host key")
        payload["upload_key"] = base64.b64encode(key.read_bytes()).decode()
        payload["upload_hosts"] = base64.b64encode(Path(hosts).expanduser().read_bytes()).decode()
    encoded = base64.b64encode(json.dumps(payload).encode()).decode()
    code = "PAYLOAD = " + repr(encoded) + "\n" + BOOTSTRAP
    endpoint = source_config["ssh"] if "ssh" in source_config else {"host": source_config["host"]}
    ssh, host = ssh_args(endpoint)
    result = subprocess.run(ssh + [host, "python3 -"], input=code.encode(),
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
    Path(log).write_bytes(result.stderr)
    if result.returncode:
        raise RelayError(f"source bootstrap failed (exit {result.returncode}); see {log}")
    try:
        return json.loads(result.stdout)
    except ValueError as exc:
        raise RelayError("source bootstrap returned no valid receipt; inspect the remote job before retrying") from exc
