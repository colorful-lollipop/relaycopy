"""Real OpenSSH + rsync + rrsync, isolated on a random loopback port.

Run explicitly: RELAYCP_INTEGRATION=1 python3 -m unittest tests.test_integration -v
No existing keys, sshd configuration, or external hosts are used.
"""
import hashlib
import json
import os
import pwd
import signal
import shlex
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from relaycopy.common import RelayError, atomic_json, digest, read_json
from relaycopy.transport import Rsync, ssh_args

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(os.environ.get("RELAYCP_INTEGRATION") == "1", "set RELAYCP_INTEGRATION=1 for real SSH tests")
class SSHIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="relaycopy-ssh-")
        cls.base = Path(cls.temp.name)
        cls.user = pwd.getpwuid(os.getuid()).pw_name
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            cls.port = sock.getsockname()[1]
        cls.relay = cls.base / "relay"
        (cls.relay / "compute").mkdir(parents=True)
        cls.tools = cls.base / "tools"
        cls.keys = {}
        for name in ("host", "source", "upload", "read"):
            key = cls.base / (name + "_key")
            subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(key)], check=True)
            cls.keys[name] = key
        host_pub = cls.keys["host"].with_suffix(".pub").read_text().split()[:2]
        cls.known = cls.base / "known_hosts"
        cls.known.write_text(f"[127.0.0.1]:{cls.port} {' '.join(host_pub)}\n")
        auth = cls.base / "authorized_keys"
        auth.write_text(
            "restrict " + cls.keys["source"].with_suffix(".pub").read_text()
            + f'restrict,command="/usr/bin/rrsync -wo -munge -no-del {cls.relay}/compute" '
            + cls.keys["upload"].with_suffix(".pub").read_text()
            + f'restrict,command="/usr/bin/rrsync -ro {cls.relay}" '
            + cls.keys["read"].with_suffix(".pub").read_text()
        )
        cfg = cls.base / "sshd_config"
        cfg.write_text(f"Port {cls.port}\nListenAddress 127.0.0.1\nHostKey {cls.keys['host']}\n"
                       f"PidFile {cls.base}/sshd.pid\nAuthorizedKeysFile {auth}\nStrictModes no\n"
                       f"AllowUsers {cls.user}\nPasswordAuthentication no\nKbdInteractiveAuthentication no\n"
                       "UsePAM no\nLogLevel ERROR\n")
        cls.server_log = (cls.base / "sshd.log").open("wb")
        cls.sshd = subprocess.Popen(["/usr/sbin/sshd", "-D", "-e", "-f", str(cfg)], stdout=cls.server_log, stderr=cls.server_log)
        deadline = time.monotonic() + 8
        while True:
            if cls.sshd.poll() is not None:
                raise RuntimeError((cls.base / "sshd.log").read_text())
            try:
                with socket.create_connection(("127.0.0.1", cls.port), timeout=0.2):
                    break
            except OSError:
                if time.monotonic() > deadline:
                    raise RuntimeError("test sshd did not start")
                time.sleep(0.1)
        cls.endpoints = {name: {"host": f"{cls.user}@127.0.0.1", "port": cls.port,
                               "key_file": str(cls.keys[name]), "known_hosts": str(cls.known)}
                         for name in ("source", "upload", "read")}
        cls.config = cls.base / "config.json"
        atomic_json(cls.config, {
            "version": 1, "state_dir": str(cls.base / "state"),
            "profiles": {"default": {"relay": cls.endpoints["read"], "download_root": ".",
                                     "namespace": "compute", "upload": cls.endpoints["upload"]}},
            "sources": {"compute": {"ssh": cls.endpoints["source"], "namespace": "compute",
                                    "remote_dir": str(cls.tools),
                                    "upload_key_file": str(cls.keys["upload"]),
                                    "upload_known_hosts_file": str(cls.known),
                                    "upload": {"host": cls.endpoints["upload"]["host"], "port": cls.port}}},
        })

    @classmethod
    def tearDownClass(cls):
        for p in cls.tools.glob("jobs/*/process.json"):
            record = read_json(p)
            try:
                cmd = Path("/proc", str(record["pid"]), "cmdline").read_bytes()
                if b"_worker" in cmd and str(cls.tools).encode() in cmd:
                    os.killpg(record["pid"], signal.SIGTERM)
            except (ProcessLookupError, FileNotFoundError):
                pass
        cls.sshd.terminate()
        cls.sshd.wait(timeout=10)
        cls.server_log.close()
        cls.temp.cleanup()

    def cli(self, *args, check=True):
        p = subprocess.run([sys.executable, "-m", "relaycopy", "--config", str(self.config), *map(str, args)],
                           cwd=ROOT, capture_output=True, text=True, timeout=45)
        if check and p.returncode:
            self.fail(f"CLI failed: {p.stdout}\n{p.stderr}\nSSH: {(self.base / 'sshd.log').read_text()}")
        return p

    def test_single_file_waits_then_resumes_upload(self):
        source = self.base / "result with 'quotes';$(touch should-not-exist).bin"
        source.write_bytes(os.urandom(2 * 1024 * 1024))
        dest = self.base / "pipeline-out"
        dest.mkdir()
        args = ["compute:" + str(source), str(dest) + "/", "--id", "pipeline", "--poll", "0.2", "--bwlimit", "256", "--wait-seconds", "30"]
        controller = subprocess.Popen([sys.executable, "-m", "relaycopy", "--config", str(self.config), *args],
                                      cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        events = []
        try:
            for line in controller.stdout:
                event = json.loads(line)
                events.append(event)
                if event["event"] == "error":
                    self.fail(str(event) + "\n" + (self.tools / "jobs/pipeline/worker.log").read_text())
                if event["event"] == "waiting_for_upload":
                    payload = self.relay / "compute/pipeline/payload"
                    deadline = time.monotonic() + 5
                    while (not payload.exists() or payload.stat().st_size == 0) and time.monotonic() < deadline:
                        time.sleep(0.05)
                    self.assertFalse((dest / (source.name + ".partial.relaycp")).exists())
                    controller.send_signal(signal.SIGINT)
                    controller.communicate(timeout=10)
                    break
            else:
                self.fail("did not observe the receiver waiting for upload: " + str(events))
        finally:
            if controller.poll() is None:
                controller.terminate()
                controller.communicate(timeout=10)
        self.assertFalse((dest / source.name).exists(), "unverified data must not be published")
        record = read_json(self.tools / "jobs/pipeline/process.json")
        os.killpg(record["pid"], signal.SIGTERM)
        time.sleep(0.2)
        retained = (self.relay / "compute/pipeline/payload").stat().st_size
        self.assertGreater(retained, 0)
        self.assertLess(retained, source.stat().st_size)
        resumed = self.cli(*args)
        self.assertIn('"event": "verified"', resumed.stdout)
        self.assertEqual(digest(dest / source.name), digest(source))
        self.assertFalse((ROOT / "should-not-exist").exists())
        self.assertNotEqual(read_json(self.tools / "jobs/pipeline/process.json")["pid"], record["pid"])
        self.assertEqual(len(list((self.tools / "keys").glob("upload_key-*"))), 1)
        for key in (self.tools / "keys").glob("upload_key-*"):
            self.assertEqual(key.stat().st_mode & 0o777, 0o600)

    def test_multiple_files_pipeline_only_completed_files(self):
        a, b = self.base / "A.bin", self.base / "B.bin"
        a.write_bytes(os.urandom(65536))
        b.write_bytes(os.urandom(2 * 1024 * 1024))
        dest = self.base / "batch-out"
        dest.mkdir()
        args = ["compute:" + str(a), "compute:" + str(b), str(dest) + "/",
                "--id", "batch", "--poll", "0.2", "--bwlimit", "256", "--wait-seconds", "30"]
        process = subprocess.Popen([sys.executable, "-m", "relaycopy", "--config", str(self.config), *args],
                                   cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        overlap = False
        try:
            for line in process.stdout:
                event = json.loads(line)
                if event["event"] == "error":
                    self.fail(str(event) + "\n" + (self.tools / "jobs/batch/worker.log").read_text())
                if event["event"] == "verified" and event["job"] == "batch.000000":
                    self.assertTrue((self.relay / "compute/batch.000000/complete.json").exists())
                    self.assertFalse((self.relay / "compute/batch.000001/complete.json").exists())
                    self.assertFalse((dest / "B.bin.partial.relaycp").exists())
                    overlap = True
            process.wait(timeout=5)
        finally:
            if process.poll() is None:
                process.terminate()
                process.communicate(timeout=10)
            else:
                process.communicate()
        self.assertEqual(process.returncode, 0)
        self.assertTrue(overlap, "A must be received while B is still uploading")
        self.assertEqual(digest(dest / "A.bin"), digest(a))
        self.assertEqual(digest(dest / "B.bin"), digest(b))

    def test_local_partial_is_resumed(self):
        source = self.base / "resume-local.bin"
        source.write_bytes(os.urandom(256 * 1024))
        self.cli("send", source, "--id", "local-resume")
        dest = self.base / "resume-local-out"
        dest.mkdir()
        manifest = read_json(self.relay / "compute/local-resume/manifest.json")
        part = dest / (source.name + ".partial.relaycp")
        part.write_bytes(source.read_bytes()[:65536])
        atomic_json(str(part) + ".json", {"manifest": manifest,
                    "relay_host": self.endpoints["read"]["host"], "root": "compute/local-resume"})
        self.cli("receive", "relay://compute/local-resume", str(dest) + "/")
        self.assertEqual(digest(dest / source.name), digest(source))

    def test_send_receive_empty_and_existing_destination(self):
        source = self.base / "empty"
        source.touch()
        self.cli("send", source, "--id", "empty")
        dest = self.base / "empty-out"
        dest.mkdir()
        self.cli("receive", "relay://compute/empty", str(dest) + "/")
        again = self.cli("receive", "relay://compute/empty", str(dest) + "/")
        self.assertIn('"already_verified": true', again.stdout)
        (dest / "empty").write_bytes(b"different")
        rejected = self.cli("receive", "relay://compute/empty", str(dest) + "/", check=False)
        self.assertNotEqual(rejected.returncode, 0)
        self.assertEqual((dest / "empty").read_bytes(), b"different")

    def test_corrupt_payload_is_not_published(self):
        source = self.base / "corruption-source"
        source.write_bytes(os.urandom(65536))
        self.cli("send", source, "--id", "corruption")
        (self.relay / "compute/corruption/payload").write_bytes(b"x" * 65536)
        dest = self.base / "corruption-out"
        dest.mkdir()
        p = self.cli("receive", "relay://compute/corruption", str(dest) + "/", check=False)
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("SHA256 mismatch", p.stdout)
        self.assertFalse((dest / source.name).exists())

    def test_restricted_keys_deny_shell_and_wrong_direction(self):
        for name in ("read", "upload"):
            ssh, host = ssh_args(self.endpoints[name])
            p = subprocess.run(ssh + [host, "id"], capture_output=True, timeout=15)
            self.assertNotEqual(p.returncode, 0)
        local = self.base / "forbidden"
        local.write_bytes(b"test")
        with Rsync(self.endpoints["read"], self.base / "read-denied.log") as transport:
            with self.assertRaises(RelayError):
                transport.copy(local, "compute/forbidden", upload=True)
        with Rsync(self.endpoints["upload"], self.base / "upload-denied.log") as transport:
            with self.assertRaises(RelayError):
                transport.copy(self.base / "forbidden-read", "empty/payload")

    def test_keys_deny_forwarding_and_directory_escape(self):
        for name in ("read", "upload"):
            ssh, host = ssh_args(self.endpoints[name])
            denied = subprocess.run(ssh + ["-W", f"127.0.0.1:{self.port}", host], capture_output=True, timeout=15)
            self.assertNotEqual(denied.returncode, 0)
        (self.base / "outside.txt").write_text("outside the authorized relay tree")
        ssh, host = ssh_args(self.endpoints["read"])
        target = self.base / "escaped.txt"
        denied = subprocess.run(["rsync", "-e", shlex.join(ssh), host + ":../outside.txt", str(target)],
                                capture_output=True, timeout=15)
        self.assertNotEqual(denied.returncode, 0)
        self.assertFalse(target.exists())


if __name__ == "__main__":
    unittest.main()
