import tempfile
import unittest
from pathlib import Path

from relaycopy.cli import normalize, ticket_parts
from relaycopy.common import RelayError, identifier, locked, make_manifest, remote_path, validate_manifest


class Invariants(unittest.TestCase):
    def test_remote_paths_reject_shell_and_traversal(self):
        for path in ("../x", "a/../b", "a;id", "$(id)", "a b", "//x", "-evil"):
            with self.subTest(path=path), self.assertRaises(RelayError):
                remote_path(path)
        self.assertEqual(remote_path("/srv/team/a-1/payload"), "/srv/team/a-1/payload")

    def test_safe_tickets_and_ids(self):
        self.assertEqual(ticket_parts("relay://team/rc-123"), ("team", "rc-123"))
        for ticket in ("relay://team/../x", "http://team/job", "relay://team/job?password=x", "relay://team/a/b"):
            with self.subTest(ticket=ticket), self.assertRaises(RelayError):
                ticket_parts(ticket)
        with self.assertRaises(RelayError):
            identifier("--option")

    def test_manifest_and_source_symlink(self):
        with tempfile.TemporaryDirectory() as d:
            source = Path(d) / "spaces and 中文.bin"
            source.write_bytes(b"hello")
            m, _ = make_manifest(source, "test")
            self.assertEqual(validate_manifest(m, "test"), m)
            with self.assertRaises(RelayError):
                validate_manifest({**m, "name": "../../escape"}, "test")
            link = Path(d) / "link"
            link.symlink_to(source)
            with self.assertRaises(OSError):
                make_manifest(link, "test")

    def test_locks_reject_duplicate_work(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "lock"
            with locked(path), self.assertRaises(RelayError):
                with locked(path):
                    pass

    def test_batch_identity_and_names_are_validated(self):
        manifest = {"version": 1, "kind": "batch", "job": "batch", "files": [
            {"job": "batch.000000", "name": "A"}, {"job": "batch.000001", "name": "B"}]}
        self.assertEqual(validate_manifest(manifest, "batch"), manifest)
        for files in ([{"job": "another-job", "name": "A"}],
                      [{"job": "batch.000000", "name": "../escape"}],
                      [{"job": "batch.000000", "name": "A"}, {"job": "batch.000001", "name": "A"}]):
            with self.subTest(files=files), self.assertRaises(RelayError):
                validate_manifest({**manifest, "files": files}, "batch")

    def test_implicit_copy_preserves_global_flags(self):
        self.assertEqual(normalize(["--config", "config.json", "--json", "compute:/x", "."]),
                         ["--config", "config.json", "--json", "copy", "compute:/x", "."])


if __name__ == "__main__":
    unittest.main()
