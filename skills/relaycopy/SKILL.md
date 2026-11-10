---
name: relaycopy
description: Deploy an authorized SSH/rsync relay with local private configuration, transparently copy completed remote files, resume transfers, and reclaim verified relay payloads. Use the relaycp CLI; source originals are retained.
---

# RelayCopy

Use `relaycp` or the distributed `relaycp.pyz`. Run `--version` and `doctor`
before the first transfer in a new environment. Credentials and endpoints live
in the user's configuration, never in this skill.

If explicitly asked to deploy a relay, read [deployment guidance](references/deployment.md).
Ordinary transfers use an existing configuration and do not create accounts.
Managed profiles work with any existing source SSH alias; no per-source entry is
needed. The source automatically receives only the dedicated write-only key.

```sh
relaycp source-alias:/absolute/completed-file ./destination/
relaycp source-alias:/data/A source-alias:/data/B ./destination/
relaycp source-alias:/absolute/completed-file ./destination/ --id PREVIOUS_ID
relaycp status relay://namespace/JOB
relaycp receive relay://namespace/JOB ./destination/
```

The source must be a finished regular file. Quote paths containing shell special
characters. The first JSON event contains the ticket and resume ID; preserve
these plus the source process receipt and local request path for another agent.
The CLI bootstraps a small worker over SSH stdin and detaches it; file bytes go
source → relay → receiver. Do not hold an extra SSH session to monitor it.

Read JSON Lines events. `verified` establishes completion for one file;
`batch_verified` establishes completion for all files in a batch. A file is
downloaded only after its upload receipt; A can download while B uploads. `status`
with `complete` means the relay has an upload completion receipt, not that a
local copy is verified. `incomplete` does not prove the source worker is alive.

On interruption, preserve partial files, their JSON identity sidecar and the
state directory. Resume with the same source, destination, profile and ID; never
delete partial files to retry or reuse an ID for changed content. The source
uploader may continue after the local CLI exits. Authentication/host-key/permission
errors require diagnosis, not more concurrent attempts or weaker SSH checks.

An authorized receiver can use a ticket independently, but the ticket grants no
permissions. Existing conflicting destination content is retained and rejected.
Managed profiles release a file's relay payload only after local verification;
`relay_released` confirms that action. Original source files are never removed.
The default is single-consumer; copying a released ticket to another destination
requires a new job, or prior explicitly configured retained mode. Cleanup errors
are not download corruption: preserve the verified local file and retry with the
same request to finish cleanup. `gc --dry-run` previews retention; `gc` applies
the configured scope/TTL. Do not manually rm paths, users or keys.
Do not pass a host management/read private key as `upload_key_file`.

v0.2 supports regular files from one source host to a local path, and explicit
`send`/`receive`. Multiple files require a destination directory.
It is scp-style syntax, not SCP/SFTP wire compatibility; recursive copies and
one-command reverse copies are not supported. Consult `relaycp --help` for flags.
