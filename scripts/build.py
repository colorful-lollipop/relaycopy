#!/usr/bin/env python3
"""Build the dependency-free, deterministic SSH bootstrap zipapp."""
import hashlib
import json
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root))
from relaycopy.bootstrap import bundle

out = root / "dist" / "relaycp.pyz"
out.parent.mkdir(exist_ok=True)
out.write_bytes(bundle())
out.chmod(0o755)
print(json.dumps({"artifact": str(out), "bytes": out.stat().st_size,
                  "sha256": hashlib.sha256(out.read_bytes()).hexdigest()}))
