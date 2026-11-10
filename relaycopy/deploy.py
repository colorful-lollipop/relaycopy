"""One-time relay provisioning. Secrets are generated and kept on the controller."""

import base64
import hashlib
import json
import os
import re
import shutil
import time
import subprocess
from pathlib import Path

from .bootstrap import bundle
from .common import RelayError, atomic_json, emit, read_json, secret_file
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
previous = json.loads((root / "managed.json").read_text()) if root.exists() else None
if previous and not p["upgrade"]:
    raise SystemExit("relay already exists; use --upgrade with its private controller config, or a new user/root")
if p["upgrade"] and not previous:
    raise SystemExit("upgrade requires the existing managed relay")
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
try:
    account = pwd.getpwnam(user)
except KeyError:
    account = None
if account and (not previous or previous.get("user") != user or previous.get("uid") != account.pw_uid):
    raise SystemExit("refusing to modify an existing unmanaged Unix account")
if previous and not account:
    raise SystemExit("managed Unix account is missing; inspect before repairing")
if previous:
    auth_before = Path(account.pw_dir) / ".ssh" / "authorized_keys"
    if previous.get("namespace") != namespace or auth_before.is_symlink() or hashlib.sha256(auth_before.read_bytes()).hexdigest() != previous.get("authorized_keys_sha256"):
        raise SystemExit("managed identity or authorized_keys changed; inspect before upgrading")
    actual_keys = set(re.findall(r" (ssh-ed25519 [A-Za-z0-9+/=]+)(?:\s|$)", auth_before.read_text()))
    if actual_keys != set(p["expected_public_keys"]):
        raise SystemExit("controller credentials do not own this relay; refusing replacement")
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
key = p["public_key"]
if not re.fullmatch(r"ssh-ed25519 [A-Za-z0-9+/=]+", key):
    raise SystemExit("expected an Ed25519 public key without options")
flock, rrsync = shutil.which("flock"), shutil.which("rrsync")
helper = f'{sys.executable} {tool} _relay --root {root} --namespace {namespace} dispatch'
content = (f'restrict,command="{helper}" {key}\n').encode()
if previous:
    for target, body in ((root / "authorized_keys.previous", auth.read_bytes()),
                         (root / "managed.previous.json", (root / "managed.json").read_bytes())):
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'wb') as output: output.write(body)
        os.chown(target, 0, 0); target.chmod(0o600)
fd = os.open(auth, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
with os.fdopen(fd, 'wb') as f: f.write(content)
os.chown(auth, uid, gid); auth.chmod(0o600)
policy = {"version": 1, "namespace": namespace, "user": user, "uid": uid,
          "ttl_hours": p["ttl_hours"], "key_mode": "unified", "flock": flock, "rrsync": rrsync, "authorized_keys_sha256": hashlib.sha256(content).hexdigest()}
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
                  "key_mode": "unified", "timer": name + '.timer', "ttl_hours": p["ttl_hours"], "bundle_sha256": p["sha256"]}))
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
    existing = None
    backup = None
    expected = []
    if destination.exists():
        if not args.upgrade:
            raise RelayError("configuration already exists; use setup-relay --upgrade --ssh ADMIN --apply")
        existing = read_json(destination)
        if existing.get("version") != 1:
            raise RelayError("unsupported configuration version")
        receipt = read_json(directory / "deployment.json")
        if set(existing.get("profiles", {})) != {"default"} or existing.get("sources"):
            raise RelayError("automatic upgrade requires the generated single managed profile without source overrides")
        old_profile = existing["profiles"]["default"]
        if receipt["namespace"] != old_profile.get("namespace"):
            raise RelayError("deployment receipt differs from configured namespace")
        args.root, args.user, args.namespace = receipt["root"], receipt["user"], receipt["namespace"]
        args.ttl_hours = receipt["ttl_hours"]
        args.relay_address = args.relay_address or old_profile["relay"]["host"].split("@", 1)[-1]
        roles = ("relay",) if old_profile.get("key_mode") == "unified" else ("relay", "upload", "cleanup")
        expected = [public_key(secret_file(old_profile[role]["key_file"])) for role in roles]
    elif args.upgrade:
        raise RelayError("--upgrade requires an existing local configuration and deployment receipt")
    if not args.apply:
        emit("setup_plan", ssh=args.ssh, root=args.root, user=args.user, namespace=args.namespace,
             ttl_hours=args.ttl_hours, config=str(destination), key_mode="unified",
             upgrade=args.upgrade, apply_required=True)
        return
    path = directory / "relay_ed25519"
    if not path.exists():
        if existing:
            old_upload = existing["profiles"]["default"].get("upload", existing["profiles"]["default"]["relay"])
            with path.open("xb") as output:
                output.write(secret_file(old_upload["key_file"]).read_bytes())
            path.chmod(0o600)
        else:
            subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(path)],
                           stdin=subprocess.DEVNULL, check=True)
    secret_file(path)
    key = public_key(path)
    if existing:
        backup = directory / ("backup-" + str(time.time_ns()))
        backup.mkdir(mode=0o700)
        for name in (destination.name, "deployment.json", "known_hosts"):
            source = directory / name
            if source.is_file():
                shutil.copyfile(source, backup / name)
                (backup / name).chmod(0o600)
    data = bundle()
    payload = {"root": args.root, "user": args.user, "namespace": args.namespace,
               "ttl_hours": args.ttl_hours, "install_deps": args.install_deps,
               "bundle": base64.b64encode(data).decode(), "sha256": hashlib.sha256(data).hexdigest(),
               "public_key": key, "upgrade": args.upgrade, "expected_public_keys": expected}
    encoded = base64.b64encode(json.dumps(payload).encode()).decode()
    ssh, host = ssh_args({"host": args.ssh})
    effective = subprocess.check_output(["ssh", "-G", args.ssh], text=True)
    effective = dict(line.split(" ", 1) for line in effective.splitlines() if " " in line)
    address = args.relay_address or effective["hostname"]
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", address):
        raise RelayError("relay address must be a hostname or IPv4 address")
    port = int(old_profile["relay"].get("port", effective["port"])) if existing else int(effective["port"])
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
    profile.update({"key_mode": "unified", "relay": {**endpoint, "key_file": str(path)}})
    config = {"version": 1, "state_dir": str(directory / "state"), "profiles": {"default": profile}}
    if existing:
        config = {**config, **existing, "profiles": {"default": profile}}
        for field in ("cleanup_after_receive", "remote_dir", "download_root"):
            if field in existing["profiles"]["default"]:
                profile[field] = existing["profiles"]["default"][field]
    atomic_json(destination, config)
    if backup:
        for role in ("upload", "read", "cleanup"):
            for suffix in ("", ".pub"):
                old = directory / (role + "_ed25519" + suffix)
                if old.exists() and not old.is_symlink():
                    old.rename(backup / old.name)
    emit("relay_deployed", config=str(destination), user=receipt["user"], namespace=receipt["namespace"],
         timer=receipt["timer"], ttl_hours=receipt["ttl_hours"], key_mode="unified",
         backup=str(backup) if backup else None)


def public_key(path):
    fields = subprocess.check_output(["ssh-keygen", "-y", "-P", "", "-f", str(path)],
                                     stdin=subprocess.DEVNULL, text=True).split()
    return " ".join(fields[:2])
