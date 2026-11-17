#!/usr/bin/env python3
"""Fail-closed public-source documentation coordinator helpers (Python stdlib)."""
import re
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import tomllib
import urllib.request
from urllib.error import HTTPError
from urllib.parse import quote, urlparse

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "automation/config.json"
CACHE = ROOT / ".automation"
REPO = CACHE / "sources/paykit-rs"

import contextlib
import fcntl
import signal

ACTIVE_RUN = None

@contextlib.contextmanager
def state_lock(cache=None):
    cache = CACHE if cache is None else Path(cache)
    cache.mkdir(parents=True, exist_ok=True)
    with (cache / "lease-state.lock").open("a") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        yield

@contextlib.contextmanager
def run_scope(run_id):
    global ACTIVE_RUN
    previous = ACTIVE_RUN
    ACTIVE_RUN = run_id
    try:
        guard_run(run_id, CACHE)
        yield
    finally:
        ACTIVE_RUN = previous

def check_effect():
    if ACTIVE_RUN:
        return guard_run(ACTIVE_RUN, CACHE)

def process_identity(pid):
    try:
        return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[19]
    except FileNotFoundError:
        return None

def group_alive(pgid):
    for stat in Path("/proc").glob("[0-9]*/stat"):
        try:
            fields = stat.read_text().rsplit(")", 1)[1].split()
            if int(fields[2]) == pgid and fields[0] != "Z":
                return True
        except (FileNotFoundError, ProcessLookupError):
            pass
    return False

def stop_group(pid):
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(pid, sig)
        except ProcessLookupError:
            return
        until = time.monotonic() + .3
        while group_alive(pid) and time.monotonic() < until:
            time.sleep(.01)
    if group_alive(pid):
        raise ValueError("Owned child process group did not stop; lease cannot be reclaimed")

def cleanup_children(record):
    for child in record.get("children", []):
        pid = child["pid"]
        if not group_alive(pid):
            continue
        if not child.get("start") or process_identity(pid) != child["start"]:
            raise ValueError("Cannot prove expired child ownership; lease cannot be reclaimed")
        stop_group(pid)

# The Linux subreaper owns only this command's descendants, including tools
# that create detached browser/server sessions. It is not a host-agent killer.
WORKER_PROGRAM = r'''
import ctypes, json, os, pathlib, signal, subprocess, sys, time
if ctypes.CDLL(None, use_errno=True).prctl(36, 1, 0, 0, 0) != 0:
    raise RuntimeError("Cannot establish owned-child subreaper")
stopping = False
def stop(signum, frame):
    global stopping
    stopping = True
signal.signal(signal.SIGTERM, stop)
signal.signal(signal.SIGINT, stop)
parent = os.getppid()
deadline = float(sys.argv[2])
command = subprocess.Popen(json.loads(sys.argv[1]))
def descendants():
    entries = {}
    for path in pathlib.Path('/proc').glob('[0-9]*/stat'):
        try:
            fields = path.read_text().rsplit(')', 1)[1].split()
            entries[int(path.parent.name)] = (int(fields[1]), fields[19], fields[0])
        except (FileNotFoundError, ProcessLookupError):
            pass
    owned = {os.getpid()}
    while True:
        found = {pid for pid, (ppid, _, _) in entries.items() if ppid in owned}
        if found <= owned:
            break
        owned.update(found)
    return {pid: entries[pid] for pid in owned if pid != os.getpid() and pid in entries}
try:
    while command.poll() is None and not stopping and os.getppid() == parent and time.time() < deadline:
        time.sleep(.02)
    code = command.poll()
finally:
    for sig in (signal.SIGTERM, signal.SIGKILL):
        until = time.monotonic() + .1
        while True:
            children = descendants()
            for pid, (_, identity, state) in children.items():
                if state == 'Z':
                    continue
                try:
                    fd = os.pidfd_open(pid)
                    try:
                        fields = pathlib.Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()
                        if fields[19] == identity:
                            signal.pidfd_send_signal(fd, sig)
                    finally:
                        os.close(fd)
                except (FileNotFoundError, ProcessLookupError):
                    pass
            if not any(state != 'Z' for _, _, state in descendants().values()) or time.monotonic() >= until:
                break
            time.sleep(.01)
    command.wait()
    while True:
        try:
            pid, _ = os.waitpid(-1, os.WNOHANG)
            if not pid:
                break
        except ChildProcessError:
            break
sys.exit(124 if code is None else (128 - code if code < 0 else code))
'''

def bounded_process(args, cwd=None, timeout=300, env=None, check=False, run_id=None, cache=None, preserve_newlines=False, raw=False):
    cache = CACHE if cache is None else Path(cache)
    run_id = ACTIVE_RUN if run_id is None else run_id
    with state_lock(cache):
        record = guard_run(run_id, cache) if run_id else None
        budget = min(timeout, record["deadline"] - time.time()) if record else timeout
        if budget <= 0:
            raise ValueError("Run expired; command forbidden")
        proc = None
        registered = False
        try:
            proc = subprocess.Popen([sys.executable, "-c", WORKER_PROGRAM, json.dumps(list(map(str, args))), str(time.time() + budget)],
                                    cwd=ROOT if cwd is None else cwd, env=env,
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=not (preserve_newlines or raw), start_new_session=True)
            if record:
                record.setdefault("children", []).append({"pid": proc.pid, "start": process_identity(proc.pid)})
                save_json(cache / "run.lock/owner.json", record)
            registered = True
        finally:
            # Registration is part of spawn: do not unlock an unowned live child.
            if proc is not None and not registered:
                stop_group(proc.pid)
                proc.communicate()
    try:
        try:
            stdout, stderr = proc.communicate(timeout=budget)
        except subprocess.TimeoutExpired as error:
            stop_group(proc.pid)
            # A second communicate returns the complete captured streams, not
            # just the exception's potentially earlier partial observation.
            error.output, error.stderr = proc.communicate()
            raise
        except BaseException:
            stop_group(proc.pid)
            proc.communicate()
            raise
        finally:
            # A successful parent must not leave background children behind.
            if group_alive(proc.pid):
                stop_group(proc.pid)
        if run_id:
            guard_run(run_id, cache)
        if proc.returncode == 124:
            raise subprocess.TimeoutExpired(args, budget, stdout, stderr)
        if preserve_newlines and not raw:
            stdout, stderr = stdout.decode("utf-8"), stderr.decode("utf-8")
        result = subprocess.CompletedProcess(args, proc.returncode, stdout, stderr)
        if check:
            result.check_returncode()
        return result
    finally:
        with state_lock(cache):
            owner = cache / "run.lock/owner.json"
            if run_id and owner.exists():
                current = read_json(owner)
                if current["id"] == run_id:
                    current["children"] = [c for c in current.get("children", []) if c["pid"] != proc.pid]
                    save_json(owner, current)

def run(args, cwd=None, timeout=300, env=None):
    stdout = bounded_process(args, cwd=cwd, timeout=timeout, env=env, check=True).stdout
    return stdout if "-z" in args or "--null" in args else stdout.strip()

def read_json(path):
    return json.loads(Path(path).read_text())

def write_artifact(path, data):
    path = Path(path)
    data = data.encode() if isinstance(data, str) else data
    with state_lock() if ACTIVE_RUN else contextlib.nullcontext():
        check_effect()
        if path.is_file() and path.read_bytes() == data:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + f".{os.getpid()}.tmp")
        try:
            temporary.write_bytes(data)
            check_effect()
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)

def save_json(path, value):
    path = Path(path)
    text = json.dumps(value, indent=2, ensure_ascii=False) + "\n"
    if ACTIVE_RUN and not path.is_relative_to(CACHE / "runs") and path != CACHE / "run.lock/owner.json":
        write_artifact(path, text)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.read_text() == text:
        return
    temporary = path.with_name(path.name + f".{os.getpid()}.tmp")
    temporary.write_text(text)
    temporary.replace(path)

def digest(value):
    data = value if isinstance(value, bytes) else json.dumps(value, sort_keys=True).encode()
    return hashlib.sha256(data).hexdigest()

def bounded_sleep(seconds):
    record = check_effect()
    if record:
        seconds = min(seconds, max(0, record["deadline"] - time.time()))
    time.sleep(seconds)
    check_effect()

def fetch_bytes(url, timeout=40, same_origin=False):
    # Network reads run inside the same killable, deadline-bounded supervisor.
    import base64
    program = r'''
import base64, json, sys, urllib.request, urllib.error
req = urllib.request.Request(sys.argv[1], headers={"User-Agent": "paykit-public-docs/1", "Accept": "application/json"})
try:
    with urllib.request.urlopen(req, timeout=float(sys.argv[2])) as response:
        print(json.dumps({"status": response.status, "finalUrl": response.url, "contentType": response.headers.get("Content-Type", ""), "data": base64.b64encode(response.read()).decode()}))
except urllib.error.HTTPError as error:
    print(json.dumps({"status": error.code, "data": ""}))
'''
    reply = json.loads(run([sys.executable, "-c", program, url, str(timeout)], timeout=timeout))
    if reply["status"] >= 400:
        raise HTTPError(url, reply["status"], "Public source HTTP failure", {}, None)
    if same_origin:
        requested, final = urlparse(url), urlparse(reply.get("finalUrl", ""))
        if (requested.scheme, requested.netloc, requested.path) != (final.scheme, final.netloc, final.path) or "json" not in reply.get("contentType", "").lower():
            raise ValueError("Preview protected or redirected to login; account action required to permit public provenance readback")
    return base64.b64decode(reply["data"])

def get_json(url, same_origin=False):
    for attempt in range(3):
        try:
            return json.loads(fetch_bytes(url, same_origin=same_origin))
        except HTTPError as error:
            if error.code not in (429, 500, 502, 503, 504) or attempt == 2:
                raise
            bounded_sleep(2 ** attempt)

def source_text(commit, path):
    if not re.fullmatch(r"[a-f0-9]{40}", commit) or path.startswith("/") or ".." in Path(path).parts:
        raise ValueError("Unsafe source reference")
    return bounded_process(["git", "show", f"{commit}:{path}"], cwd=REPO, timeout=60, check=True).stdout

def verify_published_package(package, info, commit):
    import io, tarfile
    archive_path = CACHE / "packages" / (info["checksum"] + ".crate")
    if not archive_path.exists():
        archive_path.parent.mkdir(parents=True, exist_ok=True)
        data = fetch_bytes(info["downloadUrl"], timeout=90)
        if digest(data) != info["checksum"]:
            raise ValueError("Published package checksum mismatch")
        check_effect()
        write_artifact(archive_path, data)
    data = archive_path.read_bytes()
    if digest(data) != info["checksum"]:
        raise ValueError("Cached package checksum mismatch")
    prefix = f"{package}-{info['version']}/"
    count = 0
    with tarfile.open(fileobj=io.BytesIO(data)) as archive:
        vcs = json.load(archive.extractfile(prefix + ".cargo_vcs_info.json"))
        if vcs["git"]["sha1"] != commit:
            raise ValueError("Published package came from a different commit; investigate before generation")
        for member in archive.getmembers():
            if member.name.startswith(prefix + "src/") and member.name.endswith(".rs"):
                path = package + "/" + member.name[len(prefix):]
                if archive.extractfile(member).read() != source_text(commit, path).encode():
                    raise ValueError("Published implementation differs from release source: " + path)
                count += 1
    if count == 0:
        raise ValueError("Published package contains no verified implementation")
    return {"sourceCommit": commit, "verifiedRustFiles": count}

def generation_config(root=None):
    """The root selector is authoritative; never infer a ref from repository metadata."""
    root = ROOT if root is None else Path(root)
    cfg = read_json(root / "docs-generation.json")
    if (not isinstance(cfg, dict) or set(cfg) != {"schemaVersion", "source", "defaultVersion"}
            or type(cfg["schemaVersion"]) is not int or cfg["schemaVersion"] != 1
            or cfg["defaultVersion"] != "coming-soon"):
        raise ValueError("Invalid docs-generation configuration")
    source = cfg["source"]
    if (not isinstance(source, dict) or set(source) != {"repository", "ref", "kind"}
            or source["repository"] != "pubky/paykit-rs" or source["kind"] not in ("branch", "tag")
            or not isinstance(source["ref"], str)
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]*", source["ref"])
            or source["ref"].startswith("refs/")):
        raise ValueError("Invalid configured source repository/kind/ref")
    qualified = "refs/" + ("heads/" if source["kind"] == "branch" else "tags/") + source["ref"]
    try:
        run(["git", "check-ref-format", qualified])
    except subprocess.CalledProcessError as error:
        raise ValueError("Invalid configured source ref") from error
    return cfg


def snapshot_ref(ref, kind):
    """Bind exact advertised object and peeled commit to a task-owned local ref."""
    import uuid
    qualified = "refs/" + ("heads/" if kind == "branch" else "tags/") + ref
    def advertised():
        rows = run(["git", "ls-remote", "origin", qualified, qualified + "^{}"], cwd=REPO).splitlines()
        refs = {}
        for row in rows:
            fields = row.split()
            if (len(fields) != 2 or not re.fullmatch(r"[a-f0-9]{40}", fields[0])
                    or fields[1] not in (qualified, qualified + "^{}") or fields[1] in refs):
                raise ValueError("Invalid exact source ref advertisement")
            refs[fields[1]] = fields[0]
        if qualified not in refs:
            raise ValueError("Configured source ref unavailable: " + qualified)
        return refs
    before = advertised()
    local = "refs/paykit-snapshots/" + uuid.uuid4().hex
    run(["git", "fetch", "--no-tags", "origin", "+" + qualified + ":" + local], cwd=REPO, timeout=300)
    obj = run(["git", "rev-parse", "--verify", local], cwd=REPO)
    commit = run(["git", "rev-parse", "--verify", local + "^{commit}"], cwd=REPO)
    if (obj != before[qualified] or commit != before.get(qualified + "^{}", obj)
            or advertised() != before):
        raise ValueError("Source ref moved during snapshot binding: " + qualified)
    return {"ref": ref, "kind": kind, "qualifiedRef": qualified, "object": obj, "commit": commit}


def resolve_sources():
    cfg = read_json(CONFIG)
    selected = generation_config()
    canonical = selected["source"]["repository"]
    if canonical != cfg["canonicalRepository"]:
        raise ValueError("Configured source differs from approved canonical repository")
    metadata = get_json(f"https://api.github.com/repos/{canonical}")
    if metadata.get("private") is not False or metadata.get("full_name") != canonical:
        raise ValueError("Canonical source is not the approved public repository")
    if not isinstance(metadata.get("default_branch"), str) or not metadata["default_branch"]:
        raise ValueError("Canonical default branch metadata unavailable")
    releases = []
    page = 1
    while True:
        batch = get_json(f"https://api.github.com/repos/{canonical}/releases?per_page=100&page={page}")
        if not isinstance(batch, list):
            raise ValueError("Invalid release response")
        releases.extend(batch)
        if len(batch) < 100:
            break
        page += 1
        if page > 100:
            raise ValueError("Release pagination exceeded safety bound")
    release = select_release(releases)
    REPO.parent.mkdir(parents=True, exist_ok=True)
    if not (REPO / ".git").exists():
        run(["git", "clone", "--filter=blob:none", f"https://github.com/{canonical}.git", str(REPO)], timeout=600)
        run(["git", "remote", "set-url", "--push", "origin", "DISABLED"], cwd=REPO)
    if run(["git", "remote", "get-url", "origin"], cwd=REPO) != f"https://github.com/{canonical}.git":
        raise ValueError("Unexpected source clone origin")
    released_ref = snapshot_ref(release["tag_name"], "tag")
    selected_ref = snapshot_ref(selected["source"]["ref"], selected["source"]["kind"])
    stable, dev = released_ref["commit"], selected_ref["commit"]
    lock = {"schemaVersion": 1, "repository": canonical, "defaultVersion": selected["defaultVersion"],
            "selectedSource": {"repository": canonical, **selected_ref}}
    for name, commit in (("released", stable), ("coming-soon", dev)):
        entry = {"label": release["tag_name"] if name == "released" else selected_ref["ref"], "commit": commit,
                 "sourceUrl": f"https://github.com/{canonical}/tree/{commit}", "packages": {}}
        if name == "released":
            entry.update(tag=release["tag_name"], releaseUrl=release["html_url"], **released_ref)
        else:
            entry.update(selected_ref)
            entry["tag" if selected_ref["kind"] == "tag" else "branch"] = selected_ref["ref"]
        for package in cfg["packages"]:
            manifest = tomllib.loads(source_text(commit, f"{package}/Cargo.toml"))
            version = manifest["package"]["version"]
            package_info = {"version": version, "rustVersion": manifest["package"].get("rust-version"),
                            "dependencies": manifest.get("dependencies", {}), "published": False}
            try:
                registry = get_json(f"https://crates.io/api/v1/crates/{package}/{version}")["version"]
                if registry["num"] != version or registry["crate"] != package:
                    raise ValueError("Registry returned a different package")
                package_info.update(published=True, yanked=registry["yanked"], checksum=registry["checksum"],
                                    downloadUrl=f"https://static.crates.io/crates/{package}/{package}-{version}.crate",
                                    referenceUrl=f"https://docs.rs/{package}/{version}/{package.replace('-', '_')}/")
            except HTTPError as error:
                if error.code != 404:
                    raise
            if name == "released" and package_info["published"]:
                package_info["verifiedSource"] = verify_published_package(package, package_info, commit)
            entry["packages"][package] = package_info
        pkg = entry["packages"][cfg["examplePackage"]]
        if name == "released" and (not pkg["published"] or pkg.get("yanked")):
            raise ValueError("Released example package unavailable or yanked; retain last valid docs")
        lock[name] = entry
    return lock


def validate_source_selection(lock):
    cfg = generation_config()
    source = cfg["source"]
    selected = lock.get("selectedSource", {})
    displayed = lock.get("coming-soon", {})
    qualified = "refs/" + ("heads/" if source["kind"] == "branch" else "tags/") + source["ref"]
    if (not isinstance(selected, dict) or not isinstance(displayed, dict)
            or lock.get("repository") != source["repository"] or lock.get("defaultVersion") != cfg["defaultVersion"]
            or any(selected.get(key) != value for key, value in source.items())
            or selected.get("qualifiedRef") != qualified
            or not all(isinstance(selected.get(key), str) and re.fullmatch(r"[a-f0-9]{40}", selected[key]) for key in ("object", "commit"))):
        raise ValueError("Selected source does not match configured repository/kind/ref/commit")
    expected = {key: selected[key] for key in ("commit", "ref", "kind", "qualifiedRef", "object")}
    expected.update(label=source["ref"], sourceUrl=f"https://github.com/{source['repository']}/tree/{selected['commit']}")
    expected["branch" if source["kind"] == "branch" else "tag"] = source["ref"]
    if (any(displayed.get(key) != value for key, value in expected.items())
            or ("tag" if source["kind"] == "branch" else "branch") in displayed
            or displayed.get("repository", source["repository"]) != source["repository"]):
        raise ValueError("Selected source displayed identity does not match frozen repository/kind/ref/commit")
    return lock


def _checked_native_runtime(value):
    keys = {"hermesRevision", "coordinatorSha256", "isolationVersion"}
    if not isinstance(value, dict) or set(value) != keys:
        raise ValueError("Invalid native coordinator runtime metadata")
    if not isinstance(value["hermesRevision"], str) or not re.fullmatch(r"[a-f0-9]{40}", value["hermesRevision"]):
        raise ValueError("Invalid native Hermes revision")
    if value["isolationVersion"] != "native-public-tools-v1":
        raise ValueError("Unsupported native isolation policy")
    source = ROOT / "automation/hermes_coordinator.py"
    if not source.is_file() or value["coordinatorSha256"] != digest(source.read_bytes()):
        raise ValueError("Installed coordinator does not match version-controlled source")
    return value

def native_runtime_versions(required=False):
    path = CACHE / "native-runtime.json"
    if not path.exists():
        if required:
            raise ValueError("Trusted native coordinator preflight is required")
        return {}
    return _checked_native_runtime(read_json(path))

def record_native_preflight(metadata):
    """Trusted host calls this only after successful private MCP consultation."""
    metadata = _checked_native_runtime(metadata)
    save_json(CACHE / "native-runtime.json", metadata)
    save_json(CACHE / "native-preflight.json", {"runtime": metadata, "createdAt": time.time(),
        "proofId": os.urandom(16).hex(), "consultationSucceeded": True})

def bind_native_context(record):
    if not read_json(CONFIG).get("privacy", {}).get("requireNativeCoordinator"):
        return
    metadata = native_runtime_versions(required=True)
    path = CACHE / "native-preflight.json"
    if not path.exists():
        raise ValueError("Fresh private consultation receipt is required")
    receipt = read_json(path)
    if (set(receipt) != {"runtime", "createdAt", "proofId", "consultationSucceeded"}
            or receipt["runtime"] != metadata or receipt["consultationSucceeded"] is not True
            or type(receipt["createdAt"]) not in (int, float) or not 0 <= time.time()-receipt["createdAt"] < 300
            or not isinstance(receipt["proofId"], str) or not re.fullmatch(r"[a-f0-9]{32}", receipt["proofId"])):
        raise ValueError("Invalid or expired private consultation receipt")
    save_json(CACHE / "runs" / record["id"] / "context.json", {"runId": record["id"],
        "runtime": metadata, "proofId": receipt["proofId"], "consultationSucceeded": True})
    path.unlink()

def require_native_context(run_id):
    if not read_json(CONFIG).get("privacy", {}).get("requireNativeCoordinator"):
        return
    metadata = native_runtime_versions(required=True)
    path = CACHE / "runs" / run_id / "context.json"
    if not path.exists():
        raise ValueError("Run lacks a bound native private/public context receipt")
    receipt = read_json(path)
    if (set(receipt) != {"runId", "runtime", "proofId", "consultationSucceeded"}
            or receipt["runId"] != run_id or receipt["runtime"] != metadata
            or receipt["consultationSucceeded"] is not True):
        raise ValueError("Native private/public context binding changed")

def tool_versions():
    cargo = str(Path.home() / ".cargo/bin/rustc")
    versions = {"node": run(["node", "--version"]), "npm": run(["npm", "--version"]),
            "python": sys.version.split()[0], "rust": run([cargo, "+1.91.1", "--version"]),
            "playwright": run(["node_modules/.bin/playwright", "--version"])}
    # Instruction-only mode must not consult stale receipts from the retired runner.
    if read_json(CONFIG).get("privacy", {}).get("requireNativeCoordinator", False):
        versions.update(native_runtime_versions(required=True))
    return versions

def generation_inputs(root, include_blueprints=False):
    patterns = ["docs-generation.json", "automation/*.py", "automation/*.mjs", "automation/config.json", "automation/corrections.json", "automation/SOURCE-BOUNDARY.md", "automation/LOCAL-EXECUTION.md", "automation/local-execution-policy.json", "automation/skills/*", "automation/tests/*.py",
                "automation/bootstrap*", "automation/render*", "automation/recipes/*", "automation/COORDINATOR.md", "AGENTS.md", "package*.json", "*config.*",
                "app/**/*", "components/**/*", "tests/**/*", "automation/README.md"]
    if include_blueprints:
        patterns.append("automation/blueprints/**/*.json")
    return {str(path.relative_to(root)): digest(path.read_bytes())
            for pattern in patterns for path in sorted(root.glob(pattern))
            if path.is_file() and "__pycache__" not in path.parts}


def input_fingerprint(lock, root=None, tools=None):
    root = ROOT if root is None else Path(root)
    return digest({"sources": lock, "files": generation_inputs(root, include_blueprints=True),
                   "tools": tool_versions() if tools is None else tools})


def process_fingerprint(root=None, tools=None):
    root = ROOT if root is None else Path(root)
    return digest({"files": generation_inputs(root), "tools": tool_versions() if tools is None else tools})


def regeneration_fingerprint(lock, root=None, tools=None):
    # Observed stable-release/registry metadata and tag objects remain in the full
    # source fingerprint, but only the selected *commit* drives source updates.
    return digest({"selectedCommit": lock["coming-soon"]["commit"],
                   "process": process_fingerprint(root, tools)})

def acceptance_digest(root=None):
    root = ROOT if root is None else Path(root)
    patterns = ("docs-generation.json", "automation/*.py", "automation/*.mjs", "automation/tests/*.py", "automation/skills/evaluation.md",
                "automation/config.json", "automation/corrections.json", "automation/SOURCE-BOUNDARY.md", "automation/LOCAL-EXECUTION.md", "automation/local-execution-policy.json", "automation/COORDINATOR.md", "AGENTS.md", "tests/**/*",
                "package*.json", "playwright.config.*", "tsconfig.json", "next.config.*")
    files = {str(path.relative_to(root)): digest(path.read_bytes())
             for pattern in patterns for path in sorted(root.glob(pattern))
             if path.is_file() and "__pycache__" not in path.parts}
    return digest(files)

def emit_examples(lock, output_root=None):
    output_root = ROOT / "public/docs-examples" if output_root is None else Path(output_root)
    for version in ("released", "coming-soon"):
        snapshot = lock[version]
        if version == "released":
            dependency = '{ version = "=' + snapshot["packages"]["paykit-lib"]["version"] + '" }'
        else:
            dependency = '{ git = "https://github.com/pubky/paykit-rs", rev = "' + snapshot["commit"] + '" }'
        for audience in ("wallet", "point-of-sale"):
            destination = output_root / version / audience
            check_effect()
            (destination / "src").mkdir(parents=True, exist_ok=True)
            write_artifact(destination / "Cargo.toml",
                '[package]\nname = "paykit-' + audience + '-demo"\nversion = "0.0.0"\nedition = "2021"\nrust-version = "1.91.1"\n\n'
                '[workspace]\nresolver = "3"\n\n[dependencies]\npaykit-lib = ' + dependency + '\n')
            check_effect()
            write_artifact(destination / "rust-toolchain.toml",'[toolchain]\nchannel = "1.91.1"\nprofile = "minimal"\n')
            check_effect()
            write_artifact(destination / "src/main.rs", (ROOT / f"automation/recipes/{audience}.rs").read_bytes())

EXPECTED = {
    "wallet": "demo-pos: shared rail btc-lightning-bolt11; unsupported rail: no shared rail\nLocal fixture only; no discovery request or payment was sent.",
    "point-of-sale": "order-demo-001: request validated and parsed; no payment was sent."
}

def stdout_matches_expected(stdout, expected):
    """Exact text, optionally followed by one LF; never normalize the report."""
    return isinstance(stdout, str) and stdout in (expected, expected + "\n")


def test_examples(lock, create_locks=False, write_artifacts=True):
    import tarfile, gzip, io
    if create_locks and not write_artifacts:
        raise ValueError("Read-only validation cannot create locks")
    if write_artifacts:
        emit_examples(lock)
    results = []
    cargo = str(Path.home() / ".cargo/bin/cargo")
    home = CACHE / 'examples-public-home'
    check_effect()
    home.mkdir(parents=True, exist_ok=True)
    env = {'PATH':str(Path(cargo).parent)+':/usr/local/bin:/usr/bin:/bin',
           'LANG':'C.UTF-8', 'LC_ALL':'C.UTF-8', 'HOME':str(home),
           'CARGO_HOME':str(home/'.cargo'), 'RUSTUP_HOME':str(Path.home()/'.rustup'),
           'CARGO_BUILD_JOBS':'2', 'CARGO_TARGET_DIR':str(CACHE/'cargo-target')}
    for version in ("released", "coming-soon"):
        for audience in ("wallet", "point-of-sale"):
            directory = ROOT / f"public/docs-examples/{version}/{audience}"
            if create_locks:
                bounded_process([cargo, "+1.91.1", "generate-lockfile"], cwd=directory, env=env, check=True, timeout=300)
            error = None
            try:
                result = bounded_process([cargo, "+1.91.1", "run", "--locked", "--quiet"], cwd=directory, env=env, timeout=1200, raw=True)
                stdout, stderr, code = result.stdout, result.stderr, result.returncode
            except subprocess.TimeoutExpired as caught:
                error = caught
                stdout = b"" if caught.stdout is None else caught.stdout
                stderr = b"" if caught.stderr is None else caught.stderr
                code = None
            output = CACHE / "example-output" / f"{version}-{audience}"
            write_artifact(output.with_suffix(".stdout"), stdout)
            write_artifact(output.with_suffix(".stderr"), stderr)
            decoded = []
            decode_error = False
            for stream in (stdout, stderr):
                try:
                    decoded.append(stream.decode("utf-8") if isinstance(stream, bytes) else stream)
                except UnicodeDecodeError as caught:
                    decoded.append(None)
                    decode_error = True
                    if error is None:
                        error = caught
            stdout, stderr = decoded
            passed = error is None and type(code) is int and code == 0 and stdout_matches_expected(stdout, EXPECTED[audience])
            record = {"version": version, "audience": audience, "commit": lock[version]["commit"],
                      "exitCode": code, "stdout": stdout, "stderr": stderr[-12000:] if stderr is not None else None,
                      "status": "pass" if passed else "fail", "decodeError": decode_error,
                      "timedOut": isinstance(error, subprocess.TimeoutExpired),
                      "errorType": type(error).__name__ if error else None,
                      "stdoutPath": str(output.with_suffix(".stdout")), "stderrPath": str(output.with_suffix(".stderr")),
                      "lockHash": digest((directory / "Cargo.lock").read_bytes())}
            results.append(record)
            write_artifact(CACHE / "example-results.json", json.dumps(results, indent=2, ensure_ascii=False) + "\n")
            if error is not None:
                raise error
            if not passed:
                raise ValueError("Example failed: " + json.dumps(record))
            if not write_artifacts:
                continue
            buf = io.BytesIO()
            with tarfile.open(fileobj=buf, mode="w") as tar:
                for path in sorted(directory.rglob("*")):
                    if path.is_file():
                        info = tarfile.TarInfo(f"paykit-{audience}-demo/" + str(path.relative_to(directory)))
                        data = path.read_bytes()
                        info.size, info.mode, info.mtime = len(data), 0o644, 0
                        tar.addfile(info, io.BytesIO(data))
            archive = directory.parent / f"paykit-{audience}-{version}.tar.gz"
            check_effect()
            write_artifact(archive, gzip.compress(buf.getvalue(), mtime=0))
    print(json.dumps(results, indent=2))
    return results

def anchor_evidence(record):
    """Resolve durable semantic source anchors independently in each snapshot."""
    text = source_text(record["commit"], record["path"])
    lines = text.splitlines()
    anchor = record["anchor"]
    starts = [i for i, line in enumerate(lines) if line == anchor["start"]]
    ends = [i for i, line in enumerate(lines) if line == anchor["end"]]
    if len(starts) != 1 or len(ends) != 1 or starts[0] > ends[0]:
        raise ValueError("Evidence anchor missing or ambiguous: " + record["id"])
    return {**record, "startLine": starts[0] + 1, "endLine": ends[0] + 1,
            "excerpt": "\n".join(lines[starts[0]:ends[0]+1]), "sourceHash": digest(text.encode())}

def validate_evidence(records, lock, version):
    ids = set()
    for record in records:
        if record.get("version") != version or record.get("commit") != lock[version]["commit"]:
            raise ValueError("Evidence does not match version snapshot")
        if record["id"] in ids:
            raise ValueError("Duplicate evidence id")
        ids.add(record["id"])
        path = record["path"]
        text = source_text(record["commit"], path)
        if digest(text.encode()) != record["sourceHash"]:
            raise ValueError("Source hash differs: " + record["id"])
        lines = text.splitlines()
        start, end = record["startLine"], record["endLine"]
        if not (1 <= start <= end <= len(lines)):
            raise ValueError("Invalid evidence line range")
        if "\n".join(lines[start-1:end]) != record["excerpt"]:
            raise ValueError("Evidence excerpt differs: " + record["id"])
        if "anchor" in record:
            resolved = anchor_evidence(record)
            if any(record[key] != resolved[key] for key in ("startLine", "endLine", "excerpt", "sourceHash")):
                raise ValueError("Evidence anchor does not match cited contract: " + record["id"])
        if record["kind"] not in {"implemented", "proposed", "application-responsibility", "package"}:
            raise ValueError("Missing evidence classification")
        if record["kind"] == "implemented" and path.startswith("specs/"):
            raise ValueError("Specification alone is not implementation evidence")
    return ids

def validate_blueprint(blueprint, lock, version):
    if blueprint["sourceCommit"] != lock[version]["commit"]:
        raise ValueError("Blueprint snapshot is stale")
    ids = validate_evidence(blueprint["evidence"], lock, version)
    cfg = read_json(CONFIG)
    expected = [slug for slugs in cfg["sections"].values() for slug in slugs]
    if sorted(p["slug"] for p in blueprint["pages"]) != sorted(expected):
        raise ValueError("Documentation page set is incomplete")
    for page in blueprint["pages"]:
        if page["group"] not in cfg["sections"] or page["slug"] not in cfg["sections"][page["group"]]:
            raise ValueError("Invalid audience or documentation scope")
        for section in page["sections"]:
            if not section.get("evidence") or not set(section["evidence"]) <= ids:
                raise ValueError("Unsupported section: " + page["slug"] + "/" + section["id"])
        for link in page.get("sourceLinks", []):
            if not approved_source(link["href"]):
                raise ValueError("Unapproved public source link")
    for diagram in blueprint.get("diagrams", []):
        if not diagram.get("relationships") or not diagram.get("alt"):
            raise ValueError("Diagram lacks relationships or text alternative")
        for relationship in diagram["relationships"]:
            if not relationship.get("evidence") or not set(relationship["evidence"]) <= ids:
                raise ValueError("Unsupported diagram relationship")
    return True

def assemble_pages(blueprint, lock, version):
    import copy
    pages = copy.deepcopy(blueprint["pages"])
    for page in pages:
        for section in page["sections"]:
            for block in section.get("code", []):
                if "artifact" in block:
                    artifact = block.pop("artifact")
                    if ".." in Path(artifact).parts or Path(artifact).is_absolute():
                        raise ValueError("Unsafe example artifact")
                    block["code"] = (ROOT / f"public/docs-examples/{version}" / artifact).read_text()
    return {"version": copy.deepcopy(lock[version]), "pages": pages}

def readiness_metadata(commit, version="released"):
    text = source_text(commit, "README.md")
    lines = text.splitlines()
    warning_line = next((i for i, line in enumerate(lines, 1) if re.search(r"not\s+for\s+production", line, re.I)), None)
    if warning_line:
        status = "experimental"
        notice = "Upstream README: " + lines[warning_line - 1]
        line_number = warning_line
    else:
        # Preserve the caution, but do not launder operator policy through a
        # README heading as if upstream asserted a readiness classification.
        return {"status": "unassessed", "attribution": "documentation-policy",
                "notice": "A stable release label does not establish production readiness. Review upstream status before deployment.",
                "sourceUrl": f"https://github.com/pubky/paykit-rs/blob/{commit}/README.md"}
    return {"status": status, "notice": notice, "attribution": "upstream",
            "sourceUrl": f"https://github.com/pubky/paykit-rs/blob/{commit}/README.md#L{line_number}",
            "evidence": {"id": version + "-readiness", "version": version, "commit": commit, "path": "README.md",
                "startLine": line_number, "endLine": line_number, "excerpt": lines[line_number-1],
                "sourceHash": digest(text.encode()), "kind": "application-responsibility", "claim": notice}}

def complete_document(blueprint, lock, version):
    document = assemble_pages(blueprint, lock, version)
    document["version"]["readiness"] = readiness_metadata(lock[version]["commit"], version)
    return document

def validate_corrections(documents):
    rules = read_json(ROOT / "automation/corrections.json")
    if rules.get("schemaVersion") != 1:
        raise ValueError("Unsupported persistent user-correction register")
    landing = rules["landingDocs"]
    slugs = landing["featuredSlugs"]
    if len(slugs) != 3 or len(set(slugs)) != 3:
        raise ValueError("Landing documentation requires exactly three distinct featured pages")
    if landing["mainHref"] != "/docs" or landing["version"] != "default":
        raise ValueError("Landing documentation must use the configured default docs")
    if landing["beforeSectionAriaLabelledby"] != "foundation-title":
        raise ValueError("Landing documentation must immediately precede Your data. Your choice.")
    pages = {page["slug"]: page for page in documents[generation_config()["defaultVersion"]]["pages"]}
    for slug in slugs:
        if slug not in pages or not pages[slug].get("title") or not pages[slug].get("description"):
            raise ValueError("Missing generated metadata for featured documentation page: " + slug)
    return True

def generate_docs(lock):
    blueprints = {v: read_json(ROOT / f"automation/blueprints/{v}.json") for v in ("released", "coming-soon")}
    validate_corrections(blueprints)
    for version, blueprint in blueprints.items():
        blueprint["evidence"] = [anchor_evidence(record) if "anchor" in record else record for record in blueprint["evidence"]]
        validate_blueprint(blueprint, lock, version)
    for version, blueprint in blueprints.items():
        save_json(ROOT / f"automation/blueprints/{version}.json", blueprint)
    emit_examples(lock)
    diagrams = []
    for version, blueprint in blueprints.items():
        document = complete_document(blueprint, lock, version)
        save_json(ROOT / f"content/docs/{version}.json", document)
        readiness_evidence = document["version"]["readiness"].get("evidence")
        save_json(ROOT / f"automation/evidence/{version}.json", blueprint["evidence"] + ([readiness_evidence] if readiness_evidence else []))
        for diagram in blueprint.get("diagrams", []):
            diagrams.append({"version": version, **diagram})
    save_json(CACHE / "diagram-input.json", diagrams)
    save_json(ROOT / "automation/sources.lock.json", lock)
    save_json(ROOT / "public/docs-manifest.json", {"fingerprint": input_fingerprint(lock),
        "sources": {v: {"commit": lock[v]["commit"], "tag": lock[v].get("tag")} for v in blueprints},
        "contentHashes": {v: digest((ROOT / f"content/docs/{v}.json").read_bytes()) for v in blueprints}})
    print("Generated 12 version-isolated pages; evidence verified against immutable source blobs")

def acquire_lease(cache=None, now=None, duration=7200):
    cache = CACHE if cache is None else Path(cache)
    with state_lock(cache):
        return _acquire_lease(cache, now, duration)

def _acquire_lease(cache, now, duration):
    import uuid
    now = time.time() if now is None else now
    cache = Path(cache)
    cache.mkdir(parents=True, exist_ok=True)
    lease = cache / "run.lock"
    try:
        lease.mkdir()
    except FileExistsError:
        existing = read_json(lease / "owner.json")
        if existing["deadline"] > now:
            raise ValueError("Documentation run already active: " + existing["id"])
        cleanup_children(existing)
        lease.rename(cache / ("expired-" + existing["id"]))
        lease.mkdir()
    record = {"id": uuid.uuid4().hex, "started": now, "deadline": now + duration, "repairs": 0, "stages": []}
    save_json(lease / "owner.json", record)
    save_json(cache / "runs" / record["id"] / "run.json", record)
    return record

def guard_run(run_id, cache=None, now=None):
    cache = CACHE if cache is None else Path(cache)
    now = time.time() if now is None else now
    record = read_json(Path(cache) / "run.lock/owner.json")
    if record["id"] != run_id:
        raise ValueError("Run lease owner differs")
    if record["deadline"] <= now:
        raise ValueError("Run expired; publication forbidden")
    if record.get("acceptanceInvalidated"):
        raise ValueError("Acceptance policy changed; explicit new run required")
    if record.get("acceptanceHash") and record["acceptanceHash"] != acceptance_digest():
        record["acceptanceInvalidated"] = True
        save_json(Path(cache) / "run.lock/owner.json", record)
        save_json(Path(cache) / "runs" / run_id / "run.json", record)
        raise ValueError("Acceptance policy changed; explicit new run required")
    return record

def record_stage(run_id, stage, details, cache=None):
    cache = CACHE if cache is None else Path(cache)
    record = guard_run(run_id, cache)
    record["stages"].append({"stage": stage, "details": details})
    save_json(Path(cache) / "run.lock/owner.json", record)
    save_json(Path(cache) / "runs" / run_id / "run.json", record)
    return record

def release_lease(run_id, status, details=None, cache=None):
    cache = CACHE if cache is None else Path(cache)
    with state_lock(cache):
        record = read_json(cache / "run.lock/owner.json")
        if record["id"] != run_id:
            raise ValueError("Run lease owner differs")
        if status != "failed":
            guard_run(run_id, cache)
        cleanup_children(record)
        record.update(status=status, outcome=details)
        save_json(cache / "runs" / run_id / "run.json", record)
        (cache / "run.lock/owner.json").unlink()
        (cache / "run.lock").rmdir()

def execute_stage(run_id, stage, action, cache=None):
    cache = CACHE if cache is None else Path(cache)
    record = guard_run(run_id, cache)
    if record.get("needsRepair"):
        raise ValueError("Failed stage requires an accounted repair before retry")
    try:
        with run_scope(run_id) if cache == CACHE else contextlib.nullcontext():
            result = action()
        guard_run(run_id, cache)
        return result
    except Exception as error:
        with state_lock(cache):
            owner = cache / "run.lock/owner.json"
            if owner.exists():
                record = read_json(owner)
                if record["id"] == run_id:
                    failure = {"stage": stage, "error": str(error), "repairRound": record["repairs"]}
                    record.update(needsRepair=True, failedStage=stage)
                    record["stages"].append({"stage": "failed", "details": failure})
                    save_json(owner, record)
                    save_json(cache / "runs" / run_id / "run.json", record)
                    path = cache / "runs" / run_id / "failures.json"
                    failures = read_json(path) if path.exists() else []
                    save_json(path, failures + [failure])
        raise

def request_repair(run_id, reason, cache=None, now=None):
    cache = CACHE if cache is None else Path(cache)
    with state_lock(cache):
        return _request_repair(run_id, reason, cache, now)

def _request_repair(run_id, reason, cache, now):
    record = guard_run(run_id, cache, now)
    if record["repairs"] >= 2:
        raise ValueError("Automated repair limit reached; retain last valid deployment")
    record["repairs"] += 1
    record["needsRepair"] = False
    record["stages"].append({"stage": "repair", "round": record["repairs"], "reason": reason})
    save_json(Path(cache) / "run.lock/owner.json", record)
    save_json(Path(cache) / "runs" / run_id / "run.json", record)
    return record

def artifact_digest():
    files = run(["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"]).split("\0")
    return digest({name: digest((ROOT / name).read_bytes()) for name in sorted(set(files))
                   if name and not name.startswith("automation/reports/") and (ROOT / name).is_file()})

def public_review_inventory(lock):
    """Enumerate whole-page/section/diagram coverage, not semantic entailment.

    Page targets cover title, description and the complete body. Section targets
    include every paragraph, list, code block and expected output. Diagram targets
    include actual Mermaid nodes/edges, alternatives, captions and relationships.
    The reviewer must independently enumerate claims and inspect original sources;
    these inventories are only a coverage attestation, never privacy proof.
    """
    targets, evidence_ids = [], []

    def identifier(record, key):
        value = record.get(key) if isinstance(record, dict) else None
        if not isinstance(value, str) or not value.strip() or value != value.strip():
            raise ValueError("Public evidence audit candidate identifier is malformed")
        return value

    for version in ("released", "coming-soon"):
        blueprint = read_json(ROOT / f"automation/blueprints/{version}.json")
        commit = identifier(lock.get(version), "commit")
        if blueprint.get("sourceCommit") != commit:
            raise ValueError("Public evidence audit candidate snapshot differs from frozen sources")
        for page in blueprint["pages"]:
            slug = identifier(page, "slug")
            targets.append(f"{version}:page:{slug}")
            for section in page["sections"]:
                targets.append(f"{version}:section:{slug}/{identifier(section, 'id')}")
        for diagram in blueprint.get("diagrams", []):
            targets.append(f"{version}:diagram:{identifier(diagram, 'id')}")
        for record in blueprint["evidence"]:
            if record.get("version") != version or record.get("commit") != commit:
                raise ValueError("Public evidence audit evidence snapshot differs from frozen sources")
            evidence_ids.append(f"{version}:{identifier(record, 'id')}")
    if len(set(targets)) != len(targets) or len(set(evidence_ids)) != len(evidence_ids):
        raise ValueError("Public evidence audit candidate has duplicate identifiers")
    return {"checkedTargets": sorted(targets), "checkedEvidenceIds": sorted(evidence_ids)}


def require_public_evidence_audit(review, cache, run_id):
    audit = review.get("publicEvidenceAudit")
    fields = {"checkedTargets", "checkedEvidenceIds", "unmappedClaims", "unsupportedClaims", "notes"}
    if not isinstance(audit, dict) or set(audit) != fields:
        raise ValueError("Public evidence audit requires exactly the coverage and findings fields")
    if not isinstance(audit["notes"], str) or not audit["notes"].strip():
        raise ValueError("Public evidence audit notes must explain the independent checks")
    for field in ("unmappedClaims", "unsupportedClaims"):
        if not isinstance(audit[field], list) or audit[field]:
            raise ValueError("Public evidence audit cannot pass with unmapped or unsupported claims")
    versions = review.get("versions")
    if (not isinstance(versions, list) or not all(isinstance(version, str) for version in versions)
            or sorted(versions) != ["coming-soon", "released"]):
        raise ValueError("Public evidence audit requires both exact versions")
    run_id = run_id or review.get("runId")
    if not isinstance(run_id, str) or not run_id.strip() or review.get("runId") != run_id:
        raise ValueError("Public evidence audit run identity is required")
    record = guard_run(run_id, cache)
    if not record.get("acceptanceHash"):
        raise ValueError("Acceptance policy not frozen; explicit new run required")
    lock = read_json(cache / "runs" / run_id / "sources.json")
    if digest(lock) != record["sourceHash"]:
        raise ValueError("Frozen source inputs changed during run")
    inventory = public_review_inventory(lock)
    for field, expected in inventory.items():
        values = audit[field]
        if (not isinstance(values, list) or not all(isinstance(value, str) and value.strip() for value in values)
                or len(set(values)) != len(values) or sorted(values) != expected):
            raise ValueError("Public evidence audit requires exact complete unique " + field)


def require_approvals(artifact_hash, cache=None, run_id=None):
    cache = CACHE if cache is None else Path(cache)
    if not (cache / "review.json").exists() or not (cache / "walkthroughs.json").exists():
        raise ValueError("Independent review and fresh walkthrough reports are required")
    review = read_json(cache / "review.json")
    if review.get("status") != "pass" or review.get("artifactHash") != artifact_hash or review.get("findings"):
        raise ValueError("Independent review failed or artifacts changed after review")
    walkthroughs = read_json(cache / "walkthroughs.json")
    expected = {(v, a) for v in ("released", "coming-soon") for a in ("wallet", "point-of-sale")}
    found = {(w.get("version"), w.get("audience")) for w in walkthroughs}
    if len(walkthroughs) != 4 or found != expected:
        raise ValueError("All four version/audience walkthroughs are required")
    if run_id and (review.get("runId") != run_id or not review.get("reviewerRunId")):
        raise ValueError("Independent review run identity is required")
    instruction_mode = read_json(CONFIG).get("privacy", {}).get("boundaryMode") == "instruction-based-public-evidence-v1"
    if instruction_mode:
        if not isinstance(review.get("findings"), list) or review["findings"]:
            raise ValueError("Independent review failed: findings must be an empty list")
        if not isinstance(review.get("notes"), str) or not review["notes"].strip():
            raise ValueError("Independent review notes must be a nonblank string")
        # Ordinary coordinator-assigned session IDs; not cryptographic identity proof.
        author, reviewer = review.get("authorRunId"), review.get("reviewerRunId")
        if (not isinstance(author, str) or not author.strip() or author != author.strip()
                or not isinstance(reviewer, str) or not reviewer.strip() or reviewer != reviewer.strip()
                or author == reviewer):
            raise ValueError("Independent review requires distinct author and reviewer session identities")
        sessions = {author, reviewer}
        for result in walkthroughs:
            reporter = result.get("reporterRunId")
            if (not isinstance(reporter, str) or not reporter.strip() or reporter != reporter.strip()
                    or reporter in sessions):
                raise ValueError("Walkthrough identities must be four distinct sessions separate from author and reviewer")
            sessions.add(reporter)
    for result in walkthroughs:
        if (("status" in result and result["status"] != "pass")
                or (instruction_mode and type(result.get("exitCode")) is not int)
                or result.get("exitCode") != 0 or result.get("artifactHash") != artifact_hash
                or not stdout_matches_expected(result.get("stdout"), EXPECTED[result["audience"]])):
            raise ValueError("Fresh walkthrough failed or is stale")
        if not result.get("runId") or (run_id and result["runId"] != run_id) or not result.get("reporterRunId"):
            raise ValueError("Fresh walkthrough run identity is required")
        slug = "wallets" if result["audience"] == "wallet" else "point-of-sale"
        guide = urlparse(result.get("guideUrl", ""))
        if guide.scheme not in {"http", "https"} or not guide.hostname or guide.path.rstrip("/") != f"/docs/{result['version']}/{slug}":
            raise ValueError("Walkthrough guide URL does not match version and audience")
        commands = result.get("commands")
        if not isinstance(commands, list) or not commands or not all(isinstance(c, str) and c.strip() for c in commands) or not any(re.search(r"\bcargo\s+run\s+--locked\b", c) for c in commands):
            raise ValueError("Walkthrough commands must record literal locked cargo run")
        archive = ROOT / f"public/docs-examples/{result['version']}/paykit-{result['audience']}-{result['version']}.tar.gz"
        if not archive.is_file() or result.get("archiveHash") != digest(archive.read_bytes()):
            raise ValueError("Walkthrough archive hash is missing or stale")
    if instruction_mode:
        require_public_evidence_audit(review, cache, run_id)
    return {"review": review, "walkthroughs": walkthroughs}

def frozen_sources(run_id):
    record = guard_run(run_id)
    if not record.get("acceptanceHash"):
        raise ValueError("Acceptance policy not frozen; explicit new run required")
    if record.get("tools") and record["tools"] != tool_versions():
        record["acceptanceInvalidated"] = True
        save_json(CACHE / "run.lock/owner.json", record)
        save_json(CACHE / "runs" / run_id / "run.json", record)
        raise ValueError("Acceptance tool versions changed; explicit new run required")
    if record.get("processFingerprint") and record["processFingerprint"] != process_fingerprint(tools=record.get("tools")):
        record["acceptanceInvalidated"] = True
        save_json(CACHE / "run.lock/owner.json", record)
        save_json(CACHE / "runs" / run_id / "run.json", record)
        raise ValueError("Generation process inputs changed; explicit new run required")
    lock = read_json(CACHE / "runs" / run_id / "sources.json")
    if digest(lock) != record["sourceHash"]:
        raise ValueError("Frozen source inputs changed during run")
    return lock

def credential_health():
    from datetime import datetime, timezone
    env = os.environ.copy()
    env.pop("GH_TOKEN", None)
    response = run(["gh", "api", "-i", "user", "--jq", ".login"], env=env, timeout=45)
    warnings = []
    expiry = None
    for line in response.splitlines():
        if line.lower().startswith("github-authentication-token-expiration:"):
            expiry = line.split(":", 1)[1].strip()
            date = datetime.strptime(expiry, "%Y-%m-%d %H:%M:%S UTC").replace(tzinfo=timezone.utc)
            days = (date - datetime.now(timezone.utc)).days
            if days <= 30:
                warnings.append(f"GitHub token expires {date.date().isoformat()} ({days} days); renewal needed")
    return {"expiresAt": expiry, "warnings": warnings}

def plan_changes(lock, reasons=None):
    if reasons and any(reason in reasons for reason in ("generation-process", "explicit-force", "initial-or-legacy-checkpoint")):
        pages = [slug for slugs in read_json(CONFIG)["sections"].values() for slug in slugs]
        return {version: {"affectedPages": pages, "changedSourceFiles": [], "fullAuthoringRequired": True,
                          "reason": ", ".join(reasons), "provenanceRefresh": True}
                for version in ("released", "coming-soon")}
    previous_path = ROOT / "automation/sources.lock.json"
    if not previous_path.exists():
        return {v: {"affectedPages": [s for values in read_json(CONFIG)["sections"].values() for s in values], "reason": "initial generation"} for v in ("released", "coming-soon")}
    confirmed_path = CACHE / "last-success.json"
    confirmed = read_json(confirmed_path) if confirmed_path.exists() else {}
    previous = (confirmed["sources"] if confirmed.get("verifiedDeployment") is True and
                all(version in confirmed.get("sources", {}) for version in ("released", "coming-soon"))
                else read_json(previous_path))
    result = {}
    for version in ("released", "coming-soon"):
        before, after = previous[version]["commit"], lock[version]["commit"]
        changed = run(["git", "diff", "--name-only", before, after], cwd=REPO).splitlines() if before != after else []
        blueprint = read_json(ROOT / f"automation/blueprints/{version}.json")
        evidence = {e["id"]: e for e in blueprint["evidence"]}
        affected = [p["slug"] for p in blueprint["pages"] if any(evidence[e]["path"] in changed for s in p["sections"] for e in s["evidence"])]
        result[version] = {"from": before, "to": after, "changedSourceFiles": changed, "affectedPages": affected,
                           "provenanceRefresh": before != after}
    return result

def validate_generated(lock):
    validate_corrections({version: read_json(ROOT / f"content/docs/{version}.json") for version in ("released", "coming-soon")})
    for version in ("released", "coming-soon"):
        blueprint = read_json(ROOT / f"automation/blueprints/{version}.json")
        validate_blueprint(blueprint, lock, version)
        if read_json(ROOT / f"content/docs/{version}.json") != complete_document(blueprint, lock, version):
            raise ValueError("Generated pages were manually changed; fix process and regenerate")
        for audience in ("wallet", "point-of-sale"):
            directory = ROOT / f"public/docs-examples/{version}/{audience}"
            cargo_lock = tomllib.loads((directory / "Cargo.lock").read_text())
            entries = [p for p in cargo_lock["package"] if p["name"] == "paykit-lib"]
            if len(entries) != 1:
                raise ValueError("Example dependency lock is ambiguous")
            pkg = entries[0]
            if version == "released":
                expected = lock[version]["packages"]["paykit-lib"]
                if pkg["version"] != expected["version"] or pkg.get("checksum") != expected["checksum"]:
                    raise ValueError("Released example does not match verified published package")
            elif not pkg.get("source", "").endswith("#" + lock[version]["commit"]):
                raise ValueError("Development example is not pinned to documented commit")
        for diagram in blueprint.get("diagrams", []):
            directory = ROOT / f"public/docs-assets/{version}"
            if (directory / (diagram["id"] + ".mmd")).read_text().strip() != diagram["mermaid"].strip():
                raise ValueError("Diagram source differs from evaluated blueprint")
            svg = (directory / (diagram["id"] + ".svg")).read_text()
            if "<svg" not in svg or re.search(r"<foreignObject|<script|\son\w+=|(?:href|src)=[\"'](?:https?:|data:|javascript:)", svg, re.I):
                raise ValueError("Diagram did not render safely")
    manifest = read_json(ROOT / "public/docs-manifest.json")
    if manifest["fingerprint"] != input_fingerprint(lock):
        raise ValueError("Pipeline inputs changed after generation; regenerate explicitly")
    return True

def validate_all(run_id):
    return execute_stage(run_id, "validate", lambda: _validate_all(run_id))

def _validate_all(run_id):
    lock = frozen_sources(run_id)
    validate_generated(lock)
    initial_artifacts = artifact_digest()
    results = []
    commands = [
        ["python", "-m", "unittest", "discover", "-s", "automation/tests", "-v"],
        ["npm", "test"], ["npm", "run", "typecheck"], ["npm", "run", "build"],
        ["npm", "run", "test:browser", "--", "--reporter=json"],
        ["node", "automation/capture-layout.mjs"], ["python", "automation/check-links.py"], ["git", "diff", "--check"]
    ]
    examples = test_examples(lock, write_artifacts=False)
    for index, command in enumerate(commands):
        record = guard_run(run_id)
        remaining = int(record["deadline"] - time.time())
        env = os.environ.copy()
        env["PLAYWRIGHT_JSON_OUTPUT_FILE"] = str(CACHE / "browser-results.json")
        env["PAYKIT_TEST_SOURCES"] = str(CACHE / "runs" / run_id / "sources.json")
        result = bounded_process(command, cwd=ROOT, env=env, timeout=max(1, min(1200, remaining)))
        check_effect()
        (CACHE / "runs" / run_id / f"check-{index}.log").write_text(result.stdout + "\n" + result.stderr)
        item = {"command": command, "exitCode": result.returncode}
        results.append(item)
        record_stage(run_id, "check", item)
        print("PASS " if result.returncode == 0 else "FAIL ", " ".join(command), flush=True)
        if result.returncode:
            raise ValueError("Validation failed: " + " ".join(command) + "\n" + result.stdout[-5000:] + result.stderr[-5000:])
    frozen_sources(run_id)
    validate_generated(lock)
    if artifact_digest() != initial_artifacts:
        raise ValueError("Artifacts changed during validation; regenerate and revalidate")
    lease = guard_run(run_id)
    report = {"runId": run_id, "repairRound": lease["repairs"], "acceptanceHash": lease.get("acceptanceHash"),
              "artifactHash": initial_artifacts, "fingerprint": input_fingerprint(lock), "checks": results,
              "examples": examples, "browserStats": read_json(CACHE / "browser-results.json")["stats"]}
    save_json(CACHE / "validation.json", report)
    record_stage(run_id, "validated", {"artifactHash": report["artifactHash"], "checks": len(results)})
    print(json.dumps(report, indent=2))
    return report

def approved_push_url():
    cfg = read_json(CONFIG)["publishing"]
    repo = read_json(CACHE / "deployment.json")["repository"]
    if cfg["remote"] != "origin" or cfg["branch"] != "docs-automation" or not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo) or repo.lower().startswith("pubky/"):
        raise ValueError("Publishing remote is not the explicitly configured fork")
    approved = f"https://github.com/{repo}.git"
    if run(["git", "remote", "get-url", "origin"], cwd=ROOT) != approved:
        raise ValueError("Publishing fetch URL is not approved fork")
    destinations = run(["git", "remote", "get-url", "--push", "--all", "origin"], cwd=ROOT).splitlines()
    # Resolve the explicit URL too: pushInsteadOf can rewrite URL arguments.
    rewrites = run(["git", "config", "--null", "--list"], cwd=ROOT).split("\0")
    for entry in rewrites:
        key, _, value = entry.partition("\n")
        for suffix in (".pushinsteadof", ".insteadof"):
            if key.startswith("url.") and key.endswith(suffix) and approved.startswith(value):
                if key[4:-len(suffix)] + approved[len(value):] != approved:
                    raise ValueError("Explicit push URL would be rewritten outside approved fork")
    if destinations != [approved]:
        raise ValueError("Effective push destination is not the single approved fork")
    return approved

def fork_api(endpoint):
    approved_push_url()
    cfg = read_json(CACHE / "deployment.json")
    repo = cfg["repository"]
    env = os.environ.copy()
    env.pop("GH_TOKEN", None)
    return json.loads(run(["gh", "api", f"repos/{repo}/{endpoint}"], env=env, timeout=60))

PUBLISH_PATHS = (".gitignore", "AGENTS.md", "docs-generation.json", "automation", "content/docs", "app/docs", "components/docs",
                 "components/Landing.tsx", "package.json", "package-lock.json", "playwright.config.ts", "tests",
                 "public/docs-examples", "public/docs-assets", "public/docs-manifest.json")

def enforce_publication_scope(base=None):
    def check(names):
        for name in names.split("\0"):
            if name and not any(name == allowed or name.startswith(allowed + "/") for allowed in PUBLISH_PATHS):
                raise ValueError("Publication path outside scope: " + name)
    check(run(["git", "diff", "--cached", "--name-only", "--no-renames", "-z"], cwd=ROOT))
    if base:
        run(["git", "merge-base", "--is-ancestor", base, "HEAD"], cwd=ROOT)
        for commit in run(["git", "rev-list", base + "..HEAD"], cwd=ROOT).splitlines():
            check(run(["git", "diff-tree", "--root", "-m", "-r", "--no-commit-id", "--no-renames", "--name-only", "-z", commit], cwd=ROOT))

def stage_reviewed_candidate(expected_hash, base=None):
    enforce_publication_scope(base)
    run(["git", "add", "--", *PUBLISH_PATHS])
    enforce_publication_scope(base)
    pending = run(["git", "diff", "--name-only", "--no-renames", "-z"]).split("\0")
    pending += run(["git", "ls-files", "--others", "--exclude-standard", "-z"]).split("\0")
    omitted = sorted({name for name in pending if name and not name.startswith("automation/reports/")})
    if omitted:
        raise ValueError("Reviewed candidate omitted unstaged/untracked inputs: " + ", ".join(omitted))
    if artifact_digest() != expected_hash:
        raise ValueError("Staged candidate differs from validated/reviewed artifact bytes")
    return run(["git", "write-tree"])

def publish(run_id):
    with run_scope(run_id):
        return _publish(run_id)

def _publish(run_id):
    require_native_context(run_id)
    configured_stable_alias()  # Syntax/configuration only; deployment still serves the old inputs.
    lock = frozen_sources(run_id)
    validate_generated(lock)
    cfg = read_json(CONFIG)
    if guard_run(run_id).get("needsRepair"):
        raise ValueError("Failed stage requires an accounted repair before publication")
    if run(["git", "branch", "--show-current"]) != cfg["publishing"]["branch"]:
        raise ValueError("Publication is allowed only from docs-automation")
    report = read_json(CACHE / "validation.json")
    current_hash = artifact_digest()
    lease = guard_run(run_id)
    if report.get("runId") != run_id or report.get("repairRound") != lease["repairs"] or report.get("acceptanceHash") != lease.get("acceptanceHash") or report["artifactHash"] != current_hash or report["fingerprint"] != input_fingerprint(lock):
        raise ValueError("Validation is stale")
    require_approvals(current_hash, run_id=run_id)
    metadata = fork_api("")
    if metadata.get("fork") is not True or metadata.get("parent", {}).get("full_name") != "pubky/paykit-landing-page":
        raise ValueError("Website fork relationship changed")
    if not metadata.get("permissions", {}).get("push"):
        raise ValueError("Existing credential cannot push to fork")
    branch = cfg["publishing"]["branch"]
    destination = approved_push_url()
    refs = run(["git", "ls-remote", destination, "refs/heads/" + branch]).split()
    if not refs:
        refs = run(["git", "ls-remote", destination, "refs/heads/" + metadata["default_branch"]]).split()
    if not refs:
        raise ValueError("Cannot establish outgoing publication scope")
    base = refs[0]
    checked_tree = stage_reviewed_candidate(current_hash, base)
    staged = run(["git", "diff", "--cached", "--name-only"])
    if staged:
        run(["git", "diff", "--cached", "--check"])
        guard_run(run_id)
        enforce_publication_scope(base)
        guard_run(run_id)
        run(["git", "commit", "-m", f"docs: generate {lock['released']['tag']} and development guides with verified automation"])
    sha = run(["git", "rev-parse", "HEAD"])
    if run(["git", "rev-parse", "HEAD^{tree}"]) != checked_tree or artifact_digest() != current_hash:
        raise ValueError("Committed candidate differs from reviewed/tested staged tree")
    enforce_publication_scope(base)
    destination = approved_push_url()
    guard_run(run_id)
    run(["git", "push", destination, f"HEAD:refs/heads/{branch}"], timeout=180)
    remote = run(["git", "ls-remote", destination, f"refs/heads/{branch}"]).split()[0]
    if remote != sha:
        raise ValueError("Remote branch readback does not match push")
    save_json(CACHE / "pending-publication.json", {"runId": run_id, "sha": sha, "fingerprint": input_fingerprint(lock)})
    record_stage(run_id, "pushed", {"sha": sha})
    print(json.dumps({"sha": sha, "branch": cfg["publishing"]["branch"], "deployment": "not yet verified"}, indent=2))

def finalize_success(run_id, checkpoint, cache=None):
    cache = CACHE if cache is None else Path(cache)
    with state_lock(cache):
        record = guard_run(run_id, cache)
        cleanup_children(record)
        details = {"sha": checkpoint.get("gitCommit"), "url": checkpoint.get("deploymentUrl")}
        record["stages"].append({"stage": "deployment-verified", "details": details})
        record.update(status="finalizing", outcome=details)
        save_json(cache / "runs" / run_id / "run.json", record)
        temporary = cache / ("last-success." + run_id + ".tmp")
        temporary.write_text(json.dumps(checkpoint, indent=2) + "\n")
        try:
            guard_run(run_id, cache)
            temporary.replace(cache / "last-success.json")
        finally:
            temporary.unlink(missing_ok=True)
        # No fallible lease guard after committing the checkpoint.
        record["status"] = "published"
        save_json(cache / "runs" / run_id / "run.json", record)
        (cache / "run.lock/owner.json").unlink()
        (cache / "run.lock").rmdir()

def vercel_fallback_url(sha):
    status = fork_api(f"commits/{sha}/status")
    if status.get("sha") != sha:
        return None
    vercel = next((s for s in status.get("statuses", []) if s.get("context") == "Vercel"), None)
    if not vercel:
        return None
    if vercel.get("state") in {"failure", "error"}:
        raise ValueError("Vercel matching-SHA build failed")
    if vercel.get("state") != "success" or urlparse(vercel.get("target_url", "")).hostname != "vercel.com":
        return None
    checks = fork_api(f"commits/{sha}/check-runs?per_page=100")
    for check in checks.get("check_runs", []):
        if check.get("head_sha") != sha or check.get("name") != "Vercel Preview Comments" or check.get("app", {}).get("slug") != "vercel":
            continue
        summary = check.get("output", {}).get("summary", "")
        for host in re.findall(r"https://vercel\.live/open-feedback/([a-z0-9][a-z0-9.-]*\.vercel\.app)(?=[?/)\s]|$)", summary):
            if ".." not in host:
                return "https://" + host
    return None

def configured_stable_alias():
    private = CACHE / "deployment.json"
    configuration = read_json(private) if private.exists() else {}
    url = configuration.get("stableDevelopmentUrl")
    # Publication at the agreed alias is independent of the privacy/execution mode.
    if not url:
        raise ValueError("Required stable development alias is not configured")
    if not isinstance(url, str) or not re.fullmatch(r"https://(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+vercel\.app/?", url):
        raise ValueError("Invalid configured stable development alias")
    return url.rstrip("/")

def verify_stable_alias(expected_manifest):
    url = configured_stable_alias()
    if url is None:
        return None
    if get_json(url + "/docs-manifest.json", same_origin=True) != expected_manifest:
        raise ValueError("Stable development alias does not serve the accepted documentation inputs")
    return url

def verify_publication(run_id):
    with run_scope(run_id):
        return _verify_publication(run_id)

def _verify_publication(run_id):
    lock = frozen_sources(run_id)
    pending = read_json(CACHE / "pending-publication.json")
    if pending["runId"] != run_id:
        raise ValueError("Pending publication belongs to another run")
    sha = pending["sha"]
    cfg = read_json(CONFIG)
    deadline = min(time.time() + cfg["publishing"]["deploymentTimeoutSeconds"], guard_run(run_id)["deadline"])
    url = None
    diagnostics = []
    while time.time() < deadline:
        deployments = fork_api(f"deployments?sha={sha}&per_page=100")
        for deployment in deployments:
            statuses = fork_api(f"deployments/{deployment['id']}/statuses?per_page=100")
            if not statuses:
                continue
            status = statuses[0]
            diagnostics.append({"deploymentId": deployment["id"], "state": status["state"], "environment": deployment["environment"]})
            if status["state"] in {"failure", "error"}:
                raise ValueError("Vercel deployment failed: " + json.dumps(status))
            candidate = status.get("environment_url") or status.get("target_url")
            if status["state"] == "success" and candidate and urlparse(candidate).scheme == "https" and (urlparse(candidate).hostname or "").endswith(".vercel.app"):
                url = candidate.rstrip("/")
                break
        if not url and not deployments:
            url = vercel_fallback_url(sha)
        if url:
            break
        bounded_sleep(15)
    if not url:
        raise ValueError("No successful matching Vercel deployment: " + json.dumps(diagnostics[-10:]))
    manifest = get_json(url + "/docs-manifest.json", same_origin=True)
    local = read_json(ROOT / "public/docs-manifest.json")
    if manifest != local or manifest["fingerprint"] != pending["fingerprint"]:
        raise ValueError("Deployed documentation provenance does not match published inputs")
    stable_url = verify_stable_alias(local)
    if fork_api(f"branches/{quote(cfg['publishing']['branch'], safe='')}")["commit"]["sha"] != sha:
        raise ValueError("Remote publication branch moved during deployment")
    checkpoint = {"fingerprint": pending["fingerprint"], "sources": lock, "gitCommit": sha, "deploymentUrl": url,
                  "runId": run_id, "verifiedDeployment": True, "stableDevelopmentUrl": stable_url}
    owner = guard_run(run_id)
    for key in ("regenerationFingerprint", "processFingerprint"):
        if key in owner:
            checkpoint[key] = owner[key]
    finalize_success(run_id, checkpoint)
    print(json.dumps({"published": sha, "url": url}, indent=2))
    return checkpoint

def begin_run(args):
    record = acquire_lease(duration=read_json(CONFIG)["limits"]["runtimeSeconds"])
    record["acceptanceHash"] = acceptance_digest()
    save_json(CACHE / "run.lock/owner.json", record)
    save_json(CACHE / "runs" / record["id"] / "run.json", record)
    try:
        with run_scope(record["id"]):
            bind_native_context(record)
            lock = read_json(CACHE / "candidate-sources.json") if args.reuse_candidate else resolve_sources()
            validate_source_selection(lock)
            health = credential_health()
            tools = tool_versions()
            fingerprint = input_fingerprint(lock, tools=tools)
            trigger = regeneration_fingerprint(lock, tools=tools)
            process = process_fingerprint(tools=tools)
            record.update(tools=tools, sourceHash=digest(lock), fingerprint=fingerprint, credentialHealth=health,
                          regenerationFingerprint=trigger, processFingerprint=process)
            save_json(CACHE / "run.lock/owner.json", record)
            save_json(CACHE / "runs" / record["id"] / "sources.json", lock)
            save_json(CACHE / "candidate-sources.json", lock)
            previous = read_json(CACHE / "last-success.json") if (CACHE / "last-success.json").exists() else {}
            force = getattr(args, "force", False)
            if not force and previous.get("verifiedDeployment") is True and previous.get("regenerationFingerprint") == trigger:
                manifest = get_json(previous["deploymentUrl"] + "/docs-manifest.json", same_origin=True)
                if manifest["fingerprint"] != previous["fingerprint"]:
                    raise ValueError("Previously published deployment provenance changed")
                verify_stable_alias(manifest)
                release_lease(record["id"], "no-change", health)
                print(json.dumps({"action": "no-change", "warnings": health["warnings"]}))
                return
            record["updateReasons"] = (["initial-or-legacy-checkpoint"] if previous.get("verifiedDeployment") is not True or not previous.get("regenerationFingerprint")
                                       else (["selected-source-commit"] if previous.get("sources", {}).get("coming-soon", {}).get("commit") != lock["coming-soon"]["commit"] else [])
                                       + (["generation-process"] if previous.get("processFingerprint") != process else []))
            if force:
                record["updateReasons"].append("explicit-force")
            save_json(CACHE / "runs" / record["id"] / "run.json", record)
            save_json(CACHE / "run.lock/owner.json", record)
            print(json.dumps({"action": "update", "runId": record["id"], "deadline": record["deadline"], "warnings": health["warnings"], "reasons": record["updateReasons"], "changes": plan_changes(lock, record["updateReasons"])}, indent=2))
    except Exception as error:
        release_lease(record["id"], "failed", {"error": str(error)})
        raise
    return

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["resolve", "begin", "plan", "generate", "examples", "validate", "repair", "publish", "verify", "fail", "artifact-hash", "review-targets", "status"])
    parser.add_argument("--run")
    parser.add_argument("--reuse-candidate", action="store_true")
    parser.add_argument("--force", action="store_true", help="Force begin to regenerate; all source, review and publication gates still apply")
    parser.add_argument("--create-locks", action="store_true")
    parser.add_argument("--reason", default="generation check failed")
    args = parser.parse_args()
    if args.force and args.command != "begin":
        parser.error("--force is only valid for begin")
    if args.command == "artifact-hash":
        print(artifact_digest())
        return
    if args.command == "resolve":
        lock = resolve_sources()
        save_json(CACHE / "candidate-sources.json", lock)
        print(json.dumps({k: {"commit": lock[k]["commit"], "label": lock[k]["label"]} for k in ("released", "coming-soon")}, indent=2))
        return
    if args.command == "begin":
        return begin_run(args)
    if not args.run:
        parser.error("--run is required for this stage")
    if args.command == "review-targets":
        # frozen_sources guards the existing run. No active write scope: hashing
        # must not register child bookkeeping or change reports/checkpoints.
        return dispatch_stage(args)
    if args.command == "fail":
        release_lease(args.run, "failed", {"error": args.reason})
        return
    with run_scope(args.run):
        return dispatch_stage(args)

def dispatch_stage(args):
    lock = frozen_sources(args.run)
    if args.command == "review-targets":
        artifact_hash = artifact_digest()
        inventory = public_review_inventory(lock)
        frozen_sources(args.run)
        if artifact_digest() != artifact_hash:
            raise ValueError("Artifacts changed while assembling public review targets")
        print(json.dumps({"runId": args.run, "artifactHash": artifact_hash, **inventory}, indent=2))
    elif args.command == "plan":
        print(json.dumps(plan_changes(lock, guard_run(args.run).get("updateReasons")), indent=2))
    elif args.command == "generate":
        def generate():
            generate_docs(lock)
            run(["node", "automation/render-diagrams.mjs"], timeout=120)
            record_stage(args.run, "generated", {"artifactHash": artifact_digest()})
        execute_stage(args.run, "generate", generate)
    elif args.command == "examples":
        execute_stage(args.run, "examples", lambda: test_examples(lock, create_locks=args.create_locks))
    elif args.command == "validate":
        validate_all(args.run)
    elif args.command == "repair":
        print(json.dumps(request_repair(args.run, args.reason)))
    elif args.command == "publish":
        publish(args.run)
    elif args.command == "verify":
        verify_publication(args.run)
    elif args.command == "fail":
        release_lease(args.run, "failed", {"error": args.reason})
    elif args.command == "status":
        print(json.dumps(guard_run(args.run), indent=2))

def approved_source(url):
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.username or parsed.password or parsed.port:
        return False
    if parsed.hostname == "github.com":
        return parsed.path == "/pubky/paykit-rs" or parsed.path.startswith("/pubky/paykit-rs/")
    if parsed.hostname in {"crates.io", "docs.rs", "static.crates.io"}:
        return True
    return False

STABLE = re.compile(r"v?(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:\+[0-9A-Za-z.-]+)?\Z")

def select_release(releases):
    candidates = []
    for release in releases:
        match = STABLE.fullmatch(release.get("tag_name", ""))
        if match and release.get("draft") is False and release.get("prerelease") is False:
            candidates.append((tuple(map(int, match.groups())), release["tag_name"], release))
    if not candidates:
        raise ValueError("No qualifying published stable release; retain last valid docs")
    return max(candidates, key=lambda item: item[:2])[2]

if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(f"Documentation pipeline failed: {type(error).__name__}: {error}", file=sys.stderr)
        sys.exit(1)
