"""Narrow relay maintenance capability; payload transfer stays with rrsync."""

import argparse
import contextlib
import fcntl
import json
import os
import re
import shlex
import shutil
import stat
import time
import sys
from pathlib import Path

from .common import RelayError, identifier, validate_manifest


def _json_at(fd, name):
    handle = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
    with os.fdopen(handle, "rb") as f:
        if not stat.S_ISREG(os.fstat(f.fileno()).st_mode):
            raise RelayError("metadata must be a regular file")
        data = f.read(1024 * 1024 + 1)
        if len(data) > 1024 * 1024:
            raise RelayError("metadata too large")
        return json.loads(data)


def _write_at(fd, name, value):
    tmp = f".{name}.{os.getpid()}.tmp"
    handle = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=fd)
    try:
        with os.fdopen(handle, "w") as f:
            json.dump(value, f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, name, src_dir_fd=fd, dst_dir_fd=fd)
        os.fsync(fd)
    finally:
        try:
            os.unlink(tmp, dir_fd=fd)
        except FileNotFoundError:
            pass


class RelayStore:
    def __init__(self, root, namespace):
        self.root = Path(root).absolute()
        self.namespace = identifier(namespace)
        self.fd = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            self.policy = _json_at(self.fd, "managed.json")
            if self.policy.get("version") != 1 or self.policy.get("namespace") != namespace:
                raise RelayError("not a managed RelayCopy namespace")
            ttl = self.policy.get("ttl_hours")
            if type(ttl) not in (float, int) or ttl <= 0:
                raise RelayError("invalid retention policy")
            incoming = os.open("incoming", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=self.fd)
            try:
                self.ns = os.open(namespace, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=incoming)
            finally:
                os.close(incoming)
        except BaseException:
            os.close(self.fd)
            raise

    def close(self):
        os.close(self.ns)
        os.close(self.fd)

    @contextlib.contextmanager
    def lock(self, *, exclusive=False):
        handle = os.open("transfer.lock", os.O_RDWR | os.O_NOFOLLOW, dir_fd=self.fd)
        try:
            mode = fcntl.LOCK_EX | fcntl.LOCK_NB if exclusive else fcntl.LOCK_SH
            fcntl.flock(handle, mode)
            yield
        finally:
            os.close(handle)

    def info(self):
        usage = shutil.disk_usage(self.root)
        return {"namespace": self.namespace, "ttl_hours": self.policy["ttl_hours"],
                "free_bytes": usage.free, "total_bytes": usage.total}

    def release(self, job, sha256):
        identifier(job)
        if not re.fullmatch("[a-f0-9]{64}", sha256):
            raise RelayError("invalid release digest")
        with self.lock():
            locks = os.open("releases", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=self.fd)
            try:
                lock = os.open(job, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600, dir_fd=locks)
            finally:
                os.close(locks)
            try:
                fcntl.flock(lock, fcntl.LOCK_EX)
                return self._release(job, sha256)
            finally:
                os.close(lock)

    def _release(self, job, sha256):
        fd = os.open(job, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=self.ns)
        try:
            manifest = validate_manifest(_json_at(fd, "manifest.json"), job)
            done = _json_at(fd, "complete.json")
            if manifest.get("kind") == "batch" or manifest.get("sha256") != sha256:
                raise RelayError("release digest differs from file manifest")
            if done.get("job") != job or done.get("sha256") != sha256 or done.get("bytes") != manifest["bytes"]:
                raise RelayError("file is not complete; refusing release")
            try:
                previous = _json_at(fd, "released.json")
            except FileNotFoundError:
                previous = None
            if previous:
                if previous.get("job") != job or previous.get("sha256") != sha256:
                    raise RelayError("release receipt differs")
                try:
                    os.stat("payload", dir_fd=fd, follow_symlinks=False)
                except FileNotFoundError:
                    return {**previous, "already_released": True}
            try:
                payload = os.stat("payload", dir_fd=fd, follow_symlinks=False)
            except FileNotFoundError:
                payload = None
            if payload and (not stat.S_ISREG(payload.st_mode) or payload.st_size != manifest["bytes"]):
                raise RelayError("payload identity differs; refusing release")
            # unlinkat never follows a symlink outside this opened job directory.
            if payload:
                os.unlink("payload", dir_fd=fd)
            record = {"job": job, "sha256": sha256, "released_bytes": manifest["bytes"],
                      "released_at": time.time()}
            _write_at(fd, "released.json", record)
            return record
        finally:
            os.close(fd)

    def gc(self, *, dry_run=False, clock=None):
        cutoff = (time.time() if clock is None else clock) - self.policy["ttl_hours"] * 3600
        removed, skipped = [], []
        try:
            with self.lock(exclusive=True):
                for entry in os.scandir(self.ns):
                    try:
                        identifier(entry.name)
                    except RelayError:
                        continue
                    if not entry.is_dir(follow_symlinks=False):
                        continue
                    fd = os.open(entry.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=self.ns)
                    try:
                        newest = os.fstat(fd).st_ctime
                        unsafe = False
                        for item in os.scandir(fd):
                            st = item.stat(follow_symlinks=False)
                            # Do not traverse nested trees or follow symlinks supplied by a client.
                            if not (stat.S_ISREG(st.st_mode) or stat.S_ISLNK(st.st_mode)):
                                unsafe = True
                                break
                            newest = max(newest, st.st_ctime)
                        if unsafe or newest >= cutoff:
                            skipped.append(entry.name)
                            continue
                    finally:
                        os.close(fd)
                    if not dry_run:
                        if not shutil.rmtree.avoids_symlink_attacks:
                            raise RelayError("this platform lacks symlink-safe rmtree")
                        if sys.version_info >= (3, 11):
                            shutil.rmtree(entry.name, dir_fd=self.ns)
                        else:
                            # Writers hold the shared transfer lock; the exclusive GC
                            # lock prevents namespace changes on Python 3.10 as well.
                            shutil.rmtree(self.root / "incoming" / self.namespace / entry.name)
                        lockfile = self.root / "releases" / entry.name
                        lockfile.unlink(missing_ok=True)
                    removed.append(entry.name)
        except BlockingIOError:
            return {"status": "busy", "reason": "active rsync transfers", "removed": []}
        return {"status": "complete", "dry_run": dry_run, "removed": removed, "skipped": skipped}


def relay_main(argv):
    p = argparse.ArgumentParser(prog="relaycp _relay")
    p.add_argument("--root", required=True)
    p.add_argument("--namespace", required=True)
    p.add_argument("action", choices=["serve", "gc"])
    a = p.parse_args(argv)
    store = RelayStore(a.root, a.namespace)
    try:
        if a.action == "gc":
            result = store.gc()
        else:
            command = shlex.split(os.environ.get("SSH_ORIGINAL_COMMAND", ""))
            if command == ["info"]:
                result = store.info()
            elif command in (["gc"], ["gc-dry-run"]):
                result = store.gc(dry_run=command == ["gc-dry-run"])
            elif len(command) == 3 and command[0] == "release":
                result = store.release(command[1], command[2])
            else:
                raise RelayError("only info, gc, gc-dry-run and release JOB SHA256 are allowed")
        print(json.dumps(result), flush=True)
    finally:
        store.close()
