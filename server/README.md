# Relay setup: existing OpenSSH and rsync only

RelayCopy does not run an application server. A relay needs OpenSSH `sshd`,
rsync >=3.2.3, and `rrsync` (from the rsync project). Keep these maintained
through your distribution. Source and receiver need OpenSSH clients, rsync
>=3.2.3, and Python >=3.10. The remote worker is a zipapp, with no pip packages.

Use a **dedicated Unix login and data directory**, not your administrator login.
For colleagues, give each person/team separate credentials and a separate tree.
Directory restrictions come from `rrsync` and OS permissions, not the Skill.

Generate two Ed25519 keys in the environment that owns the credentials:

```sh
ssh-keygen -t ed25519 -f relay-upload
ssh-keygen -t ed25519 -f relay-read
```

Noninteractive credentials must already be available to SSH. Automated delegation
uses a dedicated unencrypted, write-only key file with mode 0600; do not delegate
an administrator key. SSH-agent-backed local read credentials may be used through
an ordinary SSH config alias.

An administrator can prepare a **new** tree using PUBLIC keys:

```sh
python3 scripts/prepare_relay.py \
  --root /srv/relaycopy/alice/incoming --namespace compute \
  --upload-public-key relay-upload.pub --read-public-key relay-read.pub
```

Run as the dedicated login, or assign the new tree to that login before use. The
command prints two restricted `authorized_keys` entries. Review and add only
these entries to that account's existing authorized_keys, preserving its other
entries and ensuring a separating newline. The script never edits that file or
global sshd configuration. The public-key options are equivalent to:

```text
restrict,command="/usr/bin/rrsync -wo -munge -no-del /srv/relaycopy/alice/incoming/compute" UPLOAD_PUBLIC_KEY
restrict,command="/usr/bin/rrsync -ro /srv/relaycopy/alice/incoming" READ_PUBLIC_KEY
```

The upload key cannot read files, run a shell, forward ports, or delete using
rsync. It can overwrite files in its own namespace: use new random job IDs for
new content. The read key can only serve rsync reads from this team's tree.
Neither key is suitable for native `scp`, which speaks a different protocol.

Pin the relay host key from an authenticated management session or a separately
verified fingerprint. Do not trust an unauthenticated `ssh-keyscan` result alone.

Example receiver SSH config:

```sshconfig
Host relay-read
    HostName relay.example.org
    Port 2222
    User relayuser
    IdentityFile ~/.ssh/relay-read
    IdentitiesOnly yes
    StrictHostKeyChecking yes
```

Test shell denial and both wrong transfer directions before distributing keys.
The isolated integration tests demonstrate these checks with real OpenSSH.

Capacity and retention remain administrator decisions. This version stores the
whole file on the relay, does not enforce per-person quotas, and never deletes
relay/source files automatically. Use a dedicated filesystem with a capacity
limit or OS quotas and an explicitly agreed retention policy. Restrict access to
the relay account's other ports through network controls if that is required;
file-account isolation does not hide unrelated listeners on a shared IP.
