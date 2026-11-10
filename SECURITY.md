# Security

Supported release line: 0.2.x. This project has functional/adversarial tests and
secret scanning; it has not had an independent security audit. Keep OpenSSH and
rsync distribution packages patched.

Use this repository's GitHub private vulnerability reporting for suspected
vulnerabilities. Do not disclose credentials or exploitable deployment details
in public issues. Ordinary bug reports should use redacted logs.

## Boundaries

- Administrator access is used only for explicit `setup-relay --apply`. It creates
  a separate Unix account/root and does not change global sshd or unrelated users.
- Upload, read and maintenance keys are separate capabilities selected by SSH
  authentication. Data transports use private ControlMasters; maintenance forbids
  reusing an existing master authenticated with a different role.
- A source receives only an explicitly configured, namespace-scoped upload key.
  Someone controlling that source can use that capability; use separate profiles
  for different trust groups. Administrator/read/maintenance keys stay local.
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
