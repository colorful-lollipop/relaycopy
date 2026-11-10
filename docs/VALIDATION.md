# v0.3 validation

Recorded 2026-11-10. Tested locally on Linux with Python 3.12.3, OpenSSH and rsync.
The source worker zipapp was also exercised on Linux/Python 3.11.2/rsync 3.2.7
through a managed SSH/rsync relay after upgrading from three keys to one. Two
completed files verified locally and both relay payloads were released. This is bounded functional validation, not a
throughput benchmark or a security audit.

## Automated tests

Eleven standard-library unit/maintenance tests cover CLI routing, relay path
injection and traversal, ticket/batch identities, duplicate locks, digest-scoped
idempotent release, TTL and active-transfer protection, symlink-safe GC, and refusal to overwrite an existing/foreign managed deployment.

Ten isolated integration tests use generated ephemeral keys and a real sshd
on a random loopback port. They invoke real rsync and forced-command rrsync:

| Scenario | Expected behavior |
| --- | --- |
| Two-file pipeline | A is verified locally while B's upload receipt is still absent |
| Single-file wait | No local payload is created while its source is uploading |
| Source/controller interruption | Same job restarts, retaining the relay prefix |
| Local partial | An existing valid prefix resumes and verifies |
| Corrupt relay payload | No final file is published |
| Empty file / existing destination | Empty file works; matching file skips; different file remains untouched |
| Restricted credentials | Shell, forwarding, directory escape and wrong transfer direction are denied |
| Managed profile | Source SSH alias is inferred; verified relay payload is removed; resume does not reupload it |
| Unified key | One key uploads, downloads, releases and runs GC; shell, forwarding, traversal and rsync deletion are denied |
| Legacy role isolation | Maintenance does not reuse a ControlMaster authenticated with a read-only key |

The source bootstrap test includes spaces, quotes and shell metacharacters in a
filename and verifies delegated credentials have mode 0600. Tests use no real
credentials and never contact external infrastructure.

## Limits

The local v0.3 suite passed 21 tests. GitHub CI runs Python 3.10/3.12 unit and
maintenance tests, real SSH integration and a scan of all Git history for secrets;
see the [Actions results](https://github.com/colorful-lollipop/relaycopy/actions).
Windows/macOS, very large files, storage exhaustion, network fairness, many
simultaneous users, and long-running reliability have not been validated.

Run the commands in README.md to reproduce the local suite. Live environment
addresses, configs and operational evidence belong outside this public source
repository and are not included in its Git history.
