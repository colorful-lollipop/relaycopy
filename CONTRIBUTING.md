# Contributing

Keep RelayCopy an orchestration layer over maintained external tools. Prefer an
OpenSSH/rsync feature to a new transport, server, authentication system, or SDK.

Use Python's standard library and keep the zipapp usable with Python 3.10+.
Changes to authentication, bootstrap, transfer publication or resume need tests
against observable behavior, including a failure case. New metadata fields must
either preserve version-1 manifests or explicitly bump the format version.

Run unit/maintenance tests, the isolated SSH integration suite and the zipapp build from
README.md. The integration suite needs OpenSSH server/client, ssh-keygen, rsync
and `/usr/bin/rrsync`; it binds only a random loopback port and uses throwaway
credentials. Never point it at production. Do not commit credentials, private
configs, generated runtime state or test host keys.

Changes to retention must preserve the shared-rsync/exclusive-GC lock protocol
and digest-scoped release. Test cross-role ControlMaster reuse, stale/active
jobs, symlinks, idempotent release, and normal batch resume before publishing.

Open an issue describing expected and actual behavior before a large feature.
Include OS, Python, OpenSSH and rsync versions, command shape and redacted errors.
For pull requests, describe the user-visible change and relevant validation.
See SECURITY.md for suspected vulnerabilities.
