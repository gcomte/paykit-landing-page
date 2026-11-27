#!/usr/bin/env python3
"""Bound one shared weekly repair loop; never approves publication on its own."""
import argparse
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import subprocess
import time
from contextlib import contextmanager

VERSIONS = ('released', 'coming-soon')
READERS = ('newcomer', 'editorial', 'security-privacy', 'failure-recovery',
           'usability-accessibility', 'visual-design', 'diagram-review')
READER_PERSPECTIVES = {**{role: role for role in READERS},
                       'diagram-review': 'diagram-accuracy-usefulness'}
MAX_REPAIRS = 2
FINAL_RESERVE_SECONDS = 1800
SHA = re.compile(r'[0-9a-f]{64}')
KEY = re.compile(r'[a-z0-9][a-z0-9.-]{0,95}')


def check(condition, reason):
    if not condition:
        raise ValueError(reason)


def no_duplicates(pairs):
    obj = dict(pairs)
    check(len(obj) == len(pairs), 'duplicate JSON field')
    return obj


def parse(raw):
    return json.loads(raw, object_pairs_hook=no_duplicates)


def sha256(raw):
    return hashlib.sha256(raw).hexdigest()


def is_sha(value):
    return isinstance(value, str) and SHA.fullmatch(value) is not None


def git(root, *args):
    return subprocess.check_output(['git', '-C', str(root), *args], timeout=25,
                                   env={**os.environ, 'GIT_OPTIONAL_LOCKS': '0'})


def snapshot(root):
    """Content identity from the actual candidate checkout, not a timestamped capture."""
    root = root.resolve()
    check(os.fsdecode(git(root, 'rev-parse', '--show-toplevel').strip()) == str(root), 'expected exact checkout root')
    head = git(root, 'rev-parse', 'HEAD').decode().strip()
    changed = git(root, 'diff', '--no-ext-diff', '--name-only', '-z', head, '--')
    extra = git(root, 'ls-files', '--others', '--exclude-standard', '-z')
    items = []
    for name in sorted(set((changed + extra).split(b'\0')) - {b''}):
        relative = os.fsdecode(name)
        path = root / relative
        check(path.resolve() == path, 'candidate path is not a regular checkout path')
        if not path.exists():
            items.append([relative, None, None])
            continue
        check(path.is_file(), 'candidate path is not a regular file')
        with path.open('rb') as source:
            digest = hashlib.file_digest(source, 'sha256').hexdigest()
        items.append([relative, bool(path.stat().st_mode & stat.S_IXUSR), digest])
    policy_edit = any((name.startswith('automation/') and not name.startswith(('automation/blueprints/', 'automation/evidence/')))
                      or name.startswith(('app/', 'components/', 'lib/', 'tests/'))
                      or name in ('docs-generation.json', 'package.json', 'package-lock.json', 'next.config.ts', 'playwright.config.ts')
                      for name, _, _ in items)
    return head, sha256(json.dumps([head, items], separators=(',', ':')).encode()), policy_edit


def expected_pairs(gate, phase, process_review):
    early = ('factual', 'qa', 'process') if process_review or gate['processFilesChanged'] else ('factual', 'qa')
    roles = early if phase == 'early' else early + READERS
    return [(track, role) for track in gate['refreshVersions'] for role in roles]


def read_evidence(binding, work, label):
    check(isinstance(binding, dict) and isinstance(binding.get('path'), str)
          and is_sha(binding.get('sha256')), f'missing {label} binding')
    path = Path(binding['path'])
    check(path.is_absolute() and path.resolve() == path and work in path.parents and path.is_file(),
          f'{label} must be a regular file under the run workspace')
    raw = path.read_bytes()
    check(sha256(raw) == binding['sha256'], f'stale {label} bytes')
    return raw


def bound_captures(gate, packet, work, phase):
    if phase == 'early':
        return {}
    captures = packet.get('captures')
    if not isinstance(captures, dict) or set(captures) != set(gate['refreshVersions']):
        raise ValueError('missing or mixed reader capture scope')
    result = {}
    for track, binding in captures.items():
        check(isinstance(binding, dict) and set(binding) == {'reader', 'visual', 'diagram'}, 'missing rendered capture chain')
        reader = parse(read_evidence(binding['reader'], work, 'reader capture'))
        check(isinstance(reader, dict) and reader.get('kind') == 'rendered-public-reader-input'
              and reader.get('candidateSha256') == packet['candidate']
              and reader.get('version') == track and reader.get('versionMetadata', {}).get('commit') == gate['selections'][track]['sourceCommit'],
              'capture is not for the exact candidate, selected track and source')
        visual = parse(read_evidence(binding['visual'], work, 'visual capture'))
        diagram = parse(read_evidence(binding['diagram'], work, 'diagram capture'))
        check(isinstance(visual, dict) and visual.get('kind') == 'rendered-public-visual-input'
              and visual.get('inputManifestSha256') == binding['reader']['sha256'], 'visual capture is not bound to the reader index')
        check(isinstance(diagram, dict) and diagram.get('kind') == 'rendered-public-diagram-input'
              and diagram.get('inputManifestSha256') == binding['reader']['sha256']
              and diagram.get('visualManifestSha256') == binding['visual']['sha256']
              and diagram.get('sourceCommit') == gate['selections'][track]['sourceCommit'], 'diagram capture is not bound to the reader and visual indexes')
        result[track] = {k: v['sha256'] for k, v in binding.items()}
    return result


def verify_report(report, pair, items, captures, work):
    state = report['status']
    if state == 'unavailable':
        check(not items, 'unavailable evidence cannot supply findings')
        return
    raw = read_evidence(report.get('receipt'), work, 'original review or QA receipt')
    track, role = pair
    original = parse(raw)
    check(isinstance(original, dict), 'original report must be a JSON object')
    identity = None
    if role in READERS:
        captures_here = captures[track]
        reviewer = report.get('reviewerTaskId')
        check(isinstance(reviewer, str) and bool(reviewer.strip()) and reviewer == reviewer.strip()
              and original.get('reviewerTaskId') == reviewer and original.get('perspective') == READER_PERSPECTIVES[role]
              and original.get('execution') == 'reading-only', 'reader receipt is not bound to the projected role and actual dispatch ID')
        identity = (reviewer, report['receipt']['sha256'])
        check(original.get('inputManifestSha256') == captures_here['reader']
              and type(original.get('passed')) is bool and original['passed'] == (state == 'pass'),
              'reader verdict is not bound to this capture and status')
        if role in ('visual-design', 'diagram-review'):
            check(original.get('visualManifestSha256') == captures_here['visual'], 'reader used a different visual capture')
        if role == 'diagram-review':
            check(original.get('diagramManifestSha256') == captures_here['diagram'], 'reader used a different diagram capture')
    else:
        check(original.get('kind') == 'paykit-early-evidence-v1' and original.get('candidate') == report['candidate']
              and original.get('track') == track and original.get('role') == role and original.get('status') == state,
              'early evidence verdict is not bound to this candidate and role')
        read_evidence(original.get('evidence'), work, 'underlying early evidence')
    found = original.get('findings')
    if not isinstance(found, list) or not all(isinstance(x, dict) and type(x.get('blocking')) is bool
            and isinstance(x.get('location'), str) and isinstance(x.get('recommendation'), str) for x in found):
        raise ValueError('malformed original findings')
    projection = lambda values: sorted((r['location'], r['recommendation'], r['blocking']) for r in values)
    check(projection(found) == projection(items), 'feedback differs from original findings')
    return identity


def early_receipt(packet, gate_hash):
    rows = sorted((r for r in packet['reports'] if r['role'] not in READERS), key=lambda r: (r['track'], r['role']))
    return {'kind': 'paykit-early-admission-v1', 'gate': gate_hash, 'candidate': packet['candidate'],
            'evidenceSha256': sha256(json.dumps(rows, sort_keys=True, separators=(',', ':')).encode())}


def decide(gate, packet, history, head, candidate, gate_hash, now, work, policy_edit):
    check(isinstance(gate, dict) and isinstance(packet, dict), 'expected gate and feedback objects')
    check(gate.get('head') == head and gate.get('action') == 'refresh_needed', 'gate does not authorize this refresh')
    tracks = gate.get('refreshVersions')
    check(isinstance(tracks, list) and tracks and all(v in VERSIONS for v in tracks)
          and len(tracks) == len(set(tracks)), 'invalid refresh scope')
    check(isinstance(gate.get('processFilesChanged'), list), 'missing process change inventory')
    start, deadline = gate.get('startedEpoch'), gate.get('deadlineEpoch')
    if not isinstance(start, (int, float)) or isinstance(start, bool) or not isinstance(deadline, (int, float)):
        raise ValueError('invalid original run deadline')
    check(math.isfinite(start) and 0 <= start <= now and deadline == start + 21600, 'invalid original run deadline')
    phase = packet.get('phase')
    process_review = packet.get('processReviewRequired')
    check(phase in ('early', 'readers') and type(process_review) is bool, 'invalid feedback phase or process-review flag')
    check(packet.get('candidate') == candidate, 'feedback packet is for different candidate bytes')
    check(isinstance(history, list) and len(history) <= MAX_REPAIRS, 'invalid repair journal')
    prior = set()
    early_used = False
    for row in history:
        check(isinstance(row, dict) and row.get('gate') == gate_hash and is_sha(row.get('candidate'))
              and row.get('phase') in ('early', 'readers') and row['candidate'] not in prior,
              'invalid or cross-run repair journal entry')
        keys = row.get('issues')
        check(isinstance(keys, list) and keys and all(isinstance(x, str) for x in keys)
              and len(keys) == len(set(keys)), 'invalid journal issue keys')
        check(not (row['phase'] == 'early' and (prior or early_used)), 'early repair must precede reader repairs and may occur only once')
        early_used |= row['phase'] == 'early'
        prior.add(row['candidate'])

    decisions = {'candidate': candidate, 'phase': phase, 'repairsUsed': len(history),
                 'remainingSeconds': max(0, int(deadline - now))}

    def result(action, reason):
        return {**decisions, 'action': action, 'reason': reason}

    if now >= deadline:
        return result('stop', 'run_deadline_reached')
    if candidate in prior:
        return result('stop', 'unchanged_or_previously_repaired_candidate')
    if history and history[-1]['phase'] == 'readers' and phase == 'early':
        # After a late repair the same early checks still run; no early charge may be made.
        early_used = True

    captures = bound_captures(gate, packet, work, phase)
    expected = expected_pairs(gate, phase, process_review or policy_edit)
    reports = packet.get('reports')
    if not isinstance(reports, list):
        raise ValueError('missing feedback reports')
    feedback = {}
    outstanding = []
    issues = {}
    reader_ids = set()
    reader_receipts = set()
    for report in reports:
        check(isinstance(report, dict) and report.get('candidate') == candidate, 'stale candidate report')
        pair = (report.get('track'), report.get('role'))
        check(all(isinstance(x, str) for x in pair) and pair in expected and pair not in feedback,
              'unexpected or duplicate review report')
        state, items = report.get('status'), report.get('findings')
        if state not in ('pass', 'feedback', 'unavailable') or not isinstance(items, list):
            raise ValueError('invalid report outcome')
        observed = set()
        has_blocker = False
        for item in items:
            check(isinstance(item, dict) and isinstance(item.get('key'), str) and KEY.fullmatch(item['key'])
                  and isinstance(item.get('location'), str) and item['location'].strip()
                  and isinstance(item.get('recommendation'), str) and item['recommendation'].strip()
                  and type(item.get('blocking')) is bool, 'invalid anchored finding')
            key = f'{pair[0]}:{item["key"]}'
            check(key not in observed, 'duplicate finding within a report')
            observed.add(key)
            has_blocker |= item['blocking']
            if key not in issues:
                issues[key] = {'material': [], 'minor': []}
            issues[key]['material' if item['blocking'] else 'minor'].append(
                {'role': pair[1], 'location': item['location'], 'recommendation': item['recommendation']})
        check((state == 'feedback') == has_blocker, 'material feedback must agree with report outcome')
        identity = verify_report(report, pair, items, captures, work)
        if identity is not None:
            reviewer, receipt_sha = identity
            check(reviewer not in reader_ids and receipt_sha not in reader_receipts,
                  'reader roles require distinct original reports and dispatch IDs')
            reader_ids.add(reviewer)
            reader_receipts.add(receipt_sha)
        if state == 'unavailable':
            outstanding.append({'track': pair[0], 'role': pair[1]})
        feedback[pair] = has_blocker

    missing = [{'track': track, 'role': role} for track, role in expected if (track, role) not in feedback]
    important = [{'key': k, 'sources': issues[k]['material']} for k in sorted(issues) if issues[k]['material']]
    minor = [{'key': k, 'sources': issues[k]['minor']} for k in sorted(issues) if issues[k]['minor']]
    decisions.update(material=important, backlog=minor, missing=missing, unavailable=outstanding)
    if phase == 'readers' and any(feedback.get((v, r)) or {'track': v, 'role': r} in outstanding
                                 for v, r in expected if r not in READERS):
        return result('stop', 'earlier_candidate_not_reviewable')
    if phase == 'readers':
        marker = work / f'early-admission-{candidate}.json'
        check(marker.is_file() and not marker.is_symlink() and parse(marker.read_bytes()) == early_receipt(packet, gate_hash),
              'missing or mismatched early admission receipt for this candidate')
    if missing:
        return result('collect_feedback', 'review_batch_incomplete')
    if outstanding:
        return result('stop', 'required_evidence_unavailable')
    if not important:
        return result('continue', 'ready_for_reader_feedback' if phase == 'early' else 'run_all_final_publication_gates')
    if len(history) >= MAX_REPAIRS:
        return result('stop', 'repair_limit_reached')
    if phase == 'early' and early_used:
        return result('stop', 'last_repair_reserved_for_reader_feedback')
    if deadline - now <= FINAL_RESERVE_SECONDS:
        return result('stop', 'insufficient_repair_time_before_final_checks')
    keys = {row['key'] for row in important}
    if history and set(history[-1]['issues']) <= keys:
        return result('stop', 'no_previously_addressed_material_issue_cleared')
    return result('repair', 'batch_material_findings_only')


@contextmanager
def locked_journal(path, reserve):
    flags = (os.O_RDWR | os.O_APPEND) if reserve else os.O_RDONLY
    fd = os.open(path, flags | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    with os.fdopen(fd, 'a+' if reserve else 'r', encoding='utf-8', newline='') as file:
        check(stat.S_ISREG(os.fstat(file.fileno()).st_mode), 'repair journal is not a regular file')
        fcntl.flock(file, fcntl.LOCK_EX if reserve else fcntl.LOCK_SH)
        file.seek(0)
        yield file


def journal_records(text, label):
    # An otherwise parseable final record without LF would concatenate with the append.
    check(text.endswith('\n'), f'incomplete {label} record delimiter')
    return [parse(line) for line in text[:-1].split('\n')]


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root', type=Path, required=True)
    p.add_argument('--fingerprint', action='store_true')
    p.add_argument('--initialize', action='store_true', help='Create the one fixed-path journal once per run')
    p.add_argument('--admit-early', action='store_true', help='Record verified early continue before dispatching readers')
    p.add_argument('--gate', type=Path)
    p.add_argument('--feedback', type=Path)
    p.add_argument('--journal', type=Path)
    p.add_argument('--reserve', action='store_true', help='Record an authorized repair batch before dispatch')
    a = p.parse_args()
    try:
        head, candidate, policy_edit = snapshot(a.root)
        if a.fingerprint:
            check(not (a.gate or a.feedback or a.journal or a.reserve or a.initialize or a.admit_early), 'fingerprint does not read or reserve a repair')
            out = {'action': 'fingerprinted', 'head': head, 'candidate': candidate}
        else:
            if a.gate is None or a.journal is None:
                raise ValueError('gate and journal are required')
            work = a.gate.parent.resolve()
            check(a.gate.resolve() == work / 'gate.json' and work != a.root.resolve()
                  and a.root.resolve() not in work.parents and a.journal.resolve() == work / 'review-repairs.jsonl'
                  and not a.gate.is_symlink() and not a.journal.is_symlink(), 'expected fixed gate and journal paths under W outside S')
            raw = a.gate.read_bytes()
            gate_hash = sha256(raw)
            gate = parse(raw)
            header = {'kind': 'paykit-review-repair-v1', 'gate': gate_hash}
            anchor = work / 'review-repairs-anchor.jsonl'
            if a.initialize:
                check(not a.reserve and not a.admit_early and a.feedback is None and not anchor.exists() and not anchor.is_symlink()
                      and gate.get('action') == 'refresh_needed' and gate.get('head') == head,
                      'initialization requires the original refresh gate only')
                with a.journal.open('x') as file:
                    st = os.fstat(file.fileno())
                    file.write(json.dumps(header) + '\n')
                    file.flush()
                    os.fsync(file.fileno())
                with anchor.open('x') as marker:
                    marker.write(json.dumps({**header, 'journalDevice': st.st_dev, 'journalInode': st.st_ino}) + '\n')
                    marker.flush()
                    os.fsync(marker.fileno())
                out = {'action': 'initialized', 'gate': gate_hash}
            else:
                if a.feedback is None:
                    raise ValueError('feedback is required')
                check(anchor.is_file() and not anchor.is_symlink(),
                      'missing or stale run initialization receipt')
                check(not a.feedback.is_symlink() and work in a.feedback.resolve().parents, 'feedback must be in W')
                packet = parse(a.feedback.read_bytes())
                check(not (a.reserve and a.admit_early), 'admission does not reserve a repair')
                with locked_journal(a.journal, a.reserve or a.admit_early) as file:
                    lines = journal_records(file.read(), 'repair journal')
                    check(lines and lines[0] == header, 'missing or stale original repair journal header')
                    history = lines[1:]
                    st = os.fstat(file.fileno())
                    with anchor.open(encoding='utf-8', newline='') as marker:
                        anchors = journal_records(marker.read(), 'repair anchor')
                    check(anchors and anchors[0] == {**header, 'journalDevice': st.st_dev, 'journalInode': st.st_ino}
                          and anchors[1:] == history, 'repair journal replacement or rollback disagrees with its anchor')
                    for i in range(MAX_REPAIRS):
                        slot = work / f'review-repair-reservation-{i+1}.json'
                        check(not slot.is_symlink() and (slot.is_file() and parse(slot.read_bytes()) == history[i]
                              if i < len(history) else not slot.exists()), 'repair journal and immutable reservation receipts disagree')
                    out = decide(gate, packet, history, head, candidate, gate_hash, time.time(), work, policy_edit)
                    if a.reserve and out['action'] == 'repair':
                        check(snapshot(a.root) == (head, candidate, policy_edit), 'candidate changed before reserving its repair')
                        out = decide(gate, packet, history, head, candidate, gate_hash, time.time(), work, policy_edit)
                        if out['action'] == 'repair':
                            entry = {'gate': gate_hash, 'phase': out['phase'], 'candidate': candidate,
                                     'issues': [issue['key'] for issue in out['material']]}
                            with (work / f'review-repair-reservation-{len(history)+1}.json').open('x') as slot:
                                slot.write(json.dumps(entry) + '\n')
                                slot.flush()
                                os.fsync(slot.fileno())
                            file.write(json.dumps(entry) + '\n')
                            file.flush()
                            os.fsync(file.fileno())
                            with os.fdopen(os.open(anchor, os.O_WRONLY | os.O_APPEND | os.O_NOFOLLOW), 'a') as marker:
                                marker.write(json.dumps(entry) + '\n')
                                marker.flush()
                                os.fsync(marker.fileno())
                            out = {**out, 'reserved': True, 'repairsUsed': len(history) + 1}
                    if a.admit_early and out['action'] == 'continue' and out['phase'] == 'early':
                        check(snapshot(a.root) == (head, candidate, policy_edit), 'candidate changed before early admission')
                        out = decide(gate, packet, history, head, candidate, gate_hash, time.time(), work, policy_edit)
                        if out['action'] == 'continue':
                            marker = work / f'early-admission-{candidate}.json'
                            receipt = early_receipt(packet, gate_hash)
                            if marker.exists():
                                check(not marker.is_symlink() and parse(marker.read_bytes()) == receipt, 'early admission receipt changed')
                            else:
                                with marker.open('x') as handle:
                                    handle.write(json.dumps(receipt) + '\n')
                            out = {**out, 'admitted': True}
    except (OSError, TypeError, KeyError, ValueError, subprocess.SubprocessError) as error:
        print(json.dumps({'action': 'error', 'reason': str(error)}))
        raise SystemExit(1)
    print(json.dumps(out))


if __name__ == '__main__':
    main()
