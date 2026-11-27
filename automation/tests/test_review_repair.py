"""Exercise the shared repair admission loop with isolated Git/feedback fixtures."""
from hashlib import sha256
import json
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

SCRIPT = Path(__file__).parents[1] / 'review_repair.py'
READERS = ('newcomer', 'editorial', 'security-privacy', 'failure-recovery', 'usability-accessibility', 'visual-design', 'diagram-review')
EARLY = ('factual', 'qa', 'process')


class ReviewRepairTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.w = Path(temp.name)
        self.site = self.w / 'site'
        self.site.mkdir()
        self.doc = self.site / 'draft.md'
        self.doc.write_text('Initial draft.\n')
        self.git('init', '-q')
        self.git('add', 'draft.md')
        self.git('-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid', 'commit', '-qm', 'fixture baseline')
        self.start = (time.time() - 21600 + 600 if self._testMethodName == 'test_deadline_reserve_and_no_change_scope'
                      else time.time() - 1)
        changed = [] if self._testMethodName == 'test_real_process_edit_requires_process_review' else ['automation/WEEKLY.md']
        self.gate = self.w / 'gate.json'
        self.gate_data = {'action': 'refresh_needed', 'head': self.git('rev-parse', 'HEAD').strip(),
                          'startedEpoch': self.start, 'deadlineEpoch': self.start + 21600,
                          'refreshVersions': ['coming-soon'], 'processFilesChanged': changed,
                          'selections': {'coming-soon': {'sourceCommit': 'b' * 40}}}
        self.gate.write_text(json.dumps(self.gate_data))
        self.feedback = self.w / 'feedback.json'
        self.journal = self.w / 'review-repairs.jsonl'
        self.serial = 0
        self.early_packets = {}
        self.assertEqual(self.cli('--gate', self.gate, '--journal', self.journal, '--initialize')['action'], 'initialized')

    def binding(self, leaf, value):
        path = self.w / leaf
        data = json.dumps(value).encode()
        with path.open('xb') as out:
            out.write(data)
        return {'path': str(path), 'sha256': sha256(data).hexdigest()}

    def reserved(self):
        return len(self.journal.read_text().splitlines()) - 1

    def git(self, *args):
        return subprocess.check_output(['git', '-C', self.site, *args], text=True, timeout=10)

    def cli(self, *args, ok=True):
        proc = subprocess.run([sys.executable, '-B', SCRIPT, '--root', self.site, *args], capture_output=True, text=True, timeout=14)
        self.assertEqual(proc.returncode, 0 if ok else 1, proc.stdout + proc.stderr)
        return json.loads(proc.stdout)

    def fingerprint(self):
        return self.cli('--fingerprint')['candidate']

    def reports(self, phase, overrides=None, *, drop=(), version='coming-soon', auto_admit=True):
        candidate = self.fingerprint()
        self.serial += 1
        capture = self.binding(f'capture-{self.serial}.json', {'kind': 'rendered-public-reader-input', 'version': version,
                               'versionMetadata': {'commit': 'b' * 40}, 'candidateSha256': candidate})
        visual = self.binding(f'visual-{self.serial}.json', {'kind': 'rendered-public-visual-input',
                              'inputManifestSha256': capture['sha256']})
        diagram = self.binding(f'diagram-{self.serial}.json', {'kind': 'rendered-public-diagram-input',
                               'inputManifestSha256': capture['sha256'], 'visualManifestSha256': visual['sha256'],
                               'sourceCommit': 'b' * 40})
        rows = []
        for role in EARLY if phase == 'early' else EARLY + READERS:
            if role in drop:
                continue
            notes = (overrides or {}).get(role, [])
            material = any(note['blocking'] for note in notes)
            verdict = 'feedback' if material else 'pass'
            original = {'inputManifestSha256': capture['sha256'], 'passed': not material,
                        'findings': notes, 'reviewerTaskId': 'synthetic-' + role,
                        'perspective': 'diagram-accuracy-usefulness' if role == 'diagram-review' else role,
                        'execution': 'reading-only',
                        'visualManifestSha256': visual['sha256'], 'diagramManifestSha256': diagram['sha256']}
            if role in EARLY:
                raw = self.binding(f'raw-{role}-{self.serial}.json', {'synthetic': verdict})
                original = {'kind': 'paykit-early-evidence-v1', 'candidate': candidate, 'track': version,
                            'role': role, 'status': verdict, 'evidence': raw, 'findings': notes}
            receipt = self.binding(f'{role}-{self.serial}.json', original)
            rows.append({'candidate': candidate, 'track': version, 'role': role, 'status': verdict,
                         'findings': notes, 'receipt': receipt})
            if role in READERS:
                rows[-1]['reviewerTaskId'] = original['reviewerTaskId']
        if phase == 'readers' and auto_admit and not any(role in (overrides or {}) for role in EARLY):
            if candidate not in self.early_packets:
                early = {'phase': 'early', 'candidate': candidate, 'processReviewRequired': True,
                         'reports': [row for row in rows if row['role'] in EARLY]}
                self.assertTrue(self.run_plan(early, admit=True)['admitted'])
                self.early_packets[candidate] = early['reports']
            rows = self.early_packets[candidate] + [row for row in rows if row['role'] in READERS]
        return {'phase': phase, 'candidate': candidate, 'processReviewRequired': True, 'reports': rows,
                'captures': {version: {'reader': capture, 'visual': visual, 'diagram': diagram}}}

    def run_plan(self, packet, reserve=False, ok=True, admit=False):
        self.feedback.write_text(json.dumps(packet))
        flags = ('--reserve',) if reserve else ('--admit-early',) if admit else ()
        return self.cli('--gate', self.gate, '--feedback', self.feedback, '--journal', self.journal, *flags, ok=ok)

    def note(self, key, blocking=True):
        return {'key': key, 'blocking': blocking, 'location': '/docs/coming-soon/boundaries#test',
                'recommendation': 'Synthetic grounded change for the test.'}

    def test_early_repair_leaves_room_for_an_independent_reader_batch(self):
        one = self.run_plan(self.reports('early', {'factual': [self.note('fact')]}), reserve=True)
        self.assertEqual((one['action'], one['repairsUsed'], one['reserved']), ('repair', 1, True))
        old = self.run_plan(self.reports('early'), reserve=True)
        self.assertEqual(old['reason'], 'unchanged_or_previously_repaired_candidate')
        self.doc.write_text('Draft with verified factual repair.\n')
        stop = self.run_plan(self.reports('early', {'qa': [self.note('test-result')]}), reserve=True)
        self.assertEqual(stop['reason'], 'last_repair_reserved_for_reader_feedback')
        self.assertEqual(self.run_plan(self.reports('early'))['reason'], 'ready_for_reader_feedback')
        findings = {'editorial': [self.note('storage-scope'), self.note('minor-style', False)],
                    'failure-recovery': [self.note('request-id')], 'diagram-review': [self.note('storage-scope', False)]}
        withheld = self.run_plan(self.reports('readers', findings, drop=('visual-design',)), reserve=True)
        self.assertEqual(withheld['action'], 'collect_feedback')
        self.assertEqual(withheld['missing'], [{'track': 'coming-soon', 'role': 'visual-design'}])
        self.assertEqual(self.reserved(), 1)
        two = self.run_plan(self.reports('readers', findings), reserve=True)
        self.assertEqual((two['action'], two['repairsUsed'], two['reserved']), ('repair', 2, True))
        self.assertEqual([x['key'] for x in two['material']], ['coming-soon:request-id', 'coming-soon:storage-scope'])
        self.assertEqual(len(two['material'][1]['sources']), 1)
        self.assertEqual([x['key'] for x in two['backlog']], ['coming-soon:minor-style', 'coming-soon:storage-scope'])
        self.doc.write_text('Final draft with verified reader repairs.\n')
        fixed = self.run_plan(self.reports('readers', {'editorial': [self.note('minor-style', False)]}), reserve=True)
        self.assertEqual((fixed['action'], fixed['reason']), ('continue', 'run_all_final_publication_gates'))
        self.assertNotIn('reserved', fixed)
        self.assertEqual(self.reserved(), 2)

    def test_every_reader_can_contribute_material_feedback(self):
        changes = {role: [self.note('material-' + role)] for role in READERS}
        data = self.run_plan(self.reports('readers', changes))
        self.assertEqual(data['action'], 'repair')
        self.assertEqual({x['sources'][0]['role'] for x in data['material']}, set(READERS))
        self.assertEqual(self.reserved(), 0)
        all_minor = {role: [self.note('minor-' + role, False)] for role in READERS}
        data = self.run_plan(self.reports('readers', all_minor))
        self.assertEqual(data['action'], 'continue')
        self.assertEqual(len(data['backlog']), len(READERS))
        self.assertEqual(self.reserved(), 0)

    def test_same_material_issue_with_unrelated_edit_is_not_progress(self):
        finding = {'editorial': [self.note('scope')]}
        self.assertTrue(self.run_plan(self.reports('readers', finding), reserve=True)['reserved'])
        self.doc.write_text('Unrelated minor wording change.\n')
        held = self.run_plan(self.reports('readers', finding), reserve=True)
        self.assertEqual(held['reason'], 'no_previously_addressed_material_issue_cleared')
        self.assertEqual(self.reserved(), 1)

    def test_no_retry_after_shared_round_limit(self):
        self.run_plan(self.reports('readers', {'editorial': [self.note('one')]}), reserve=True)
        self.doc.write_text('Fix one applied.\n')
        self.run_plan(self.reports('readers', {'editorial': [self.note('two')]}), reserve=True)
        self.doc.write_text('Fix two applied.\n')
        self.assertEqual(self.run_plan(self.reports('readers', {'editorial': [self.note('three')]}), reserve=True)['reason'],
                         'repair_limit_reached')

    def test_unavailable_evidence_and_early_blockers_do_not_promote(self):
        p = self.reports('readers')
        p['reports'][-1]['status'] = 'unavailable'
        self.assertEqual(self.run_plan(p)['reason'], 'required_evidence_unavailable')
        p = self.reports('readers', {'factual': [self.note('unsafe-content')]})
        self.assertEqual(self.run_plan(p)['reason'], 'earlier_candidate_not_reviewable')
        self.assertEqual(self.reserved(), 0)

    def test_stale_downgraded_duplicate_or_cross_run_evidence_refused(self):
        p = self.reports('readers')
        p['reports'][-1]['candidate'] = 'b' * 64
        self.assertEqual(self.run_plan(p, ok=False)['action'], 'error')
        p = self.reports('readers', {'editorial': [self.note('wrong')]})
        next(q for q in p['reports'] if q['role'] == 'editorial')['status'] = 'pass'
        self.assertEqual(self.run_plan(p, ok=False)['action'], 'error')
        p = self.reports('readers')
        p['reports'].append(dict(p['reports'][0]))
        self.assertEqual(self.run_plan(p, ok=False)['action'], 'error')
        self.run_plan(self.reports('readers', {'editorial': [self.note('one')]}), reserve=True)
        self.gate.write_text(json.dumps(self.gate_data, indent=2))
        self.doc.write_text('Different bytes.\n')
        self.assertEqual(self.run_plan(self.reports('readers', auto_admit=False), ok=False)['action'], 'error')

    def test_deadline_reserve_and_no_change_scope(self):
        held = self.run_plan(self.reports('readers', {'editorial': [self.note('one')]}), reserve=True)
        self.assertEqual(held['reason'], 'insufficient_repair_time_before_final_checks')
        self.assertEqual(self.reserved(), 0)
        self.gate_data['action'] = 'skip'
        self.gate.write_text(json.dumps(self.gate_data))
        self.assertEqual(self.run_plan(self.reports('early'), ok=False)['action'], 'error')

    def test_fingerprint_uses_bytes_not_mtime_or_capture_receipt(self):
        prior = self.fingerprint()
        self.doc.touch()
        (self.w / 'capture-from-new-port.json').write_text('{"port":42}\n')
        self.assertEqual(self.fingerprint(), prior)
        self.doc.write_text('Materially different draft.\n')
        self.assertNotEqual(self.fingerprint(), prior)


    def test_no_progress_across_early_and_reader_phases(self):
        self.run_plan(self.reports('early', {'factual': [self.note('scope')]}), reserve=True)
        self.doc.write_text('Changed bytes, original scope problem still present.\n')
        self.assertEqual(self.run_plan(self.reports('early'))['action'], 'continue')
        result = self.run_plan(self.reports('readers', {'editorial': [self.note('scope')]}), reserve=True)
        self.assertEqual(result['reason'], 'no_previously_addressed_material_issue_cleared')
        self.assertEqual(self.reserved(), 1)

    def test_journal_cannot_be_replaced_truncated_or_reinitialized(self):
        header = self.journal.read_text()
        self.run_plan(self.reports('early', {'qa': [self.note('one')]}), reserve=True)
        self.doc.write_text('New revision.\n')
        packet = self.reports('early')
        self.feedback.write_text(json.dumps(packet))
        alternate = self.w / 'another-journal.jsonl'
        self.assertEqual(self.cli('--gate', self.gate, '--feedback', self.feedback, '--journal', alternate, ok=False)['action'], 'error')
        self.journal.write_text(header)
        self.assertIn('rollback disagrees', self.run_plan(packet, ok=False)['reason'])
        self.journal.unlink()
        self.assertEqual(self.run_plan(packet, reserve=True, ok=False)['action'], 'error')
        self.assertEqual(self.cli('--gate', self.gate, '--journal', self.journal, '--initialize', ok=False)['action'], 'error')

    def test_mixed_captures_and_changed_or_downgraded_report_bytes_refused(self):
        packet = self.reports('readers', {'editorial': [self.note('scope')]})
        original = next(x for x in packet['reports'] if x['role'] == 'editorial')
        data = json.loads(Path(original['receipt']['path']).read_bytes())
        other = self.binding('other-editor.json', {**data, 'inputManifestSha256': 'f' * 64})
        before = original['receipt']
        original['receipt'] = other
        self.assertIn('not bound to this capture', self.run_plan(packet, ok=False)['reason'])
        original['receipt'] = before
        original['findings'][0]['recommendation'] = 'Changed recommendation in projection only.'
        self.assertIn('differs from original', self.run_plan(packet, ok=False)['reason'])
        Path(before['path']).write_text('{}')
        self.assertIn('stale original review', self.run_plan(packet, ok=False)['reason'])

    def test_real_process_edit_requires_process_review(self):
        packet = self.reports('early', drop=('process',))
        packet['processReviewRequired'] = False
        self.assertEqual(self.run_plan(packet)['action'], 'continue')
        (self.site / 'automation').mkdir()
        (self.site / 'automation' / 'helper.py').write_text('# New fixture process input.\n')
        packet = self.reports('early', drop=('process',))
        packet['processReviewRequired'] = False
        self.assertEqual(self.run_plan(packet)['missing'], [{'track': 'coming-soon', 'role': 'process'}])


    def test_early_admission_has_exact_candidate_and_verdict_bindings(self):
        reader = self.reports('readers', auto_admit=False)
        self.assertIn('missing or mismatched early admission', self.run_plan(reader, ok=False)['reason'])
        early = self.reports('early')
        row = early['reports'][0]
        real = json.loads(Path(row['receipt']['path']).read_bytes())
        row['receipt'] = self.binding('stale-early-report.json', {**real, 'candidate': 'd' * 64})
        self.assertIn('early evidence verdict is not bound', self.run_plan(early, admit=True, ok=False)['reason'])
        early = self.reports('early')
        row = early['reports'][1]
        real = json.loads(Path(row['receipt']['path']).read_bytes())
        row['receipt'] = self.binding('failed-qa-report.json', {**real, 'status': 'feedback'})
        self.assertIn('early evidence verdict is not bound', self.run_plan(early, admit=True, ok=False)['reason'])
        self.assertEqual(self.reserved(), 0)

    def test_old_capture_cannot_be_relabeled_after_edit(self):
        prior = self.reports('readers')
        self.doc.write_text('Changed but same documented source.\n')
        current = self.reports('readers')
        current['captures'] = prior['captures']
        self.assertIn('not for the exact candidate', self.run_plan(current, ok=False)['reason'])
        current = self.reports('readers')
        current['captures']['coming-soon']['visual'] = prior['captures']['coming-soon']['visual']
        self.assertIn('visual capture is not bound', self.run_plan(current, ok=False)['reason'])

    def test_journal_inode_and_anchor_prevent_coordinated_partial_rollback(self):
        pristine = self.journal.read_bytes()
        self.run_plan(self.reports('early', {'qa': [self.note('one')]}), reserve=True)
        original = self.w / 'original-journal'
        self.journal.rename(original)
        self.journal.write_bytes(original.read_bytes())
        self.doc.write_text('Different draft.\n')
        after = self.reports('early')
        self.assertIn('replacement or rollback', self.run_plan(after, ok=False)['reason'])
        original.replace(self.journal)
        self.journal.write_bytes(pristine)
        (self.w / 'review-repair-reservation-1.json').unlink()
        self.assertIn('replacement or rollback', self.run_plan(after, ok=False)['reason'])


    def test_unterminated_parseable_journal_or_anchor_never_reserves(self):
        packet = self.reports('early', {'qa': [self.note('one')]})
        anchor = self.w / 'review-repairs-anchor.jsonl'
        for path, name in ((self.journal, 'repair journal'), (anchor, 'repair anchor')):
            with self.subTest(name=name):
                raw = path.read_bytes()
                self.assertTrue(raw.endswith(b'\n'))
                path.write_bytes(raw[:-1])
                result = self.run_plan(packet, reserve=True, ok=False)
                self.assertIn(f'incomplete {name} record delimiter', result['reason'])
                self.assertNotIn('reserved', result)
                self.assertEqual(path.read_bytes(), raw[:-1])
                self.assertFalse((self.w / 'review-repair-reservation-1.json').exists())
                path.write_bytes(raw)
        self.assertTrue(self.run_plan(packet, reserve=True)['reserved'])

    def test_one_receipt_or_dispatch_id_cannot_supply_two_reader_roles(self):
        packet = self.reports('readers', {'editorial': [self.note('actual-finding')]})
        first = next(row for row in packet['reports'] if row['role'] == 'newcomer')
        second = next(row for row in packet['reports'] if row['role'] == 'security-privacy')
        original_second = json.loads(Path(second['receipt']['path']).read_bytes())
        second['receipt'] = first['receipt']
        second['reviewerTaskId'] = first['reviewerTaskId']
        reused = self.run_plan(packet, reserve=True, ok=False)
        self.assertIn('not bound to the projected role', reused['reason'])
        self.assertNotIn('reserved', reused)
        second['receipt'] = self.binding('different-report-same-dispatch.json',
                                         {**original_second, 'reviewerTaskId': first['reviewerTaskId']})
        duplicate = self.run_plan(packet, reserve=True, ok=False)
        self.assertIn('distinct original reports and dispatch IDs', duplicate['reason'])
        self.assertNotIn('reserved', duplicate)
        self.assertEqual(self.reserved(), 0)


if __name__ == '__main__':
    unittest.main()
