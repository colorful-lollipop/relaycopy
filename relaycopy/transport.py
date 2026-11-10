"""Use OpenSSH and rsync without executing application commands on the relay."""

import os
import re
import shlex
import subprocess
import tempfile
import time
from pathlib import Path

from .common import RelayError, remote_path, secret_file


def ssh_args(endpoint):
    host = endpoint.get("host")
    if not isinstance(host, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.@-]*", host):
        raise RelayError("SSH host must be a hostname, IPv4 address or SSH config alias")
    cmd = ["ssh", "-T", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes",
           "-o", "ForwardAgent=no", "-o", "ConnectTimeout=15",
           "-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=3"]
    if "port" in endpoint:
        port = int(endpoint["port"])
        if not 1 <= port <= 65535:
            raise RelayError("invalid SSH port")
        cmd += ["-p", str(port)]
    if endpoint.get("key_file"):
        cmd += ["-i", str(secret_file(endpoint["key_file"])), "-o", "IdentitiesOnly=yes"]
    if endpoint.get("known_hosts"):
        cmd += ["-o", "UserKnownHostsFile=" + str(Path(endpoint["known_hosts"]).expanduser())]
    return cmd, host


class Rsync:
    def __init__(self, endpoint, log, timeout=120):
        self.endpoint, self.log, self.timeout = endpoint, Path(log), timeout
        self.ssh, self.host = ssh_args(endpoint)
        self.master = None
        self.tmp = None

    def __enter__(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="relaycp-")
        self.socket = str(Path(self.tmp.name) / "cm")
        self.log.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.log_file = self.log.open("ab")
        self.master = subprocess.Popen(
            self.ssh + ["-MN", "-o", "ControlMaster=yes", "-o", "ControlPersist=no",
                        "-S", self.socket, self.host], stdin=subprocess.DEVNULL,
            stdout=self.log_file, stderr=self.log_file,
        )
        deadline = time.monotonic() + 30
        try:
            while not Path(self.socket).exists():
                if self.master.poll() is not None:
                    raise RelayError(f"relay SSH authentication/connection failed; see {self.log}")
                if time.monotonic() > deadline:
                    raise RelayError(f"relay SSH connection timed out; see {self.log}")
                time.sleep(0.1)
        except BaseException:
            self.__exit__(None, None, None)
            raise
        return self

    def __exit__(self, *_):
        if self.master is not None:
            # This socket is ours; never close a user's existing ControlMaster.
            try:
                subprocess.run(self.ssh + ["-S", self.socket, "-O", "exit", self.host],
                               stdin=subprocess.DEVNULL, stdout=self.log_file,
                               stderr=self.log_file, timeout=5)
            except subprocess.TimeoutExpired:
                pass
            try:
                self.master.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.master.terminate()
                self.master.wait(timeout=5)
        if hasattr(self, "log_file"):
            self.log_file.close()
        if self.tmp:
            self.tmp.cleanup()

    def copy(self, local, remote, *, upload=False, missing_ok=False, max_bytes=None, bwlimit=None):
        remote = remote_path(remote)
        shell = shlex.join(self.ssh + ["-S", self.socket])
        cmd = ["rsync", "-t", "--timeout=90", "-e", shell]
        # Metadata may shrink on updates. append-verify is only valid for payloads.
        cmd += ["--partial", "--append-verify"] if remote.endswith("/payload") else ["--checksum"]
        if upload:
            cmd += ["--mkpath"]
        if bwlimit:
            cmd += ["--bwlimit=" + str(int(bwlimit))]
        if max_bytes is not None:
            cmd += ["--max-size=" + str(max_bytes)]
        src, dst = str(local), self.host + ":" + remote
        cmd += [src, dst] if upload else [dst, src]
        result = subprocess.run(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, timeout=None if remote.endswith("/payload") else self.timeout,
                                env={**os.environ, "LC_ALL": "C"})
        if result.returncode == 0:
            if not upload and not Path(local).is_file():
                raise RelayError("relay metadata exceeds size limit or is not a regular file")
            return True
        error = result.stderr.decode("utf-8", "replace")
        if missing_ok and result.returncode == 23 and "No such file or directory" in error:
            return False
        self.log_file.write(result.stdout + result.stderr)
        self.log_file.flush()
        raise RelayError(f"rsync failed (exit {result.returncode}); see {self.log}")

    def put(self, local, remote, bwlimit=None):
        last = None
        for attempt in range(3):
            try:
                return self.copy(local, remote, upload=True, bwlimit=bwlimit)
            except RelayError as exc:
                last = exc
                if self.master.poll() is not None:
                    break
                if attempt < 2:
                    time.sleep(1 + attempt)
        raise last
