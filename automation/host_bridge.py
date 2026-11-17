#!/usr/bin/env python3
"""Narrow public-only Docker bridge. Trusted host fixes run/role/session.

The public agent must never receive this CLI or a generic terminal: it receives
only host custom-tool closures with these three arguments fixed. See the exact
protocol in /workspace/paykit-reports/native-bridge-protocol.json. No SDK needed.
"""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path, PurePosixPath
import re
import sys
import zlib

_SPEC = importlib.util.spec_from_file_location("bridge_pipeline", Path(__file__).with_name("pipeline.py"))
p = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(p)
MAX_REQUEST = 1048576
MAX_TOKEN = 120000  # Includes framing; leave headroom below Linux's argv limit.
MAX_RESPONSE = 18000
WIRE_PREFIX = 'pkhex1:'


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Invalid message")
        result[key] = value
    return result


def _nonfinite(value):
    raise ValueError("Invalid message")


def encode_message(value):
    raw = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode()
    if len(raw) > MAX_REQUEST:
        raise ValueError("Message too large")
    # Machine framing only, for already-authorized public RPC payloads. This
    # changes no classification, capability, path or SDK security policy.
    token = WIRE_PREFIX + zlib.compress(raw).hex()
    if len(token) > MAX_TOKEN:
        raise ValueError("Message too large")
    return token


def decode_message(token):
    try:
        if not isinstance(token, str) or len(token) > MAX_TOKEN or not re.fullmatch(r"pkhex1:[0-9a-f]+", token):
            raise ValueError("Invalid message")
        compressed = bytes.fromhex(token[len(WIRE_PREFIX):])
        decoder = zlib.decompressobj()
        raw = decoder.decompress(compressed, MAX_REQUEST + 1)
        if len(raw) > MAX_REQUEST or not decoder.eof or decoder.unused_data or decoder.unconsumed_tail:
            raise ValueError("Invalid message")
        result = json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs, parse_constant=_nonfinite)
        if type(result) is not dict:
            raise ValueError("Invalid message")
        return result
    except (UnicodeError, zlib.error, RecursionError, json.JSONDecodeError) as error:
        raise ValueError("Invalid message") from error


VERSIONS = ("released", "coming-soon")
AUDIENCES = ("wallet", "point-of-sale")
READ_OPS = {"bootstrap", "read_inputs", "source_inventory", "read_source", "read_blueprint"}
ROLE_OPS = {
    "analyst": READ_OPS,
    "author": READ_OPS | {"submit_blueprint", "draft_open", "draft_read", "draft_edit", "draft_finalize"},
    "diagram": READ_OPS | {"submit_blueprint"},
    "reviewer": READ_OPS | {"get_review_inputs", "artifact_hash", "submit_review"},
    "walkthrough": {"walkthrough_prepare", "walkthrough_run", "submit_walkthrough"},
}
SCHEMAS = {
    "bootstrap": (set(), set()),
    "read_inputs": ({"path"}, {"offset", "limit"}),
    "source_inventory": ({"version"}, {"offset", "limit"}),
    "read_source": ({"version", "path"}, {"offset", "limit"}),
    "read_blueprint": ({"version"}, {"offset", "limit"}),
    "submit_blueprint": ({"version", "blueprint"}, set()),
    "draft_open": ({"version"}, set()),
    "draft_read": ({"version", "revision", "selector", "offset", "limit"}, set()),
    "draft_edit": ({"version", "expectedRevision", "edits"}, set()),
    "draft_finalize": ({"version", "expectedRevision"}, set()),
    "get_review_inputs": (set(), set()), "artifact_hash": (set(), set()),
    "submit_review": ({"report"}, set()),
    "walkthrough_prepare": ({"version", "audience"}, {"offset", "limit"}),
    "walkthrough_run": ({"version", "audience", "commands"}, set()),
    "submit_walkthrough": ({"version", "audience"}, set()),
}
REQUIRED_POLICY_PATHS = ['AGENTS.md', 'automation/COORDINATOR.md', 'automation/README.md',
    'automation/SOURCE-BOUNDARY.md', 'automation/config.json', 'automation/corrections.json',
    'automation/skills/coordinator.md', 'automation/skills/sources.md', 'automation/skills/writing.md',
    'automation/skills/diagrams.md', 'automation/skills/evaluation.md']
PUBLIC_PATTERNS = (
    "AGENTS.md", "automation/COORDINATOR.md", "automation/SOURCE-BOUNDARY.md",
    "automation/config.json", "automation/corrections.json", "automation/README.md",
    "automation/pipeline.py", "automation/host_bridge.py", "automation/bootstrap.py",
    "automation/*.mjs", "automation/check-links.py", "automation/skills/*.md",
    "automation/recipes/*.rs", "automation/tests/*.py", "automation/blueprints/*.json",
    "automation/evidence/*.json", "automation/sources.lock.json",
    "content/docs/*.json", "app/docs/**/*.tsx", "app/docs/**/*.css",
    "components/docs/*.tsx", "components/docs/*.css", "components/Landing.tsx",
    "tests/**/*.mjs", "tests/**/*.ts", "package.json", "package-lock.json",
    "playwright.config.ts", "public/docs-manifest.json", "public/docs-assets/**/*",
    "public/docs-examples/**/*",
)


def fields(value, required, optional=()):
    if type(value) is not dict or not set(required) <= value.keys() or value.keys() - set(required) - set(optional):
        raise ValueError("Invalid fields")


def canonical_path(name):
    if not isinstance(name, str) or not name or len(name) > 512 or not re.fullmatch(r"[A-Za-z0-9_.@+/-]+", name):
        raise ValueError("Denied path")
    parts = name.split("/")
    if any(part in ("", ".", "..") for part in parts):
        raise ValueError("Denied path")
    for part in parts:
        low = part.lower()
        if low in {".git", ".automation", ".ssh", ".aws", ".gnupg", "credentials", "credentials.json", "id_rsa", "id_ed25519"} or low.startswith('.env') or low.endswith(('.pem', '.key')):
            raise ValueError("Denied path")
    return name


def target(root, name):
    """Trusted relative output names only; reject *all* symlink ancestors."""
    root = Path(root)
    result = root / name
    if result != root and not result.is_relative_to(root):
        raise ValueError("Denied path")
    for item in [root, *result.relative_to(root).parents]:
        candidate = item if item == root else root / item
        if candidate.is_symlink():
            raise ValueError("Denied path")
    current = root
    for part in result.relative_to(root).parts:
        if part in ('', '.', '..'):
            raise ValueError("Denied path")
        current = current / part
        if current.is_symlink():
            raise ValueError("Denied path")
    return result


def safe_read(root, name, max_bytes=2097152):
    """Open beneath an owned root using O_NOFOLLOW for every component."""
    import os
    canonical_path(name)
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        parts = name.split('/')
        for part in parts[:-1]:
            next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = next_fd
        file_fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
        with os.fdopen(file_fd, 'rb') as handle:
            import stat
            if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
                raise ValueError("Denied path")
            result = handle.read(max_bytes + 1)
        if len(result) > max_bytes:
            raise ValueError("Public file too large")
        return result
    finally:
        os.close(fd)


def public_paths():
    candidates = set(p.run(["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"], cwd=p.ROOT).split('\0'))
    # --cached includes force-added ignored paths; they are not public inputs.
    ignored = set(p.run(["git", "ls-files", "--cached", "--ignored", "--exclude-standard", "-z"], cwd=p.ROOT).split('\0'))
    candidates -= ignored
    selected = {str(file.relative_to(p.ROOT)) for pattern in PUBLIC_PATTERNS for file in p.ROOT.glob(pattern) if file.is_file()}
    result = []
    for name in sorted(candidates & selected):
        try:
            canonical_path(name)
            target(p.ROOT, name)
            if not any(name == allowed or name.startswith(allowed + '/') for allowed in p.PUBLISH_PATHS):
                continue
            result.append(name)
        except ValueError:
            pass
    return result


def paginate(text, args):
    offset, limit = args.get('offset', 0), args.get('limit', 6000)
    if type(offset) is not int or type(limit) is not int or not 0 <= offset <= len(text) or not 1 <= limit <= 6000:
        raise ValueError("Invalid pagination")
    end = min(offset + limit, len(text))
    return {'text':text[offset:end], 'offset':offset, 'nextOffset':end if end < len(text) else None,
            'totalChars':len(text), 'sha256':p.digest(text.encode())}


def source_tree(lock, version):
    commit = lock[version]['commit']
    if not re.fullmatch('[a-f0-9]{40}', commit):
        raise ValueError("Invalid snapshot")
    raw = p.run(['git', 'ls-tree', '-r', '-l', '-z', commit], cwd=p.REPO, timeout=60)
    result = {}
    for entry in raw.split('\0'):
        if not entry:
            continue
        header, name = entry.split('\t', 1)
        mode, kind, blob, size = header.split()
        try:
            canonical_path(name)
        except ValueError:
            continue
        if mode in ('100644', '100755') and kind == 'blob' and int(size) <= 2097152:
            result[name] = blob
    return result


def source_bytes(lock, version, name):
    canonical_path(name)
    if name not in source_tree(lock, version):
        raise ValueError("Denied source")
    return p.source_text(lock[version]['commit'], name)


def session_path(run_id, session, filename):
    return target(p.CACHE, f'runs/{run_id}/bridge/{session}/{filename}')


def bind_session(run_id, role, session):
    with p.state_lock():
        path = session_path(run_id, session, 'identity.json')
        if path.exists() and p.read_json(path) != {'role':role, 'session':session}:
            raise ValueError("Session role differs")
        p.save_json(path, {'role':role, 'session':session})


def public_review_feedback(run_id):
    """Export only a bridge-issued public review, never arbitrary cache text."""
    path = target(p.CACHE, 'review.json')
    if not path.exists():
        return None
    report = p.read_json(path)
    if not isinstance(report, dict) or report.get('runId') != run_id:
        return None
    session = report.get('reviewerRunId')
    if not isinstance(session, str) or not re.fullmatch('[A-Za-z0-9_-]{1,96}', session):
        raise ValueError('Invalid public review provenance')
    issued = session_path(run_id, session, 'review.json')
    identity = session_path(run_id, session, 'identity.json')
    if (not issued.exists() or not identity.exists()
            or p.read_json(identity) != {'role':'reviewer', 'session':session}
            or p.read_json(issued) != report):
        raise ValueError('Unissued public review feedback')
    return {key:report[key] for key in ('status', 'findings', 'versions', 'notes')}

def dispatch(run_id, role, session, request):
    if not re.fullmatch('[a-f0-9]{32}', str(run_id)) or not re.fullmatch('[A-Za-z0-9_-]{1,96}', str(session)) or role not in ROLE_OPS:
        raise ValueError("Denied identity")
    fields(request, {'op', 'args'})
    op, args = request['op'], request['args']
    if not isinstance(op, str) or op not in ROLE_OPS[role]:
        raise ValueError("Denied operation")
    fields(args, *SCHEMAS[op])
    if 'version' in args and args['version'] not in VERSIONS:
        raise ValueError("Invalid version")
    if 'audience' in args and args['audience'] not in AUDIENCES:
        raise ValueError("Invalid audience")
    with p.run_scope(run_id):
        lock = p.frozen_sources(run_id)
        if lock.get('repository') != 'pubky/paykit-rs' or p.read_json(p.CONFIG).get('canonicalRepository') != 'pubky/paykit-rs':
            raise ValueError("Unapproved public repository")
        bind_session(run_id, role, session)
        if op.startswith('draft_'):
            return draft_operation(run_id, session, lock, op, args)
        if op == 'bootstrap':
            return {'protocolVersion':1, 'runId':run_id, 'role':role, 'sources':lock, 'inputPaths':public_paths(), 'versions':list(VERSIONS), 'repairFeedback':public_review_feedback(run_id), 'deltaPlan':p.plan_changes(lock), 'requiredPolicyPaths':list(REQUIRED_POLICY_PATHS)}
        if op == 'read_inputs':
            if args['path'] not in public_paths():
                raise ValueError("Denied input")
            return paginate(safe_read(p.ROOT, args['path']).decode('utf-8'), args)
        if op == 'source_inventory':
            items = sorted(source_tree(lock, args['version']))
            offset, limit = args.get('offset', 0), args.get('limit', 100)
            if type(offset) is not int or type(limit) is not int or not 0 <= offset <= len(items) or not 1 <= limit <= 100:
                raise ValueError("Invalid pagination")
            end = min(offset + limit, len(items))
            return {'items':items[offset:end], 'offset':offset, 'nextOffset':end if end < len(items) else None, 'total':len(items)}
        if op == 'read_source':
            return {**paginate(source_bytes(lock, args['version'], args['path']), args), 'commit':lock[args['version']]['commit']}
        if op == 'read_blueprint':
            return paginate(safe_read(p.ROOT, f"automation/blueprints/{args['version']}.json").decode('utf-8'), args)
        if op == 'submit_blueprint':
            with draft_lock(run_id):
                validate_public_blueprint(args['blueprint'], lock, args['version'])
                path = target(p.ROOT, f"automation/blueprints/{args['version']}.json")
                if role == 'diagram':
                    old = json.loads(safe_read(p.ROOT, str(path.relative_to(p.ROOT))))
                    if {k:v for k,v in old.items() if k != 'diagrams'} != {k:v for k,v in args['blueprint'].items() if k != 'diagrams'}:
                        raise ValueError("Diagram role cannot change prose or evidence")
                p.save_json(path, args['blueprint'])
                return {'saved':True, 'sha256':p.digest(path.read_bytes())}
        if op in ('get_review_inputs', 'artifact_hash'):
            result = {'artifactHash':p.artifact_digest()}
            if op == 'get_review_inputs':
                result.update(versions=list(VERSIONS), inputPaths=public_paths())
            return result
        if op == 'submit_review':
            report = args['report']
            fields(report, {'status','artifactHash','findings','versions','notes'})
            if report['status'] not in ('pass','fail') or report['versions'] != list(VERSIONS) or type(report['findings']) is not list:
                raise ValueError("Invalid review")
            text_value(report['notes'])
            for finding in report['findings']:
                if isinstance(finding, str):
                    text_value(finding)
                else:
                    fields(finding, {'evidenceId','message'}, {'version','severity'})
                    for value in finding.values():
                        text_value(value)
            if report['status'] == 'pass' and report['findings']:
                raise ValueError("Passing review has findings")
            current = p.artifact_digest()
            if report['artifactHash'] != current:
                raise ValueError("Review artifact differs")
            saved = {**report, 'runId':run_id, 'reviewerRunId':session}
            p.save_json(target(p.CACHE, 'review.json'), saved)
            p.save_json(session_path(run_id, session, 'review.json'), saved)
            if p.artifact_digest() != current:
                raise ValueError("Review artifact changed")
            return {'saved':True}
        if op == 'walkthrough_prepare':
            return prepare_walkthrough(run_id, session, lock, args)
        if op == 'walkthrough_run':
            return p.execute_stage(run_id, 'walkthrough-run', lambda: run_walkthrough(run_id, session, args))
        if op == 'submit_walkthrough':
            return submit_walkthrough(run_id, session, args)
        raise ValueError("Operation not implemented")


@p.contextlib.contextmanager
def draft_lock(run_id):
    # Separate from lease-state: validators launch guarded git subprocesses.
    import fcntl
    path = target(p.CACHE, f'runs/{run_id}/bridge/drafts.lock')
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a') as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


@p.contextlib.contextmanager
def source_cache():
    # Scope to this request, never reuse source bytes across frozen snapshots.
    original = p.source_text
    cache = {}
    def cached(commit, path):
        key = (commit, path)
        if key not in cache:
            cache[key] = original(commit, path)
        return cache[key]
    p.source_text = cached
    try:
        yield
    finally:
        p.source_text = original


def stale_references(state):
    bp = state['blueprint']
    commits = {state['baselineCommit'],state['priorCommit']} - {bp['sourceCommit']}
    tokens = sorted({commit[:size] for commit in commits for size in (40,12,7)}, key=len, reverse=True)
    if not tokens:
        return []
    pattern = re.compile(r'(?<![a-f0-9])(?:'+'|'.join(map(re.escape,tokens))+r')(?![a-f0-9])', re.I)
    result = []
    def inspect(value, selector):
        # Presentation and claims only; historical canonical excerpts are excluded.
        if pattern.search(json.dumps(value, ensure_ascii=False)):
            result.append({'selector':selector, 'reason':'Stale current-snapshot commit locator; explicit refresh required'})
    for page in bp['pages']:
        inspect({k:v for k,v in page.items() if k not in ('sections','sourceLinks')}, {'kind':'sourceLinks','pageSlug':page['slug']})
        inspect(page.get('sourceLinks',[]), {'kind':'sourceLinks','pageSlug':page['slug']})
        for section in page['sections']:
            inspect(section, {'kind':'section','pageSlug':page['slug'],'sectionId':section['id']})
    for record in bp['evidence']:
        inspect(record['claim'], {'kind':'evidence','evidenceId':record['id']})
    for diagram in bp['diagrams']:
        inspect(diagram, {'kind':'diagram','diagramId':diagram['id']})
    return result


def draft_summary(state):
    bp = state['blueprint']
    selectors = []
    for page in bp['pages']:
        selectors.extend({'kind':'section', 'pageSlug':page['slug'], 'sectionId':s['id']} for s in page['sections'])
        selectors.append({'kind':'sourceLinks', 'pageSlug':page['slug']})
    selectors.extend({'kind':'evidence', 'evidenceId':e['id']} for e in bp['evidence'])
    selectors.extend({'kind':'diagram', 'diagramId':d['id']} for d in bp['diagrams'])
    dependencies = []
    for eid in state['unresolvedEvidenceIds']:
        dependencies.append({'evidenceId':eid,
            'sections':[{'kind':'section','pageSlug':page['slug'],'sectionId':section['id']}
                        for page in bp['pages'] for section in page['sections'] if eid in section['evidence']],
            'diagrams':[diagram['id'] for diagram in bp['diagrams'] if any(eid in relation['evidence'] for relation in diagram['relationships'])]})
    return {'baselineCommit':state['baselineCommit'], 'sourceCommit':bp['sourceCommit'],
            'dependencyChanges':dependencies,
            'baselineSha256':state['baselineSha256'], 'deltaPlan':state['deltaPlan'],
            'unresolvedEvidenceIds':state['unresolvedEvidenceIds'], 'staleReferences':stale_references(state),
            'selectors':selectors}


def draft_selection(state, selector):
    fields(selector, {'kind'}, {'pageSlug','sectionId','evidenceId','diagramId'})
    kind = selector['kind']
    if kind == 'summary':
        fields(selector, {'kind'})
        return draft_summary(state)
    if kind in ('section', 'sourceLinks'):
        fields(selector, {'kind','pageSlug'} | ({'sectionId'} if kind == 'section' else set()))
        page = next((page for page in state['blueprint']['pages'] if page['slug'] == selector['pageSlug']), None)
        if page is not None:
            if kind == 'sourceLinks':
                return page.get('sourceLinks', [])
            return unique_item(page['sections'], selector['sectionId'])
    elif kind in ('evidence','diagram'):
        key = 'evidenceId' if kind == 'evidence' else 'diagramId'
        fields(selector, {'kind',key})
        return unique_item(state['blueprint']['evidence' if kind == 'evidence' else 'diagrams'], selector[key])
    raise ValueError('Unknown draft selector')


def unique_item(items, identity):
    found = [item for item in items if item['id'] == identity]
    if len(found) != 1:
        raise ValueError('Unknown or ambiguous draft identity')
    return found[0]


def draft_guard(run_id, state):
    # Called under lease-state lock: no git/tool subprocesses here.
    record = p.guard_run(run_id)
    frozen = p.read_json(target(p.CACHE, f'runs/{run_id}/sources.json'))
    if p.digest(frozen) != state['sourceHash'] or record.get('sourceHash') != state['sourceHash']:
        raise ValueError('Frozen draft inputs changed')
    if p.digest(safe_read(p.ROOT, 'automation/sources.lock.json')) != state['previousLockSha256']:
        raise ValueError('Approved baseline snapshot changed')
    return frozen


def draft_operation(run_id, session, lock, op, args):
    version = args['version']
    name = f'automation/blueprints/{version}.json'
    path = session_path(run_id, session, f'draft-{version}.json')
    with draft_lock(run_id), source_cache():
        if not path.exists():
            if op != 'draft_open':
                raise ValueError('Draft is not open')
            raw = safe_read(p.ROOT, name)
            bp = json.loads(raw)
            previous = json.loads(safe_read(p.ROOT, 'automation/sources.lock.json'))
            if previous.get('repository') != 'pubky/paykit-rs':
                raise ValueError('Unapproved baseline snapshot')
            baseline_lock = lock if bp.get('sourceCommit') == lock[version]['commit'] else previous
            if bp.get('sourceCommit') != baseline_lock[version]['commit']:
                raise ValueError('Unapproved baseline commit')
            validate_public_blueprint(bp, baseline_lock, version)
            baseline_commit = bp['sourceCommit']
            unresolved = []
            tree = source_tree(lock, version)
            file_matches = {}
            for record in bp['evidence']:
                source_path = record['path']
                if source_path not in file_matches:
                    # Git blob identity includes raw newlines; source_text normalizes them.
                    blobs = [p.run(['git', 'rev-parse', '--verify', commit + ':' + source_path], cwd=p.REPO)
                             for commit in (baseline_commit, lock[version]['commit'])] if source_path in tree else []
                    if any(re.fullmatch('[a-f0-9]{40}', blob) is None for blob in blobs):
                        raise ValueError('Invalid source blob identity')
                    file_matches[source_path] = bool(blobs) and blobs[0] == blobs[1]
                if file_matches[source_path]:
                    record['commit'] = lock[version]['commit']
                else:
                    unresolved.append(record['id'])
            bp['sourceCommit'] = lock[version]['commit']
            state = {'version':version, 'blueprint':bp, 'baselineCommit':baseline_commit,
                     'priorCommit':previous[version]['commit'],
                     'previousLockSha256':p.digest(safe_read(p.ROOT, 'automation/sources.lock.json')),
                     'baselineSha256':p.digest(raw), 'sourceHash':p.digest(lock),
                     'deltaPlan':p.plan_changes(lock)[version], 'unresolvedEvidenceIds':unresolved,
                     'revision':p.digest({'blueprint':bp,'run':run_id,'session':session})}
            with p.state_lock():
                draft_guard(run_id, state)
                if p.digest(safe_read(p.ROOT, name)) != state['baselineSha256']:
                    raise ValueError('Baseline changed')
                p.save_json(path, state)
        state = p.read_json(path)
        current = p.digest(safe_read(p.ROOT, name))
        allowed = {state['baselineSha256']}
        if state.get('intent'):
            allowed.add(state['intent']['sha256'])
        if state.get('receipt'):
            allowed = {state['receipt']['sha256']}
        if state['sourceHash'] != p.digest(lock) or current not in allowed:
            raise ValueError('Draft inputs changed')
        if op == 'draft_open':
            return {'version':version,'revision':state['revision'],'summary':draft_summary(state)}
        revision = args['revision' if op == 'draft_read' else 'expectedRevision']
        if not isinstance(revision, str) or not re.fullmatch('[a-f0-9]{64}', revision) or revision != state['revision']:
            raise ValueError('Stale draft revision')
        if op == 'draft_read':
            text = json.dumps(draft_selection(state, args['selector']), ensure_ascii=False, separators=(',',':'))
            return {'version':version,'revision':revision, **paginate(text, args)}
        if op == 'draft_edit':
            import copy
            if state.get('intent') or state.get('receipt'):
                raise ValueError('Draft is closed')
            edits = args['edits']
            if type(edits) is not list or not 1 <= len(edits) <= 16 or len(json.dumps(edits, ensure_ascii=False, allow_nan=False, separators=(',',':')).encode()) > 32768:
                raise ValueError('Invalid edit batch')
            candidate = copy.deepcopy(state)
            bp = candidate['blueprint']
            for edit in edits:
                fields(edit, {'op'}, {'pageSlug','sectionId','section','record','sourceLinks','diagramId','diagram'})
                operation = edit['op']
                if operation == 'replaceSection':
                    fields(edit, {'op','pageSlug','sectionId','section'})
                    page = next((page for page in bp['pages'] if page['slug'] == edit['pageSlug']), None)
                    if page is None:
                        raise ValueError('Unknown page')
                    previous = unique_item(page['sections'], edit['sectionId'])
                    if type(edit['section']) is not dict or edit['section'].get('id') != previous['id']:
                        raise ValueError('Section identity differs')
                    page['sections'][page['sections'].index(previous)] = edit['section']
                    validate_public_shape({'sourceCommit':bp['sourceCommit'],'pages':[page],'evidence':[],'diagrams':[]}, lock, version)
                elif operation == 'putEvidence':
                    fields(edit, {'op','record'})
                    record = edit['record']
                    validate_public_shape({'sourceCommit':bp['sourceCommit'],'pages':[],'evidence':[record],'diagrams':[]}, lock, version)
                    p.validate_evidence([record], lock, version)
                    found = [i for i,e in enumerate(bp['evidence']) if e['id'] == record['id']]
                    if len(found) > 1:
                        raise ValueError('Ambiguous evidence identity')
                    if found:
                        bp['evidence'][found[0]] = record
                    else:
                        bp['evidence'].append(record)
                    candidate['unresolvedEvidenceIds'] = [eid for eid in candidate['unresolvedEvidenceIds'] if eid != record['id']]
                elif operation == 'replaceSourceLinks':
                    fields(edit, {'op','pageSlug','sourceLinks'})
                    page = next((page for page in bp['pages'] if page['slug'] == edit['pageSlug']), None)
                    if page is None or type(edit['sourceLinks']) is not list:
                        raise ValueError('Unknown page or invalid links')
                    for link in edit['sourceLinks']:
                        link_value(link)
                        if not p.approved_source(link['href']):
                            raise ValueError('Unapproved public source link')
                    page['sourceLinks'] = edit['sourceLinks']
                elif operation == 'replaceDiagram':
                    fields(edit, {'op','diagramId','diagram'})
                    previous = unique_item(bp['diagrams'], edit['diagramId'])
                    if type(edit['diagram']) is not dict or edit['diagram'].get('id') != previous['id']:
                        raise ValueError('Diagram identity differs')
                    validate_public_shape({'sourceCommit':bp['sourceCommit'],'pages':[],'evidence':[],'diagrams':[edit['diagram']]}, lock, version)
                    bp['diagrams'][bp['diagrams'].index(previous)] = edit['diagram']
                else:
                    raise ValueError('Unknown edit')
            candidate['revision'] = p.digest({'previous':state['revision'],'blueprint':bp})
            with p.state_lock():
                if p.digest(draft_guard(run_id, state)) != state['sourceHash'] or p.digest(safe_read(p.ROOT, name)) != state['baselineSha256']:
                    raise ValueError('Draft inputs changed')
                p.save_json(path, candidate)
            return {'version':version,'revision':candidate['revision'],'summary':draft_summary(candidate)}
        if op == 'draft_finalize':
            if state.get('receipt'):
                return state['receipt']
            if state['unresolvedEvidenceIds'] or stale_references(state):
                raise ValueError('Unresolved draft evidence or stale references')
            validate_public_blueprint(state['blueprint'], lock, version)
            baseline = json.loads(safe_read(p.ROOT, name))
            unchanged = state['blueprint'] == baseline and current == state['baselineSha256']
            data = safe_read(p.ROOT, name) if unchanged else (json.dumps(state['blueprint'], indent=2, ensure_ascii=False)+'\n').encode()
            result = {'version':version,'revision':revision,'saved':True,'sha256':p.digest(data),'unchanged':unchanged}
            with p.state_lock():
                if p.digest(draft_guard(run_id, state)) != state['sourceHash'] or p.digest(safe_read(p.ROOT, name)) not in allowed:
                    raise ValueError('Draft inputs changed')
                state['intent'] = result
                p.save_json(path, state)
            if not unchanged:
                p.write_artifact(target(p.ROOT, name), data)
            if p.digest(safe_read(p.ROOT, name)) != result['sha256']:
                raise ValueError('Finalized bytes differ')
            with p.state_lock():
                draft_guard(run_id, state)
                state['receipt'] = result
                p.save_json(path, state)
            return result
        raise ValueError('Unsupported draft operation')


def text_value(value, limit=100000):
    if not isinstance(value, str) or not value.strip() or len(value) > limit or '\0' in value:
        raise ValueError("Invalid text")


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch('[a-z0-9]+(?:-[a-z0-9]+)*', value):
        raise ValueError("Invalid identifier")


def string_list(value):
    if type(value) is not list:
        raise ValueError("Invalid list")
    for item in value:
        text_value(item)


def link_value(link):
    fields(link, {'href','label'})
    text_value(link['href'], 2048)
    text_value(link['label'])
    href = link['href']
    if not (href.startswith('/docs/') or href.startswith('/docs-examples/') or p.approved_source(href)) or '..' in href or any(c in href for c in ('\\', '\n', '\r')):
        raise ValueError("Unapproved link")


def validate_public_blueprint(bp, lock, version):
    validate_public_shape(bp, lock, version)
    p.validate_blueprint(bp, lock, version)


def validate_public_shape(bp, lock, version):
    fields(bp, {'sourceCommit','pages','evidence','diagrams'})
    if bp['sourceCommit'] != lock[version]['commit'] or any(type(bp[key]) is not list for key in ('pages','evidence','diagrams')):
        raise ValueError("Invalid blueprint")
    tree = source_tree(lock, version)
    for record in bp['evidence']:
        fields(record, {'id','version','commit','path','startLine','endLine','excerpt','sourceHash','kind','claim'}, {'anchor'})
        identifier(record['id'])
        text_value(record['claim'])
        canonical_path(record['path'])
        if record['path'] not in tree or record['commit'] != lock[version]['commit'] or record['version'] != version:
            raise ValueError("Unapproved evidence")
        if not isinstance(record['sourceHash'], str) or not re.fullmatch('[a-f0-9]{64}', record['sourceHash']) or type(record['startLine']) is not int or type(record['endLine']) is not int:
            raise ValueError("Invalid evidence")
        text_value(record['excerpt'])
        if 'anchor' in record:
            fields(record['anchor'], {'start','end'})
            text_value(record['anchor']['start'])
            text_value(record['anchor']['end'])
    for page in bp['pages']:
        fields(page, {'slug','title','description','group','kind','sections'}, {'sourceLinks'})
        identifier(page['slug'])
        for key in ('title','description','group','kind'):
            text_value(page[key])
        if type(page['sections']) is not list or type(page.get('sourceLinks', [])) is not list:
            raise ValueError("Invalid sections")
        for link in page.get('sourceLinks', []):
            link_value(link)
        for section in page['sections']:
            fields(section, {'id','title','evidence'}, {'paragraphs','list','code','links','diagram'})
            identifier(section['id'])
            text_value(section['title'])
            string_list(section['evidence'])
            for key in ('paragraphs','list'):
                if key in section:
                    string_list(section[key])
            if type(section.get('code', [])) is not list or type(section.get('links', [])) is not list:
                raise ValueError("Invalid section")
            for block in section.get('code', []):
                fields(block, {'language','caption'}, {'artifact','code'})
                if ('artifact' in block) == ('code' in block):
                    raise ValueError("Invalid code block")
                text_value(block['language'], 40)
                text_value(block['caption'])
                if 'artifact' in block:
                    artifact = block['artifact']
                    if not isinstance(artifact, str) or not re.fullmatch(r'(wallet|point-of-sale)/(Cargo\.toml|Cargo\.lock|rust-toolchain\.toml|src/main\.rs)', artifact):
                        raise ValueError("Denied artifact")
                    target(p.ROOT, f'public/docs-examples/{version}/{artifact}')
                else:
                    text_value(block['code'])
            for link in section.get('links', []):
                link_value(link)
            if 'diagram' in section:
                fields(section['diagram'], {'id','caption','alt'})
                identifier(section['diagram']['id'])
                text_value(section['diagram']['caption'])
                text_value(section['diagram']['alt'])
    diagram_ids = set()
    for diagram in bp['diagrams']:
        fields(diagram, {'id','mermaid','caption','alt','relationships'})
        identifier(diagram['id'])
        if diagram['id'] in diagram_ids:
            raise ValueError("Duplicate diagram")
        diagram_ids.add(diagram['id'])
        for key in ('mermaid','caption','alt'):
            text_value(diagram[key])
        if re.search(r'<|https?:|javascript:|data:|%%\{|\bclick\b|\bcallback\b', diagram['mermaid'], re.I) or type(diagram['relationships']) is not list:
            raise ValueError("Unsafe diagram")
        for relation in diagram['relationships']:
            fields(relation, {'text','evidence'})
            text_value(relation['text'])
            string_list(relation['evidence'])


def parse_guide(html, version):
    """Read server-rendered main content, never scripts or Next flight payloads."""
    from html.parser import HTMLParser
    class MainReader(HTMLParser):
        def __init__(self):
            super().__init__(convert_charrefs=True)
            self.main = False
            self.count = 0
            self.version = None
            self.skip = 0
            self.pre = False
            self.code = None
            self.codes, self.links, self.text = [], [], []

        def handle_starttag(self, tag, attrs):
            attrs = dict(attrs)
            if tag == 'main':
                self.main = True
                self.count += 1
                self.version = attrs.get('data-doc-version')
            if not self.main:
                return
            if tag in ('script','style'):
                self.skip += 1
            if self.skip:
                return
            if tag in ('h1','h2','h3','p','li','pre','section','figcaption'):
                self.text.append('\n')
            if tag == 'pre':
                self.pre = True
            if tag == 'code' and self.pre:
                self.code = []
            if tag == 'a' and 'href' in attrs:
                self.links.append(attrs['href'])

        def handle_endtag(self, tag):
            if not self.main:
                return
            if tag in ('script','style') and self.skip:
                self.skip -= 1
                return
            if self.skip:
                return
            if tag == 'code' and self.code is not None:
                self.codes.append(''.join(self.code))
                self.code = None
            if tag == 'pre':
                self.pre = False
            if tag in ('h1','h2','h3','p','li','pre','section','figcaption'):
                self.text.append('\n')
            if tag == 'main':
                self.main = False

        def handle_data(self, data):
            if self.main and not self.skip:
                self.text.append(data)
                if self.code is not None:
                    self.code.append(data)
    parser = MainReader()
    parser.feed(html)
    if parser.count != 1 or parser.version != version or parser.main:
        raise ValueError('Rendered guide mismatch')
    return {'text':''.join(parser.text).strip(), 'codes':parser.codes, 'links':parser.links}


def validate_commands(commands, guide, version, audience):
    expected = [f'tar -xzf paykit-{audience}-{version}.tar.gz', f'cd paykit-{audience}-demo', 'cargo run --locked']
    if commands != expected or not any(block.splitlines() == commands for block in guide['codes']):
        raise ValueError('Commands must exactly follow the rendered guide')


def check_archive(data, version, audience):
    import io
    import tarfile
    if len(data) > 5242880:
        raise ValueError('Archive too large')
    expected = {'Cargo.toml','Cargo.lock','rust-toolchain.toml','src/main.rs'}
    found = set()
    total = 0
    try:
        with tarfile.open(fileobj=io.BytesIO(data), mode='r:gz') as archive:
            for member in archive:
                prefix = f'paykit-{audience}-demo/'
                if not member.name.startswith(prefix) or not member.isfile() or member.size > 2097152:
                    raise ValueError('Unsafe archive')
                name = member.name[len(prefix):]
                if name not in expected or name in found:
                    raise ValueError('Unsafe archive')
                total += member.size
                if total > 5242880:
                    raise ValueError('Archive too large')
                content = archive.extractfile(member).read()
                if content != safe_read(p.ROOT, f'public/docs-examples/{version}/{audience}/{name}'):
                    raise ValueError('Archive bytes differ')
                found.add(name)
    except tarfile.TarError as error:
        raise ValueError('Unsafe archive') from error
    if found != expected:
        raise ValueError('Incomplete archive')


def public_environment(home=None):
    """Never forward provider/GitHub/Talos credentials to website or cargo."""
    import os
    env = {'PATH':'/usr/local/bin:/usr/bin:/bin', 'LANG':'C.UTF-8', 'LC_ALL':'C.UTF-8', 'NEXT_TELEMETRY_DISABLED':'1'}
    if home is not None:
        env.update(HOME=str(home), CARGO_HOME=str(home / '.cargo'), RUSTUP_HOME=str(Path.home()/'.rustup'),
                   CARGO_BUILD_JOBS='2', CARGO_TARGET_DIR=str(p.CACHE/'cargo-target'))
        env['PATH'] = str(Path.home()/'.cargo/bin') + ':' + env['PATH']
    return env


def owned_listener(parent, port):
    """Require the listener inode to belong to this fresh descendant tree."""
    import os
    entries = {}
    for stat in Path('/proc').glob('[0-9]*/stat'):
        try:
            entries[int(stat.parent.name)] = int(stat.read_text().rsplit(')',1)[1].split()[1])
        except (FileNotFoundError, ProcessLookupError):
            pass
    owned = {parent}
    while True:
        children = {pid for pid,ppid in entries.items() if ppid in owned}
        if children <= owned:
            break
        owned.update(children)
    listeners = set()
    for line in Path('/proc/net/tcp').read_text().splitlines()[1:]:
        row = line.split()
        if row[1] == f'0100007F:{port:04X}' and row[3] == '0A':
            listeners.add(row[9])
    for pid in owned:
        try:
            if Path(f'/proc/{pid}/cwd').resolve() != p.ROOT.resolve():
                continue
            for fd in Path(f'/proc/{pid}/fd').iterdir():
                try:
                    if os.readlink(fd) in {'socket:[' + inode + ']' for inode in listeners}:
                        return True
                except (FileNotFoundError, ProcessLookupError):
                    pass
        except (FileNotFoundError, ProcessLookupError):
            pass
    return False


@p.contextlib.contextmanager
def heavy_scope():
    import fcntl
    import time
    path = target(p.CACHE, 'bridge-heavy.lock')
    with path.open('a') as handle:
        while True:
            p.check_effect()
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                time.sleep(.05)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


@p.contextlib.contextmanager
def production_server(run_id):
    import shutil
    import socket
    import subprocess
    import time
    # Fail on an occupied port rather than attaching to an old/unrelated server.
    with socket.socket() as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        probe.bind(('127.0.0.1',3174))
    if not (p.ROOT/'.next/BUILD_ID').is_file():
        raise ValueError('Production build required')
    node = shutil.which('node', path=public_environment()['PATH'])
    if not node:
        raise ValueError('Production runtime unavailable')
    proc = None
    try:
        with p.state_lock():
            record = p.guard_run(run_id)
            budget = min(120, record['deadline'] - time.time())
            if budget <= 0:
                raise ValueError('Run expired')
            proc = subprocess.Popen([sys.executable, '-c', p.WORKER_PROGRAM,
                json.dumps([node,'node_modules/next/dist/bin/next','start','--hostname','127.0.0.1','--port','3174']),
                str(time.time()+budget)], cwd=p.ROOT, env=public_environment(), stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, start_new_session=True)
            record.setdefault('children', []).append({'pid':proc.pid,'start':p.process_identity(proc.pid)})
            p.save_json(target(p.CACHE,'run.lock/owner.json'),record)
        until = time.monotonic() + min(30,budget)
        while time.monotonic() < until:
            p.guard_run(run_id)
            if proc.poll() is not None:
                raise ValueError('Fresh production server failed')
            if owned_listener(proc.pid,3174):
                break
            time.sleep(.05)
        else:
            raise ValueError('Fresh production server unavailable')
        yield 'http://127.0.0.1:3174'
    finally:
        if proc is not None:
            if p.group_alive(proc.pid):
                p.stop_group(proc.pid)
            proc.wait(timeout=3)
            with p.state_lock():
                owner = target(p.CACHE,'run.lock/owner.json')
                if owner.exists():
                    record = p.read_json(owner)
                    if record['id'] == run_id:
                        record['children'] = [c for c in record.get('children',[]) if c['pid'] != proc.pid]
                        p.save_json(owner,record)


def local_fetch(url, max_bytes=5242880):
    import urllib.request
    from urllib.parse import urlparse
    info = urlparse(url)
    if info.scheme != 'http' or info.netloc != '127.0.0.1:3174' or info.query or info.fragment or '..' in info.path:
        raise ValueError('Denied local URL')
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            return None
    record = p.check_effect()
    timeout = min(10,max(.01,record['deadline']-p.time.time()))
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    with opener.open(url, timeout=timeout) as response:
        if response.status != 200 or response.url != url:
            raise ValueError('Local response mismatch')
        data = response.read(max_bytes+1)
    if len(data) > max_bytes:
        raise ValueError('Local response too large')
    p.check_effect()
    return data


def walkthrough_state(run_id, session, args):
    path = session_path(run_id,session,'prepared.json')
    if not path.is_file():
        raise ValueError('Fresh rendered guide preparation required')
    state = p.read_json(path)
    if (state['version'],state['audience']) != (args['version'],args['audience']) or state['artifactHash'] != p.artifact_digest():
        raise ValueError('Walkthrough preparation is stale or belongs to another pair')
    return state


def prepare_walkthrough(run_id, session, lock, args):
    version, audience = args['version'], args['audience']
    # Validate page arguments before creating a server or touching captures.
    offset, limit = args.get('offset',0), args.get('limit',6000)
    if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 6000:
        raise ValueError('Invalid pagination')
    pair_path = session_path(run_id,session,'pair.json')
    pair = {'version':version,'audience':audience}
    if pair_path.exists() and p.read_json(pair_path) != pair:
        raise ValueError('A fresh walker session is required for each guide')
    p.save_json(pair_path,pair)
    path = session_path(run_id,session,'prepared.json')
    if not path.exists():
        def capture():
            with heavy_scope():
                initial = p.artifact_digest()
                slug = 'wallets' if audience == 'wallet' else 'point-of-sale'
                archive_name = f'paykit-{audience}-{version}.tar.gz'
                archive_href = f'/docs-examples/{version}/{archive_name}'
                with production_server(run_id) as origin:
                    guide_url = origin + f'/docs/{version}/{slug}'
                    html = local_fetch(guide_url, max_bytes=2097152).decode('utf-8')
                    guide = parse_guide(html,version)
                    if lock[version]['commit'] not in guide['text'] or archive_href not in guide['links']:
                        raise ValueError('Rendered guide source or download mismatch')
                    expected = p.EXPECTED[audience]
                    if expected not in guide['codes']:
                        raise ValueError('Rendered promised output is missing or differs')
                    document = json.loads(safe_read(p.ROOT,f'content/docs/{version}.json'))
                    page = next(item for item in document['pages'] if item['slug'] == slug)
                    normalized = ' '.join(guide['text'].split())
                    required = [page['title'],page['description']]
                    for section in page['sections']:
                        required.extend([section['title'],*section.get('paragraphs',[]),*section.get('list',[])])
                        required.extend(block['code'] for block in section.get('code',[]))
                    if any(' '.join(value.replace('`','').split()) not in normalized for value in required):
                        raise ValueError('Production build guide is stale')
                    archive = local_fetch(origin+archive_href)
                    local_archive = safe_read(p.ROOT,f'public/docs-examples/{version}/{archive_name}',max_bytes=5242880)
                    if archive != local_archive:
                        raise ValueError('Downloaded archive does not match artifacts')
                    check_archive(archive,version,audience)
                # No Next server remains while the fresh agent plans commands.
                if initial != p.artifact_digest():
                    raise ValueError('Artifacts changed during rendered capture')
                p.write_artifact(session_path(run_id,session,'download.tar.gz'),archive)
                p.write_artifact(session_path(run_id,session,'guide.html'),html)
                state = {**pair,'runId':run_id,'reporterRunId':session,'artifactHash':initial,
                         'guideUrl':guide_url,'archiveHash':p.digest(archive),'guide':guide,'expected':expected}
                p.save_json(path,state)
        p.execute_stage(run_id,'walkthrough-prepare',capture)
    state = walkthrough_state(run_id,session,args)
    return {'guideUrl':state['guideUrl'],'archiveHash':state['archiveHash'],'artifactHash':state['artifactHash'],
            'guide':paginate(state['guide']['text'],args)}


def run_walkthrough(run_id, session, args):
    import os
    version, audience = args['version'], args['audience']
    state = walkthrough_state(run_id,session,args)
    validate_commands(args['commands'],state['guide'],version,audience)
    attempted = session_path(run_id,session,'attempted.json')
    if attempted.exists():
        raise ValueError('Fresh empty walkthrough execution already attempted')
    with heavy_scope():
        p.save_json(attempted,{'attempted':True})
        root = session_path(run_id,session,'execution')
        root.mkdir(exist_ok=False)
        if any(root.iterdir()):
            raise ValueError('Walkthrough directory is not empty')
        archive = session_path(run_id,session,'download.tar.gz').read_bytes()
        if p.digest(archive) != state['archiveHash']:
            raise ValueError('Prepared archive changed')
        check_archive(archive,version,audience)
        archive_name = f'paykit-{audience}-{version}.tar.gz'
        p.write_artifact(target(root,archive_name),archive)
        home = session_path(run_id,session,'tool-home')
        home.mkdir(exist_ok=False)
        env = public_environment(home)
        steps = []
        extract = p.bounded_process(['tar','-xzf',archive_name],cwd=root,env=env,timeout=30)
        steps.append({'command':args['commands'][0],'exitCode':extract.returncode})
        if extract.returncode:
            raise ValueError('Literal archive extraction failed')
        cwd = target(root,f'paykit-{audience}-demo')
        if not cwd.is_dir():
            raise ValueError('Literal guide directory is missing')
        # cd is interpreted as a single directory change, never a shell program.
        steps.append({'command':args['commands'][1],'exitCode':0})
        before = p.digest((cwd/'Cargo.lock').read_bytes())
        cargo = str(Path.home()/'.cargo/bin/cargo')
        result = p.bounded_process([cargo,'run','--locked'],cwd=cwd,env=env,timeout=1200)
        after = p.digest((cwd/'Cargo.lock').read_bytes())
        p.write_artifact(session_path(run_id,session,'cargo.stdout'),result.stdout)
        p.write_artifact(session_path(run_id,session,'cargo.stderr'),result.stderr)
        steps.append({'command':args['commands'][2],'exitCode':result.returncode})
        report = {'version':version,'audience':audience,'runId':run_id,'reporterRunId':session,
            'artifactHash':state['artifactHash'],'guideUrl':state['guideUrl'],'commands':args['commands'],
            'exitCode':result.returncode,'stdout':result.stdout.strip(),'archiveHash':state['archiveHash'],
            'limitations':['Local fixture mechanics only; no discovery network, authenticated session, payment, liquidity or settlement validation.',
                'Fresh extracted project and credential-free tool home; existing Rust toolchains and ignored Cargo target cache reused.'],
            'logs':{'initialDirectoryEmpty':True,'lockHashBefore':before,'lockHashAfter':after,'lockUnchanged':before==after,
                    'expectedStdoutMatched':result.stdout.strip()==state['expected'],'stdoutHash':p.digest(result.stdout.encode()),'steps':steps}}
        p.save_json(session_path(run_id,session,'execution-result.json'),report)
        if result.returncode or result.stdout.strip() != state['expected'] or before != after or p.artifact_digest() != state['artifactHash']:
            raise ValueError('Actual walkthrough execution failed or artifacts changed')
        p.save_json(session_path(run_id,session,'passed.json'),report)
        return report


@p.contextlib.contextmanager
def reports_lock():
    import fcntl
    with target(p.CACHE,'bridge-reports.lock').open('a') as handle:
        fcntl.flock(handle,fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle,fcntl.LOCK_UN)


def submit_walkthrough(run_id, session, args):
    state = walkthrough_state(run_id,session,args)
    passed = session_path(run_id,session,'passed.json')
    if not passed.is_file():
        raise ValueError('No actual successful execution for this session')
    report = p.read_json(passed)
    if report['reporterRunId'] != session or report['runId'] != run_id or report['artifactHash'] != state['artifactHash'] or report['exitCode'] != 0 or report['stdout'] != p.EXPECTED[args['audience']] or not report['logs']['lockUnchanged']:
        raise ValueError('Actual walkthrough report differs')
    archive = safe_read(p.ROOT,f"public/docs-examples/{args['version']}/paykit-{args['audience']}-{args['version']}.tar.gz",max_bytes=5242880)
    if report['archiveHash'] != p.digest(archive):
        raise ValueError('Walkthrough archive changed')
    with reports_lock():
        path = target(p.CACHE,'walkthroughs.json')
        existing = p.read_json(path) if path.exists() else []
        kept = [item for item in existing if item.get('runId') == run_id and item.get('artifactHash') == state['artifactHash'] and (item['version'],item['audience']) != (args['version'],args['audience'])]
        if any(item.get('reporterRunId') == session for item in kept):
            raise ValueError('Walkers must be fresh independent sessions')
        p.save_json(path,kept+[report])
    return {'saved':True}


def main(argv=None):
    import contextlib
    import io
    class QuietParser(argparse.ArgumentParser):
        def error(self, message):
            raise ValueError('Invalid invocation')
    try:
        parser = QuietParser(add_help=False,allow_abbrev=False)
        for name in ('run','role','session','request'):
            parser.add_argument('--'+name,required=True)
        options = parser.parse_args(argv)
        request = decode_message(options.request)
        with contextlib.redirect_stdout(io.StringIO()):
            result = dispatch(options.run,options.role,options.session,request)
        response = {'ok':True,'result':result}
        token = encode_message(response)
        if len(token) > MAX_RESPONSE:
            raise ValueError('Page size exceeded')
    except ValueError:
        token = encode_message({'ok':False,'error':{'code':'denied','message':'Invalid or denied public request'}})
    except Exception:
        token = encode_message({'ok':False,'error':{'code':'failed','message':'Public operation failed; no approval recorded'}})
    print(token)
    return 0


if __name__ == '__main__':
    sys.exit(main())
