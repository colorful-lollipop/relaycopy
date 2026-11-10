# Deploy a relay when the user asks

Prerequisites: a trusted administrator SSH alias to a Linux/systemd relay, root
or passwordless sudo, Python >=3.10; local OpenSSH, ssh-keygen and Git. The CLI
can install relay rsync/rrsync/flock dependencies from Debian/Ubuntu packages
when `--install-deps` is supplied. It does not install packages on source hosts.

Build the CLI from the checked-out project if no installed command is available:

```sh
python3 scripts/build.py
./dist/relaycp.pyz setup-relay --ssh ADMIN_ALIAS --install-deps --apply
```

Use `--sudo` for a non-root administrator. Omit `--apply` to inspect the plan.
Use a new dedicated Unix user and root per person/team; defaults are `relaycopy`
and `/srv/relaycopy`. An unmanaged existing account or directory is rejected.
Do not weaken host-key checking, reuse a host administrator key for transfers,
or point the managed root at existing user data.

Default output is `~/.config/relaycopy/config.json`, with separate upload/read/
maintenance keys alongside it. Keep that directory outside every Git worktree,
mode 0700, keys mode 0600. Never print, commit or upload its contents. The user
should retain a private backup. Record only config location and deployment result.

Then run `relaycp doctor` and `relaycp gc --dry-run`, and use a small authorized
source file for an end-to-end check. Verify the local file and `relay_released`.
The source only needs its normal SSH alias; the CLI bootstraps its small worker.

Managed cleanup is single-consumer by default. Verified relay payloads release
immediately; stale jobs expire after 24 hours when the exclusive GC lock can be
obtained, checked by systemd every 15 minutes. For multiple intended receivers,
set `cleanup_after_receive: false` before transfer; TTL still applies. Storage
quotas, additional network isolation and long-term archives are separate concerns.

If setup fails, inspect its private setup.log and any recorded deployment receipt
before retrying. It may already have created the dedicated account. Never remove
an unmanaged account or replace another user's keys to force setup through.
