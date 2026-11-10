# Managed relay setup

Use the CLI's one-time deployment from an authenticated administrator SSH alias:

```sh
python3 scripts/build.py
./dist/relaycp.pyz setup-relay --ssh relay-admin --install-deps --apply
```

Add `--sudo` for a non-root login with passwordless sudo. Without `--apply`, the
command emits a plan. Prerequisites: running Linux/systemd, Python >=3.10,
OpenSSH server, rsync/rrsync >=3.2.3, util-linux flock and OpenSSL. Optional
package installation uses Debian/Ubuntu's signed package manager. Source hosts
are not modified except for worker/credential delivery under their user's cache.

Defaults: user `relaycopy`, root `/srv/relaycopy`, namespace `files`, TTL 24 hours.
For another user/team choose a separate root and Unix user. An existing unmanaged
account or directory is rejected. This command does not modify global sshd or
another account's keys. Setup needs root only once; payload and maintenance
operations run as the dedicated unprivileged account.

One private Ed25519 key is generated on the local controller, mode 0600. Only
public keys reach the relay. Host public keys are obtained through the existing
authenticated administrator connection and pinned for subsequent connections.
Local configuration is refused inside Git worktrees. Keep its entire directory
private, including deployment receipts, and back it up through your normal
private credential process, not a public repository.

The account has one `restrict` forced-command key. The dispatcher accepts:

- Upload: shared `flock`, then `rrsync -wo -munge -no-del` in its namespace.
- Download: shared `flock`, then `rrsync -ro` over that profile's incoming tree.
- Maintenance: fixed namespace-scoped `info`, `release JOB SHA256`, `gc`,
  `gc-dry-run`.

The dispatcher does not run arbitrary commands or parse the rsync protocol;
rrsync retains responsibility for option/path validation. Shell, forwarding,
PTY and arbitrary path deletion remain forbidden. The same key is delegated to
sources, so they possess all three capabilities for this profile.

To migrate an existing generated controller, run
`setup-relay --ssh relay-admin --upgrade --apply`. Its recorded root/user/namespace
and TTL are retained; current credentials must match the server's authorized
keys. The old upload identity is reused as `relay_ed25519`. Original config and
keys are moved to a private backup directory; old read/maintenance identities are
revoked. Update other controllers before their next connection. A new deployment
must choose a different user/root instead of replacing an existing deployment.

A receiver releases a file only after local SHA256 verification and publication.
Release checks matching file manifest and completion metadata, uses directory
file descriptors, rejects symlinks and is idempotent. Small receipts remain for
resume until TTL expiration. Source files are untouched.

`relaycopy-USER.timer` runs GC every 15 minutes. GC uses a nonblocking exclusive
flock, so any managed rsync writer/reader makes it skip the run. It considers
only aged job directories in the new managed namespace, uses inode-change times
instead of source-preserved mtimes, and does not follow client symlinks. The
service uses an unprivileged user, NoNewPrivileges and a restricted writable tree.

For multi-receiver retention set `cleanup_after_receive: false`. TTL still
expires retained data. Retention is not a hard quota; use a separate filesystem
or OS quota for storage limits. Never point this managed root at other users'
data. Network controls are still needed to hide unrelated services on the host.

For advanced existing deployments, `scripts/prepare_relay.py` prints options for
manually provisioned restricted rsync keys. Legacy relays need explicit source
configuration; `config.example.json` shows a managed profile. Legacy profiles do
not receive automatic cleanup without the managed maintenance capability. Do
not give a transfer agent the relay's administrator key as a substitute.
