# Installation, HTTP 403 and SSH failures

Start with the exact command, CLI version, failing stage and redacted error/log.
Never share private keys, passwords, tokens, or a private configuration bundle.

## HTTP 403

RelayCopy transfers and maintenance use SSH/rsync, with no HTTP application
endpoint. A 403 alone does not identify a relay-key problem. Locate its source:

| Stage | Inspect |
| --- | --- |
| Downloading source/CLI/Skill | Failing GitHub/API/asset URL, HTTP response headers, proxy policy |
| Installing relay packages | Private `setup.log`, distribution package mirror and proxy |
| Connecting through an SSH proxy | SSH log and the alias's `ProxyCommand`; an HTTP CONNECT proxy can deny a connection |
| SSH authentication | `Permission denied (publickey)`, correct user/key/port and pinned host key |
| rsync / maintenance | Log path printed by the CLI; direction, namespace, receipt and server version |

GitHub API rate limits can return 403/429. Check `x-ratelimit-remaining`,
`x-ratelimit-reset` and `retry-after`; follow the
[official troubleshooting guidance](https://docs.github.com/en/rest/using-the-rest-api/troubleshooting-the-rest-api).
Do not assume every 403 is a rate limit or retry indefinitely.

A versioned asset URL avoids querying the GitHub API for `latest`:

```sh
curl --fail --location --output relaycp.pyz https://github.com/colorful-lollipop/relaycopy/releases/download/v0.3.0/relaycp.pyz
curl --fail --location --output SHA256SUMS https://github.com/colorful-lollipop/relaycopy/releases/download/v0.3.0/SHA256SUMS
sha256sum -c SHA256SUMS
python3 relaycp.pyz --version
```

This does not bypass a blocked asset domain. If downloads are denied, inspect
the approved proxy/network route or clone the public repository and build it.
Do not weaken SSH host-key verification or substitute a relay private key for a
GitHub token.

## Existing relay / version upgrade

Fresh setup refuses an existing managed root. Use a distinct user/root for a
new person, or `setup-relay --ssh ADMIN --upgrade --apply` with the original
controller configuration and deployment receipt. Missing or mismatched ownership
is a reason to inspect the deployment, not replace its keys.

An upgrade to v0.3 reuses the old upload identity as the unified key. Old separate
read/maintenance identities cease to authorize new connections. Keep the private
backup and update other controllers; never upload the backup to a public issue.
