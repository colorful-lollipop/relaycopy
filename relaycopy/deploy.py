"""One-time relay provisioning. Secrets are generated and kept on the controller."""

import base64
import hashlib
import json
import os
import re
import subprocess
from pathlib import Path

from .bootstrap import bundle
from .common import RelayError, atomic_json, emit, secret_file
from .transport import ssh_args


INSTALL = r"""
import base64, hashlib, json, os, pwd, re, shutil, subprocess, sys
from pathlib import Path
p = json.loads(base64.b64decode(PAYLOAD))
if os.geteuid() != 0:
    raise SystemExit("setup requires root, or --sudo with passwordless sudo")
if sys.version_info < (3, 10):
    raise SystemExit("relay requires Python >=3.10")
user, namespace = p["user"], p["namespace"]
if not re.fullmatch(r"[a-z_][a-z0-9_-]{0,29}", user) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", namespace):
    raise SystemExit("invalid dedicated user or namespace")
root = Path(p["root"])
if not root.is_absolute() or len(root.parts) < 3 or not re.fullmatch(r"/[A-Za-z0-9_./-]+", str(root)) or root.resolve() != root or '..' in root.parts:
    raise SystemExit("root must be a dedicated absolute path without symlinks or shell syntax")
if root.exists() and not (root / "managed.json").is_file():
    raise SystemExit("refusing to adopt an existing unmanaged directory")
required = ["rsync", "rrsync", "flock", "openssl", "systemctl", "useradd", "chpasswd"]
if any(not shutil.which(x) for x in required) and p["install_deps"]:
    if not shutil.which("apt-get"):
        raise SystemExit("install dependencies using this distribution's package manager first")
    subprocess.run(["apt-get", "update"], check=True, stdout=subprocess.DEVNULL)
    subprocess.run(["apt-get", "install", "-y", "rsync", "util-linux", "openssl"], check=True, stdout=subprocess.DEVNULL)
missing = [x for x in required if not shutil.which(x)]
if missing:
    raise SystemExit("missing relay prerequisites: " + ', '.join(missing))
if not Path("/run/systemd/system").is_dir():
    raise SystemExit("automatic retention requires a running systemd system manager")
previous = json.loads((root / "managed.json").read_text()) if root.exists() else None
try:
    account = pwd.getpwnam(user)
except KeyError:
    account = None
if account and (not previous or previous.get("user") != user or previous.get("uid") != account.pw_uid):
    raise SystemExit("refusing to modify an existing unmanaged Unix account")
if not account:
    if previous:
        raise SystemExit("managed Unix account is missing; inspect before repairing")
    subprocess.run(["useradd", "--system", "--create-home", "--home-dir", str(root / "home"), "--shell", "/bin/sh", user], check=True)
    account = pwd.getpwnam(user)
    # An unknown high-entropy password unlocks public-key account checks, without
    # enabling any usable password credential or changing global sshd settings.
    random = os.urandom(48).hex().encode() + b'\n'
    hashed = subprocess.check_output(["openssl", "passwd", "-6", "-stdin"], input=random).strip()
    subprocess.run(["chpasswd", "-e"], input=user.encode() + b':' + hashed + b'\n', check=True)
uid, gid = account.pw_uid, account.pw_gid
root.mkdir(parents=True, exist_ok=True)
root.chmod(0o755)
os.chown(root, 0, 0)
for directory in (root / "incoming", root / "incoming" / namespace, root / "releases"):
    directory.mkdir(mode=0o700, exist_ok=True)
    if directory.is_symlink():
        raise SystemExit("managed directories cannot be symlinks")
    os.chown(directory, uid, gid)
    directory.chmod(0o700)
lock = root / "transfer.lock"
fd = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600); os.close(fd)
os.chown(lock, uid, gid)
tool = root / "relaycp.pyz"
data = base64.b64decode(p["bundle"])
if hashlib.sha256(data).hexdigest() != p["sha256"]:
    raise SystemExit("relay bundle hash mismatch")
temporary = root / "relaycp.pyz.new"
fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o644)
with os.fdopen(fd, 'wb') as f: f.write(data)
os.replace(temporary, tool); os.chown(tool, 0, 0); tool.chmod(0o644)
sshd = Path(account.pw_dir) / ".ssh"
sshd.mkdir(mode=0o700, exist_ok=True)
if sshd.is_symlink(): raise SystemExit(".ssh cannot be a symlink")
os.chown(sshd, uid, gid); sshd.chmod(0o700)
auth = sshd / "authorized_keys"
if auth.exists() and (auth.is_symlink() or not previous or hashlib.sha256(auth.read_bytes()).hexdigest() != previous.get("authorized_keys_sha256")):
    raise SystemExit("authorized_keys changed outside this deployment; refusing to replace it")
keys = p["public_keys"]
for key in keys.values():
    if not re.fullmatch(r"ssh-ed25519 [A-Za-z0-9+/=]+", key):
        raise SystemExit("expected Ed25519 public keys without options")
flock, rrsync = shutil.which("flock"), shutil.which("rrsync")
prefix = f'{flock} -s {lock} {rrsync}'
helper = f'{sys.executable} {tool} _relay --root {root} --namespace {namespace} serve'
lines = [f'restrict,command="{prefix} -wo -munge -no-del {root}/incoming/{namespace}" {keys["upload"]}',
         f'restrict,command="{prefix} -ro {root}/incoming" {keys["read"]}',
         f'restrict,command="{helper}" {keys["cleanup"]}']
content = ('\n'.join(lines) + '\n').encode()
fd = os.open(auth, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
with os.fdopen(fd, 'wb') as f: f.write(content)
os.chown(auth, uid, gid); auth.chmod(0o600)
policy = {"version": 1, "namespace": namespace, "user": user, "uid": uid,
          "ttl_hours": p["ttl_hours"], "authorized_keys_sha256": hashlib.sha256(content).hexdigest()}
(root / "managed.json").write_text(json.dumps(policy)); (root / "managed.json").chmod(0o644)
name = 'relaycopy-' + user
service = f'''[Unit]
Description=Expire inactive RelayCopy spool entries
[Service]
Type=oneshot
User={user}
ExecStart={sys.executable} {tool} _relay --root {root} --namespace {namespace} gc
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths={root}
'''
timer = '''[Unit]
Description=Periodic RelayCopy retention
[Timer]
OnBootSec=5min
OnUnitActiveSec=15min
[Install]
WantedBy=timers.target
'''
for suffix, text in (("service", service), ("timer", timer)):
    unit = Path('/etc/systemd/system') / (name + '.' + suffix)
    if unit.exists() and not previous:
        raise SystemExit("refusing to overwrite an unrelated systemd unit")
    unit.write_text(text)
subprocess.run(['systemctl', 'daemon-reload'], check=True, stdout=subprocess.DEVNULL)
subprocess.run(['systemctl', 'enable', '--now', name + '.timer'], check=True, stdout=subprocess.DEVNULL)
host_keys = []
for key in Path('/etc/ssh').glob('ssh_host_*_key.pub'):
    fields = key.read_text().split()
    if len(fields) >= 2: host_keys.append(' '.join(fields[:2]))
if not host_keys: raise SystemExit("no relay host public keys found; configure an authenticated known_hosts file")
print(json.dumps({"user": user, "namespace": namespace, "root": str(root), "host_keys": host_keys,
                  "timer": name + '.timer', "ttl_hours": p["ttl_hours"], "bundle_sha256": p["sha256"]}))
"""


def setup(args):
    compile(INSTALL, "<relay setup>", "exec")
    directory = Path(args.config_output).expanduser().absolute().parent
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    if directory.is_symlink() or directory.stat().st_mode & 0o077:
        raise RelayError("configuration directory must be private (mode 0700)")
    check = subprocess.run(["git", "-C", str(directory), "rev-parse", "--show-toplevel"], capture_output=True)
    if check.returncode == 0:
        raise RelayError("keep configuration and private keys outside every Git working tree")
    destination = Path(args.config_output).expanduser().absolute()
    if destination.exists():
        raise RelayError("configuration already exists; use its recorded admin receipt to update deliberately")
    if not args.apply:
        emit("setup_plan", ssh=args.ssh, root=args.root, user=args.user, namespace=args.namespace,
             ttl_hours=args.ttl_hours, config=str(destination), apply_required=True)
        return
    keys = {}
    for name in ("upload", "read", "cleanup"):
        path = directory / (name + "_ed25519")
        if not path.exists():
            subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(path)],
                           stdin=subprocess.DEVNULL, check=True)
        secret_file(path)
        fields = subprocess.check_output(["ssh-keygen", "-y", "-P", "", "-f", str(path)],
                                         stdin=subprocess.DEVNULL, text=True).split()
        keys[name] = " ".join(fields[:2])
    data = bundle()
    payload = {"root": args.root, "user": args.user, "namespace": args.namespace,
               "ttl_hours": args.ttl_hours, "install_deps": args.install_deps,
               "bundle": base64.b64encode(data).decode(), "sha256": hashlib.sha256(data).hexdigest(),
               "public_keys": keys}
    encoded = base64.b64encode(json.dumps(payload).encode()).decode()
    ssh, host = ssh_args({"host": args.ssh})
    effective = subprocess.check_output(["ssh", "-G", args.ssh], text=True)
    effective = dict(line.split(" ", 1) for line in effective.splitlines() if " " in line)
    address = args.relay_address or effective["hostname"]
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", address):
        raise RelayError("relay address must be a hostname or IPv4 address")
    port = int(effective["port"])
    command = "sudo -n python3 -" if args.sudo else "python3 -"
    result = subprocess.run(ssh + [host, command], input=("PAYLOAD=" + repr(encoded) + "\n" + INSTALL).encode(),
                            capture_output=True, timeout=300)
    (directory / "setup.log").write_bytes(result.stderr)
    if result.returncode:
        raise RelayError(f"relay setup failed; inspect {directory / 'setup.log'} before retrying")
    receipt = json.loads(result.stdout)
    atomic_json(directory / "deployment.json", receipt)
    known = directory / "known_hosts"
    host_field = address if port == 22 else f"[{address}]:{port}"
    known.write_text("".join(host_field + " " + key + "\n" for key in receipt["host_keys"]))
    known.chmod(0o600)
    endpoint = {"host": receipt["user"] + "@" + address, "port": port, "known_hosts": str(known)}
    profile = {"namespace": receipt["namespace"], "download_root": ".", "cleanup_after_receive": True}
    for role, field in (("read", "relay"), ("upload", "upload"), ("cleanup", "cleanup")):
        profile[field] = {**endpoint, "key_file": str(directory / (role + "_ed25519"))}
    config = {"version": 1, "state_dir": str(directory / "state"), "profiles": {"default": profile}}
    atomic_json(destination, config)
    emit("relay_deployed", config=str(destination), user=receipt["user"], namespace=receipt["namespace"],
         timer=receipt["timer"], ttl_hours=receipt["ttl_hours"])
