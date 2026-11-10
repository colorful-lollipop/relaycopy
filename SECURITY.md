# Security

This is an early v0.1 project, not a security-audited transfer service. The
supported release line is 0.1.x. Use supported, patched OpenSSH and rsync packages.

Report suspected vulnerabilities privately to the repository maintainer using
the hosting platform's private vulnerability reporting, when enabled. Do not
post credentials, server addresses, private configs or exploitable deployment
details in public issues. No security contact address or hosted reporting endpoint
has been established yet; this repository has not been published.

## Trust boundaries

- Source and relay storage are controlled by their operators. Source files must
  be complete and unchanged; namespaces and job IDs must have a single owner.
- A delegated source key is a real credential. Use only a forced-command,
  write-only, namespace-restricted key; it remains readable by the source's OS
  account. The program never delegates a default/admin SSH key automatically.
- Receiver credentials must be read-only on the relay. rrsync and filesystem
  permissions enforce this. Skills and client-side validation do not.
- Host keys must already be authenticated. Strict host-key checking stays on.
  Source control connections reuse the user's SSH configuration, so choose that
  configuration and its ProxyCommand/Match rules with care.
- The relay provides availability and shared storage. A write credential can
  overwrite its own namespace. Protect retained jobs from unintended writers.
- Final verification detects corruption against a manifest, not malicious
  content from an authorized sender. Do not automatically extract archives.

RelayCopy never provisions users/keys during an ordinary transfer, modifies
global sshd settings, installs source packages, or automatically removes relay
or source data. Administrator provisioning is a separate, explicit operation.
