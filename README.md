# RelayCopy

**`relaycp compute:/data/A.tar.gz compute:/data/B.tar.gz ./results/`**

Copy remote files through your own relay using **OpenSSH, rsync and rrsync**.
Install the CLI only on your local machine. It automatically delivers a small
Python worker to the source using your existing SSH alias. Payload bytes travel
source → relay → receiver, outside your source-control/bastion connection.

[中文说明](README.zh-CN.md) · [Relay setup](server/README.md) · [Security](SECURITY.md)

## Quick start

Source/receiver: Linux, Python >=3.10, OpenSSH clients and rsync >=3.2.3.
Managed relay: Debian/Ubuntu with systemd, Python >=3.10 and an existing trusted
administrator SSH connection (root or passwordless sudo). Other distributions
must have the dependencies preinstalled. macOS/Windows are not yet tested.

```sh
git clone https://github.com/colorful-lollipop/relaycopy.git
cd relaycopy
python3 scripts/build.py

# One-time setup; creates an isolated account, scoped keys and retention timer.
./dist/relaycp.pyz setup-relay --ssh my-relay-admin --install-deps --apply

# Thereafter: only your normal source SSH alias and local destination are needed.
./dist/relaycp.pyz compute:/data/A.tar.gz compute:/data/B.tar.gz ./results/
```

Use `--sudo` when the administrator SSH account requires passwordless sudo.
Alternatively, download the versioned `relaycp.pyz` and `SHA256SUMS` from
[Releases](https://github.com/colorful-lollipop/relaycopy/releases), verify the
checksum, and invoke it with `python3 relaycp.pyz`.
Omit `--apply` to inspect a setup plan. Setup never changes global sshd settings,
opens a new listener, or replaces another account's authorized_keys. It refuses
an existing unmanaged account/directory. Distribution package installation
requires `--install-deps`; no source packages are installed automatically.

A single local `~/.config/relaycopy/config.json` holds relay connection details;
upload, read and maintenance private keys stay beside it, mode 0600 in an
owner-only directory. Setup refuses storage inside a Git working tree. The
source receives **only the namespace-scoped, write-only upload key**, over SSH
stdin. Never delegate a host administrator key. Public code contains no credentials.

For multiple people/teams, deploy distinct users/roots and keep credentials
separate, e.g. `--user relaycopy-alice --root /srv/relaycopy-alice`. One profile
can use any SSH source alias; a separate source entry is no longer required.
See `config.example.json` for a placeholder managed profile. Legacy explicit
source configurations remain supported.

Optionally install from the local checkout into your virtual environment with
`python3 -m pip install .`, which provides the `relaycp` command. No PyPI release
has been published. The zipapp has no third-party Python runtime dependencies.

## File-level pipeline and cleanup

1. Source uploads A using resumable rsync, then publishes its completion receipt.
2. Receiver downloads and SHA256-verifies A while the source uploads B.
3. Receiver atomically publishes A locally, then releases A's relay payload.
4. A systemd timer checks every 15 minutes for stale jobs; default retention is
   24 hours. An exclusive `flock` prevents GC while any managed rsync is active.

There is **no same-file streaming**, custom chunk protocol, or repeated reading
of growing payloads. Partial files remain available for retry. Original source
files are never removed. Only managed relay storage is reclaimed.

The maintenance key accepts only fixed `info`, `release JOB SHA256`, `gc` and
`gc-dry-run` operations on its configured namespace. It cannot run a shell,
forward ports, or choose an arbitrary filesystem path. A release requires matching
file manifest and completion metadata. A cleanup failure is reported, and the
local verification receipt records `cleanup_pending` for retry.

Default managed profiles are **single-consumer**: successful verification
releases that file, so another destination must start a fresh job. For intentional
multiple receivers, set `cleanup_after_receive: false`; TTL retention still
applies. Tickets locate data and grant no permissions.

```sh
relaycp gc --dry-run
relaycp gc
```

Retention prevents abandoned jobs from accumulating indefinitely during idle
periods. It is not a hard disk quota: a large active batch can still exceed
available space. Use a dedicated filesystem/OS quota for hard isolation. Long
active transfers defer GC until a later timer run, rather than being deleted.

## Resume and agent handoff

```sh
relaycp compute:/data/A.tar.gz ./ --id rc-PREVIOUS_ID
relaycp status relay://files/rc-PREVIOUS_ID
relaycp receive relay://files/rc-PREVIOUS_ID ./
```

Keep the first JSON event's job ID/ticket, local state directory and source
process receipt. The worker detaches after bootstrap; stopping the local CLI may
leave the uploader running. Rerun with the same source, destination, profile and
ID. Completed source files are not uploaded again when a batch resumes. A
released/expired payload needs a new job ID if copying to a new destination.

Only `verified` proves one local file is complete; `batch_verified` proves the
whole batch is complete. `relay_released` confirms relay payload removal. Relay
`status: complete` proves upload completion, while `incomplete` does not establish
worker liveness. Inspect the recorded source log/state on failure. A local
SHA256 mismatch or conflicting destination is rejected without overwriting it.

Paths with spaces must be quoted. The CLI supports ordinary completed files
from one source host per invocation, plus explicit local `send` / `receive`.
It accepts an **scp-style syntax subset**, not the SCP/SFTP wire protocol. Native
scp cannot use forced-rsync keys. Recursive copies, globs and one-command
local-to-remote/remote-to-remote copies are not implemented.

## Agent Skill

Give your agent `skills/relaycopy/SKILL.md` and a trusted administrator SSH alias:

> Deploy a RelayCopy relay using my-relay-admin, save the private config locally,
> then copy compute:/data/A.tar.gz into this directory.

For Codex, install `skills/relaycopy/` under the consuming project's
`.agents/skills/`. Other harnesses can use their skill location or read SKILL.md
explicitly. The same CLI works without an agent SDK or MCP service. Setup needs
explicit deployment authorization; ordinary transfers do not provision accounts.

## Development

```sh
python3 -m unittest tests.test_unit tests.test_relay -v
RELAYCP_INTEGRATION=1 python3 -m unittest tests.test_integration -v
python3 scripts/build.py
```

Tests run real OpenSSH/rsync/rrsync on a random loopback port with generated keys;
they never use production credentials. CI includes Python 3.10/3.12 and a
checksum-pinned Gitleaks scan of all Git history. See [validation](docs/VALIDATION.md)
and [contribution guidance](CONTRIBUTING.md).

MIT license for original code. OpenSSH, rsync, rrsync and util-linux flock are
external tools with their own licenses; no upstream binaries are vendored.
