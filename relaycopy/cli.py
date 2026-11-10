"""Public CLI: familiar copy syntax and portable tickets for other agents."""

import argparse
import json
import os
import shutil
import signal
import subprocess
import sys
import uuid
from pathlib import Path
from urllib.parse import urlparse

from .bootstrap import launch
from .common import RelayError, VERSION, atomic_json, emit, identifier, join_remote, read_json
from .transfer import pull, push_many, status


def parser():
    p = argparse.ArgumentParser(prog="relaycp", description="Copy through a relay using existing SSH and rsync. Default: remote:file local-path")
    p.add_argument("--version", action="version", version=VERSION)
    p.add_argument("--config", default=os.environ.get("RELAYCP_CONFIG", "~/.config/relaycopy/config.json"))
    p.add_argument("--profile", default="default")
    p.add_argument("--json", action="store_true", help="stdout is always JSON Lines; accepted for automation")
    sub = p.add_subparsers(dest="command", required=True, metavar="{copy,send,receive,status,doctor}")
    copy = sub.add_parser("copy", help="one-command remote-to-local copy; 'copy' may be omitted")
    copy.add_argument("sources", nargs="+", help="one or more SSH-source-alias:/absolute/path on the same host")
    copy.add_argument("dest", help="local file or directory")
    copy.add_argument("--id", help="reuse the ID printed on a previous interrupted copy")
    copy.add_argument("--bwlimit", type=int, help="source rsync bandwidth limit in KiB/s")
    waiting(copy)
    send = sub.add_parser("send", help="upload a local file and print a portable receive ticket")
    send.add_argument("sources", nargs="+")
    send.add_argument("--id")
    send.add_argument("--bwlimit", type=int)
    receive = sub.add_parser("receive", help="download a ticket from anywhere with relay read access")
    receive.add_argument("ticket", help="relay://namespace/job")
    receive.add_argument("dest", nargs="?", default=".")
    receive.add_argument("--no-follow", action="store_true", help="fail when upload is incomplete")
    waiting(receive)
    st = sub.add_parser("status", help="query relay receipts without running commands on the relay")
    st.add_argument("ticket")
    sub.add_parser("doctor", help="check local prerequisites and configuration; no network changes")
    worker = sub.add_parser("_worker")
    worker.add_argument("request")
    return p


def waiting(p):
    p.add_argument("--wait-seconds", type=float, default=3600, help="overall follow deadline; partial data is retained")
    p.add_argument("--poll", type=float, default=5, help="seconds between relay queries, not source SSH sessions")


def normalize(argv):
    # Preserve ordinary scp-style positional invocation without a second CLI.
    commands = {"copy", "send", "receive", "status", "doctor", "_worker"}
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg in ("--config", "--profile"):
            i += 2
            continue
        if arg.startswith("-"):
            i += 1
            continue
        if arg not in commands:
            return argv[:i] + ["copy"] + argv[i:]
        break
    return argv


def config(args):
    path = Path(args.config).expanduser()
    data = read_json(path)
    if data.get("version") != 1:
        raise RelayError("configuration must have version: 1")
    profile = data.get("profiles", {}).get(args.profile)
    if not isinstance(profile, dict) or not isinstance(profile.get("relay"), dict):
        raise RelayError("profile needs a relay SSH endpoint")
    state = Path(data.get("state_dir", "~/.local/state/relaycopy")).expanduser()
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    return data, profile, state


def ticket_parts(ticket):
    u = urlparse(ticket)
    if u.scheme != "relay" or u.query or u.fragment:
        raise RelayError("expected relay://namespace/job")
    return identifier(u.netloc), identifier(u.path.removeprefix("/"))


def reader(profile, namespace):
    return {**profile["relay"], "download_root": join_remote(profile.get("download_root", "."), namespace)}


def destination(value):
    path = Path(value).expanduser().absolute()
    if path.is_dir() or value.endswith("/"):
        return path, None
    return path.parent, path


def run(args):
    if args.command == "_worker":
        def terminate(_signal, _frame):
            raise KeyboardInterrupt

        signal.signal(signal.SIGTERM, terminate)
        request = read_json(args.request)
        try:
            push_many(request["sources"], request["job"], request["relay"], request["work"], request.get("bwlimit"))
        except BaseException as exc:
            atomic_json(Path(request["work"]) / request["job"] / "state.json",
                        {"job": request["job"], "status": "failed", "error_type": type(exc).__name__})
            raise
        return
    data, profile, state = config(args)
    if args.command == "doctor":
        available = {tool: shutil.which(tool) for tool in ("ssh", "rsync")}
        if not all(available.values()):
            raise RelayError("install the existing OpenSSH client and rsync first")
        rsync_version = subprocess.run(["rsync", "--version"], capture_output=True, text=True, check=True).stdout.splitlines()[0]
        emit("doctor", python=sys.version.split()[0], tools=available, rsync=rsync_version,
             profile=args.profile, sources=sorted(data.get("sources", {})), state_dir=str(state))
        return
    if args.command in ("receive", "status"):
        namespace, job = ticket_parts(args.ticket)
        relay = reader(profile, namespace)
        cache = state / "downloads" / namespace / job
        if args.command == "status":
            status(job, relay, cache)
        else:
            dest, output = destination(args.dest)
            pull(job, relay, dest, cache, follow=not args.no_follow,
                 wait_seconds=args.wait_seconds, poll=args.poll, output=output)
        return
    job = identifier(args.id or ("rc-" + uuid.uuid4().hex))
    if args.command == "send":
        namespace = identifier(profile.get("namespace", "default"))
        if not isinstance(profile.get("upload"), dict):
            raise RelayError("send needs a local upload endpoint; remote copies use sources.<alias>.upload instead")
        emit("ticket", job=job, ticket=f"relay://{namespace}/{job}")
        push_many(args.sources, job, profile["upload"], state / "uploads", args.bwlimit)
        return
    if any(":" not in s for s in args.sources):
        raise RelayError("expected source-alias:/remote/file for every source; use 'send' for local files")
    pairs = [s.split(":", 1) for s in args.sources]
    alias = pairs[0][0]
    identifier(alias)
    if any(host != alias for host, _ in pairs):
        raise RelayError("all source files in one invocation must use the same SSH source alias")
    if any(not path.startswith("/") for _, path in pairs):
        raise RelayError("remote source path must be absolute")
    if len(pairs) > 1 and not (Path(args.dest).expanduser().is_dir() or args.dest.endswith("/")):
        raise RelayError("multiple source files require a destination directory")
    source = data.get("sources", {}).get(alias)
    if not isinstance(source, dict):
        raise RelayError(f"source {alias!r} is not configured; add its SSH alias and existing upload endpoint")
    source = {"host": alias, **source}
    namespace = identifier(source["namespace"])
    ticket = f"relay://{namespace}/{job}"
    work = state / "copies" / job
    work.mkdir(parents=True, exist_ok=True, mode=0o700)
    request = {"sources": args.sources, "dest": str(Path(args.dest).absolute()), "ticket": ticket, "profile": args.profile}
    request_path = work / "request.json"
    if request_path.exists() and read_json(request_path) != request:
        raise RelayError("copy ID belongs to a different source/destination/profile")
    atomic_json(request_path, request)
    emit("ticket", job=job, ticket=ticket, resume_id=job, request=str(request_path))
    receipt = launch(source, [path for _, path in pairs], job, work / "bootstrap.log", args.bwlimit)
    atomic_json(work / "source-process.json", receipt)
    emit("source_started", **receipt)
    dest, output = destination(args.dest)
    pull(job, reader(profile, namespace), dest, state / "downloads" / namespace / job,
         wait_seconds=args.wait_seconds, poll=args.poll, output=output)


def main(argv=None):
    args = parser().parse_args(normalize(list(sys.argv[1:] if argv is None else argv)))
    if hasattr(args, "poll") and (args.poll < 0.1 or args.wait_seconds < 0):
        parser().error("--poll must be >=0.1 and --wait-seconds must be >=0")
    if getattr(args, "bwlimit", None) is not None and args.bwlimit <= 0:
        parser().error("--bwlimit must be positive")
    try:
        os.umask(0o077)
        run(args)
        return 0
    except KeyboardInterrupt:
        emit("interrupted", message="partial data retained; a detached remote uploader may still be running")
        return 130
    except (RelayError, OSError, ValueError, KeyError, subprocess.SubprocessError) as exc:
        emit("error", error_type=type(exc).__name__, message=str(exc))
        return 1
