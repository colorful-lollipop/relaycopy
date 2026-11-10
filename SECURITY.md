# Security

Supported release line: 0.3.x. This project has functional/adversarial tests and
secret scanning; it has not had an independent security audit. Keep OpenSSH and
rsync distribution packages patched.

Use this repository's GitHub private vulnerability reporting for suspected
vulnerabilities. Do not disclose credentials or exploitable deployment details
in public issues. Ordinary bug reports should use redacted logs.

## Boundaries

- Administrator access is used only for explicit `setup-relay --apply`. It creates
  a separate Unix account/root and does not change global sshd or unrelated users.
- A single restricted key permits upload, download and scoped maintenance. A
  forced-command dispatcher sends rsync requests to the distribution's rrsync;
  it never evaluates the supplied command as shell code. Rsync cannot delete;
  cleanup is provided only through the fixed namespace/job/digest operations.
- The same key is delegated to a source. Someone controlling that source has
  all three relay capabilities within this profile. Use separate Unix users,
  roots and profiles for different trust groups. Administrator identities stay
  local. Legacy three-key client configurations remain supported.
- Upgrade requires both an administrator SSH connection and the existing
  controller credentials to match the relay. Fresh setup refuses an existing
  managed relay; it cannot silently replace another controller's keys.
- Local private configuration must be outside Git. Secret keys have mode 0600,
  parent directories mode 0700. Public source/history and distribution artifacts
  must be scanned before publishing; .gitignore is only an additional guard.
- Source files must remain unchanged. Random IDs and local locks do not provide
  distributed ownership against malicious writers with the same upload key.
- Local files publish only after size/SHA verification, without overwriting
  conflicting content. SHA verifies consistency, not trustworthiness of content.
  Do not automatically extract received archives.
- Release is namespace/job/digest scoped. Cleanup never removes source files.
  GC requires an exclusive lock against managed rsync and only ages this project's
  new dedicated namespace. Storage modified outside this mechanism is outside its
  concurrency guarantees. Direct shell access to the relay Unix account must not
  be distributed to transfer users.
- Immediate cleanup is single-consumer; use retained mode for multiple receivers.
  Expired/abandoned jobs are eventually removed when the GC lock is available.
  A hard disk quota and protection against a malicious uploader filling storage
  require OS/filesystem limits; TTL is not a quota.

Setup/maintenance payloads and secrets travel through authenticated SSH stdin,
not shell arguments or public downloads. Credentials, actual endpoints and live
operational evidence do not belong in this repository.
