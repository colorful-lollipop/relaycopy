"""File-level relay orchestration. All payload movement and resume use rsync."""

import os
import stat
import time
from contextlib import nullcontext
from pathlib import Path

from .common import (RelayError, atomic_json, digest, emit, fingerprint, identifier,
                     join_remote, locked, make_manifest, now, read_json, validate_manifest)
from .transport import Rsync


def push(source, job, relay, work, bwlimit=None, _transport=None):
    identifier(job)
    source = Path(source).expanduser().absolute()
    work = Path(work).expanduser().absolute() / job
    work.mkdir(parents=True, exist_ok=True, mode=0o700)
    with locked(work / "upload.lock"):
        atomic_json(work / "state.json", {"job": job, "status": "hashing", "pid": os.getpid()})
        manifest, identity = make_manifest(source, job)
        path = work / "manifest.json"
        if path.exists() and read_json(path) != manifest:
            raise RelayError("job already belongs to different content; use a new job ID")
        atomic_json(path, manifest)
        root = join_remote(relay.get("upload_root", "."), job)
        with (nullcontext(_transport) if _transport else Rsync(relay, work / "rsync.log")) as transport:
            try:
                transport.put(path, root + "/manifest.json")
                emit("manifest", **manifest)
                atomic_json(work / "state.json", {"job": job, "status": "uploading", "pid": os.getpid()})
                if source.is_symlink() or fingerprint(source.stat()) != identity:
                    raise RelayError("source changed after hashing")
                # rsync handles resume. Receivers wait for complete.json before reading.
                transport.put(source, root + "/payload", bwlimit=bwlimit)
                if source.is_symlink() or fingerprint(source.stat()) != identity:
                    raise RelayError("source changed during upload; completion receipt withheld")
                done = {"job": job, "sha256": manifest["sha256"], "bytes": manifest["bytes"], "completed_at": now()}
                atomic_json(work / "complete.json", done)
                transport.put(work / "complete.json", root + "/complete.json")
            except BaseException as exc:
                failure = {"job": job, "error_type": type(exc).__name__, "error": "sender failed; inspect its local worker log", "failed_at": now()}
                atomic_json(work / "failed.json", failure)
                atomic_json(work / "state.json", {**failure, "status": "failed"})
                raise
        atomic_json(work / "state.json", {"status": "complete", **done})
        emit("upload_complete", **done)
        return done


def push_many(sources, job, relay, work, bwlimit=None):
    if len(sources) == 1:
        return push(sources[0], job, relay, work, bwlimit)
    identifier(job)
    paths = [Path(s).expanduser().absolute() for s in sources]
    if len({p.name for p in paths}) != len(paths):
        raise RelayError("batch basenames must be unique")
    batch = {"version": 1, "kind": "batch", "job": job,
             "files": [{"job": identifier(f"{job}.{i:06d}"), "name": p.name} for i, p in enumerate(paths)]}
    validate_manifest(batch, job)
    work = Path(work).expanduser().absolute()
    directory = work / job
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    with locked(directory / "upload.lock"), Rsync(relay, directory / "rsync.log") as transport:
        manifest = directory / "manifest.json"
        if manifest.exists() and read_json(manifest) != batch:
            raise RelayError("batch ID already belongs to different inputs")
        atomic_json(manifest, batch)
        root = join_remote(relay.get("upload_root", "."), job)
        transport.put(manifest, root + "/manifest.json")
        for source, item in zip(paths, batch["files"]):
            push(source, item["job"], relay, work, bwlimit, _transport=transport)
        done = {"job": job, "kind": "batch", "files": len(paths), "completed_at": now()}
        atomic_json(directory / "complete.json", done)
        transport.put(directory / "complete.json", root + "/complete.json")
        atomic_json(directory / "state.json", {"status": "complete", **done})
        emit("batch_upload_complete", **done)
        return done


def fetch_json(transport, root, name, cache):
    path = Path(cache) / ("incoming-" + name)
    path.unlink(missing_ok=True)
    if not transport.copy(path, root + "/" + name, missing_ok=True, max_bytes=1024 * 1024):
        return None
    return read_json(path)


def pull(job, relay, dest, cache, *, follow=True, wait_seconds=3600, poll=5, output=None,
         _transport=None, _expected_name=None):
    identifier(job)
    cache = Path(cache).expanduser().absolute()
    cache.mkdir(parents=True, exist_ok=True, mode=0o700)
    root = join_remote(relay.get("download_root", "."), job)
    deadline = time.monotonic() + wait_seconds

    def wait():
        if not follow:
            raise RelayError("upload not complete; use --follow or retry after the sender completes")
        if time.monotonic() >= deadline:
            raise RelayError("transfer wait deadline exceeded; partial data retained for resume")
        time.sleep(min(poll, max(0, deadline - time.monotonic())))

    with locked(cache / "download.lock"), (nullcontext(_transport) if _transport else Rsync(relay, cache / "rsync.log")) as transport:
        while (manifest := fetch_json(transport, root, "manifest.json", cache)) is None:
            wait()
        validate_manifest(manifest, job)
        if manifest.get("kind") == "batch":
            if output:
                raise RelayError("multiple files need a destination directory, ending in / or already existing")
            results = []
            for item in manifest["files"]:
                results.append(pull(item["job"], relay, dest, cache / item["job"], follow=follow,
                                    wait_seconds=max(0, deadline - time.monotonic()), poll=poll,
                                    _transport=transport, _expected_name=item["name"]))
            while (done := fetch_json(transport, root, "complete.json", cache)) is None:
                wait()
            if done.get("job") != job or done.get("files") != len(results):
                raise RelayError("batch completion receipt differs")
            record = {"job": job, "files": results, "verified_at": now()}
            atomic_json(cache / "batch-verified.json", record)
            emit("batch_verified", **record)
            return record
        if _expected_name is not None and manifest["name"] != _expected_name:
            raise RelayError("file manifest name differs from batch manifest")
        previous = cache / "manifest.json"
        if previous.exists() and read_json(previous) != manifest:
            raise RelayError("manifest changed; refusing to mix old partial data with new content")
        atomic_json(previous, manifest)
        # File-level pipeline: no payload request while the source is uploading.
        while (done := fetch_json(transport, root, "complete.json", cache)) is None:
            emit("waiting_for_upload", job=job)
            wait()
        if done.get("job") != job or done.get("sha256") != manifest["sha256"] or done.get("bytes") != manifest["bytes"]:
            raise RelayError("upload completion receipt differs from manifest")
        dest = Path(dest).expanduser().absolute()
        dest.mkdir(parents=True, exist_ok=True)
        final = Path(output).expanduser().absolute() if output else dest / manifest["name"]
        if final.is_symlink():
            raise RelayError("destination is a symlink; refusing to follow it")
        if final.exists():
            if not final.is_file() or final.stat().st_size != manifest["bytes"] or digest(final) != manifest["sha256"]:
                raise RelayError("existing destination differs; refusing to overwrite")
            record = {"job": job, "local": str(final), "bytes": manifest["bytes"], "sha256": manifest["sha256"], "already_verified": True}
            emit("verified", **record)
            return record
        final.parent.mkdir(parents=True, exist_ok=True)
        part = final.with_name(final.name + ".partial.relaycp")
        sidecar = part.with_name(part.name + ".json")
        with locked(part.with_name(part.name + ".lock")):
            identity = {"manifest": manifest, "relay_host": relay["host"], "root": root}
            if part.exists() and (not sidecar.exists() or read_json(sidecar) != identity):
                raise RelayError("partial destination belongs to a different transfer")
            if part.is_symlink() or (part.exists() and not stat.S_ISREG(part.lstat().st_mode)):
                raise RelayError("partial destination must be a regular file")
            atomic_json(sidecar, identity)
            transport.copy(part, root + "/payload", max_bytes=manifest["bytes"])
            current = part.stat().st_size
            if current != manifest["bytes"] or digest(part) != manifest["sha256"]:
                raise RelayError("whole-file SHA256 mismatch; partial data retained for diagnosis")
            os.link(part, final)
            part.unlink()
            record = {"job": job, "local": str(final), "bytes": manifest["bytes"], "sha256": manifest["sha256"], "verified_at": now()}
            atomic_json(final.with_name(final.name + ".relaycp-verified.json"), record)
            sidecar.unlink()
            emit("verified", **record)
            return record


def status(job, relay, cache):
    identifier(job)
    cache = Path(cache)
    cache.mkdir(parents=True, exist_ok=True, mode=0o700)
    root = join_remote(relay.get("download_root", "."), job)
    with locked(cache / "download.lock"), Rsync(relay, cache / "rsync.log") as transport:
        for name, state in (("complete.json", "complete"), ("manifest.json", "incomplete")):
            value = fetch_json(transport, root, name, cache)
            if value is not None:
                if name == "manifest.json":
                    validate_manifest(value, job)
                result = {"job": job, "status": state, "metadata": value}
                emit("status", **result)
                return result
    result = {"job": job, "status": "pending"}
    emit("status", **result)
    return result
