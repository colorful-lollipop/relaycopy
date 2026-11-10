#!/usr/bin/env python3
"""Prepare a new isolated relay tree and print PUBLIC authorized_keys entries.

Does not touch sshd configuration, existing authorized_keys, or private keys.
"""
import argparse
import base64
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from relaycopy.common import identifier, remote_path


def public_key(path):
    lines = Path(path).read_text().strip().splitlines()
    if len(lines) != 1:
        raise ValueError("expected exactly one public key")
    fields = lines[0].split()
    if len(fields) < 2 or not re.fullmatch(r"(?:ssh-ed25519|ssh-rsa|ecdsa-sha2-nistp(?:256|384|521))", fields[0]):
        raise ValueError("expected a public key without authorized_keys options")
    base64.b64decode(fields[1], validate=True)
    return " ".join(fields[:2])


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--root", required=True, help="new dedicated tree, owned by the relay login")
    p.add_argument("--namespace", required=True)
    p.add_argument("--upload-public-key", required=True)
    p.add_argument("--read-public-key", required=True)
    p.add_argument("--rrsync", default="/usr/bin/rrsync")
    a = p.parse_args()
    root = Path(remote_path(a.root)).absolute()
    namespace = identifier(a.namespace)
    rrsync = remote_path(a.rrsync)
    upload = public_key(a.upload_public_key)
    read = public_key(a.read_public_key)
    if upload == read:
        p.error("use different keys for upload and read access")
    root.mkdir(parents=True, mode=0o700, exist_ok=False)
    (root / namespace).mkdir(mode=0o700)
    print(f'restrict,command="{rrsync} -wo -munge -no-del {root}/{namespace}" {upload}')
    print(f'restrict,command="{rrsync} -ro {root}" {read}')


if __name__ == "__main__":
    main()
