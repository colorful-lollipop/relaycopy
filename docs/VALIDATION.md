# v0.1 validation

Recorded 2026-11-10. Tested locally on Linux with Python 3.12.3, OpenSSH and rsync.
The source worker zipapp was also exercised on Linux/Python 3.11.2/rsync 3.2.7
through an existing SSH/rsync relay. This is bounded functional validation, not a
throughput benchmark or a security audit.

## Automated tests

Six standard-library unit tests cover CLI routing, relay path injection and
traversal rejection, ticket and batch identity validation, source symlinks, and
duplicate job locks.

Seven isolated integration tests use generated ephemeral keys and a real sshd
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

The source bootstrap test includes spaces, quotes and shell metacharacters in a
filename and verifies delegated credentials have mode 0600. Tests use no real
credentials and never contact external infrastructure.

## Limits

Python 3.10 compatibility is included in CI configuration but was not run locally.
GitHub-hosted CI has not run because the repository has not been published.
Windows/macOS, very large files, storage exhaustion, network fairness, many
simultaneous users, and long-running reliability have not been validated.

Run the commands in README.md to reproduce the local suite. Live environment
addresses, configs and operational evidence belong outside this public source
repository and are not included in its Git history.
