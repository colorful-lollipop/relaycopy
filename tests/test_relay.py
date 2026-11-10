import json
import base64
import hashlib
import os
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from relaycopy.common import RelayError, atomic_json
from relaycopy.relay import RelayStore
from relaycopy.deploy import INSTALL


class Maintenance(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "relay"
        (self.root / "incoming/files").mkdir(parents=True)
        (self.root / "releases").mkdir()
        (self.root / "transfer.lock").touch(mode=0o600)
        atomic_json(self.root / "managed.json", {"version": 1, "namespace": "files", "ttl_hours": 1})
        self.store = RelayStore(self.root, "files")

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def job(self):
        path = self.root / "incoming/files/job"
        path.mkdir()
        manifest = {"version": 1, "job": "job", "name": "file", "bytes": 3, "sha256": "a" * 64}
        atomic_json(path / "manifest.json", manifest)
        atomic_json(path / "complete.json", {"job": "job", "bytes": 3, "sha256": "a" * 64})
        (path / "payload").write_bytes(b"abc")
        return path

    def test_release_is_scoped_verified_and_idempotent(self):
        path = self.job()
        with self.assertRaises(RelayError):
            self.store.release("job", "b" * 64)
        self.assertTrue((path / "payload").exists())
        self.assertEqual(self.store.release("job", "a" * 64)["released_bytes"], 3)
        self.assertFalse((path / "payload").exists())
        self.assertTrue(self.store.release("job", "a" * 64)["already_released"])
        with self.assertRaises(RelayError):
            self.store.release("../outside", "a" * 64)

    def test_gc_protects_active_transfers_and_respects_retention(self):
        path = self.job()
        self.assertEqual(self.store.gc()["removed"], [])
        future = time.time() + 7200
        with self.store.lock():
            self.assertEqual(self.store.gc(clock=future)["status"], "busy")
        preview = self.store.gc(clock=future, dry_run=True)
        self.assertEqual(preview["removed"], ["job"])
        self.assertTrue(path.exists())
        self.assertEqual(self.store.gc(clock=future)["removed"], ["job"])
        self.assertFalse(path.exists())

    def test_symlinks_cannot_escape_cleanup(self):
        path = self.job()
        outside = Path(self.temp.name) / "outside"
        outside.write_bytes(b"abc")
        (path / "payload").unlink()
        (path / "payload").symlink_to(outside)
        with self.assertRaises(RelayError):
            self.store.release("job", "a" * 64)
        (self.root / "incoming/files/escape").symlink_to(outside.parent)
        self.store.gc(clock=time.time() + 7200)
        self.assertEqual(outside.read_bytes(), b"abc")

    def test_installer_program_compiles_before_any_remote_mutation(self):
        compile(INSTALL, "<installer>", "exec")

    def test_installer_refuses_redeployment_and_foreign_controller_before_changes(self):
        home = self.root / "home"
        (home / ".ssh").mkdir(parents=True)
        auth = home / ".ssh/authorized_keys"
        auth.write_text('restrict ssh-ed25519 dGVzdA==\n')
        policy = {"version": 1, "namespace": "files", "user": "testrelay", "uid": os.getuid(),
                  "authorized_keys_sha256": hashlib.sha256(auth.read_bytes()).hexdigest()}
        atomic_json(self.root / "managed.json", policy)
        payload = {"user": "testrelay", "namespace": "files", "root": str(self.root),
                   "upgrade": False, "install_deps": False, "expected_public_keys": []}
        account = SimpleNamespace(pw_dir=str(home), pw_uid=os.getuid(), pw_gid=os.getgid())
        before = {str(p): p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        is_dir = Path.is_dir
        for upgrade in (False, True):
            payload["upgrade"] = upgrade
            encoded = base64.b64encode(json.dumps(payload).encode()).decode()
            with patch("os.geteuid", return_value=0), patch("pwd.getpwnam", return_value=account), \
                 patch("shutil.which", side_effect=lambda name: "/usr/bin/" + name), \
                 patch.object(Path, "is_dir", lambda p: str(p) == "/run/systemd/system" or is_dir(p)), \
                 patch("subprocess.run") as run:
                with self.assertRaises(SystemExit):
                    exec(INSTALL, {"PAYLOAD": encoded})
                run.assert_not_called()
            self.assertEqual(before, {str(p): p.read_bytes() for p in self.root.rglob("*") if p.is_file()})


if __name__ == "__main__":
    unittest.main()
