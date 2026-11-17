"""Delta drafts use isolated temporary roots; canonical corpus is read-only."""
import copy
import importlib.util
import json
from pathlib import Path
import unittest
from unittest.mock import patch

_spec = importlib.util.spec_from_file_location('draft_fixtures', Path(__file__).with_name('test_host_bridge.py'))
fixture = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fixture)


class DraftTests(unittest.TestCase):
    setUpClass = fixture.BridgeTests.__dict__['setUpClass']
    setUp = fixture.BridgeTests.setUp
    git = fixture.BridgeTests.git
    call = fixture.BridgeTests.call
    blueprint = fixture.BridgeTests.blueprint

    def seed(self):
        bp = self.blueprint()
        directory = self.root / 'automation/blueprints'
        directory.mkdir(exist_ok=True)
        raw = json.dumps(bp, separators=(',', ':')).encode()
        for version in self.bridge.VERSIONS:
            other = copy.deepcopy(bp)
            for evidence in other['evidence']:
                evidence['version'] = version
            (directory / (version + '.json')).write_bytes(raw if version == 'released' else json.dumps(other).encode())
        (self.root / 'automation/sources.lock.json').write_text(json.dumps(self.lock))
        return bp, raw

    def test_00_released_noop_roundtrip_preserves_exact_raw_bytes(self):
        bp, raw = self.seed()
        opened = self.call('draft_open', {'version':'released'})
        self.assertEqual(set(opened), {'version','revision','summary'})
        self.assertRegex(opened['revision'], r'^[a-f0-9]{64}$')
        summary = opened['summary']
        self.assertTrue({'baselineCommit','sourceCommit','baselineSha256','deltaPlan','unresolvedEvidenceIds','staleReferences','selectors'} <= summary.keys())
        self.assertEqual(summary['unresolvedEvidenceIds'], [])
        self.assertEqual(summary['staleReferences'], [])
        read = self.call('draft_read', {'version':'released','revision':opened['revision'], 'selector':{'kind':'section','pageSlug':'overview','sectionId':'fixture'},'offset':0,'limit':6000})
        self.assertEqual(json.loads(read['text']), bp['pages'][0]['sections'][0])
        result = self.call('draft_finalize', {'version':'released','expectedRevision':opened['revision']})
        self.assertTrue(result['saved'])
        self.assertTrue(result['unchanged'])
        self.assertEqual((self.root/'automation/blueprints/released.json').read_bytes(), raw)
        self.assertEqual(self.call('draft_finalize', {'version':'released','expectedRevision':opened['revision']}), result)
        self.assertEqual(self.call('draft_open', {'version':'released'})['revision'], opened['revision'])


    def test_edits_are_atomic_revision_checked_scoped_and_closed_after_finalize(self):
        bp, raw = self.seed()
        opened = self.call('draft_open', {'version':'released'})
        old = opened['revision']
        section = copy.deepcopy(bp['pages'][0]['sections'][0])
        section['paragraphs'] = ['Explicit replacement']
        edit = {'op':'replaceSection','pageSlug':'overview','sectionId':'fixture','section':section}
        changed = self.call('draft_edit', {'version':'released','expectedRevision':old,'edits':[edit]})
        self.assertNotEqual(changed['revision'], old)
        self.assertEqual(self.call('draft_open', {'version':'released'}), changed)
        for revision, edits in [(old,[edit]), (changed['revision'],[edit,dict(edit,sectionId='foreign')]), (changed['revision'],[dict(edit,path='.env')]), (changed['revision'],[edit]*17), (changed['revision'],[dict(edit,section=dict(section,paragraphs=['é'*20000]))])]:
            with self.assertRaises(ValueError):
                self.call('draft_edit', {'version':'released','expectedRevision':revision,'edits':edits})
            self.assertEqual(self.call('draft_open', {'version':'released'}), changed)
        self.assertEqual((self.root/'automation/blueprints/released.json').read_bytes(), raw)
        result = self.call('draft_finalize', {'version':'released','expectedRevision':changed['revision']})
        self.assertFalse(result['unchanged'])
        saved = json.loads((self.root/'automation/blueprints/released.json').read_bytes())
        expected = copy.deepcopy(bp)
        expected['pages'][0]['sections'][0] = section
        self.assertEqual(saved, expected)
        with self.assertRaises(ValueError):
            self.call('draft_edit', {'version':'released','expectedRevision':changed['revision'],'edits':[edit]})


    def advance(self):
        (self.source/'README.md').write_text('NEW PUBLIC CONTRACT\nline two\n')
        self.git(self.source,'add','README.md')
        self.git(self.source,'commit','-qm','changed contract')
        self.current = self.git(self.source,'rev-parse','HEAD')
        self.lock['released']['commit'] = self.current
        p = self.bridge.p
        record = p.read_json(p.CACHE/'run.lock/owner.json')
        record['sourceHash'] = p.digest(self.lock)
        p.save_json(p.CACHE/'run.lock/owner.json',record)
        p.save_json(p.CACHE/'runs'/self.runid/'sources.json',self.lock)

    def test_delta_requires_explicit_validated_evidence_and_presentation_refresh(self):
        bp, _ = self.seed()
        bp['pages'][0]['sections'][0]['paragraphs'] = ['Current snapshot '+self.commit[:12]]
        bp['pages'][0]['sourceLinks'] = [{'href':'https://github.com/pubky/paykit-rs/tree/'+self.commit,'label':'Current '+self.commit[:7]}]
        bp['diagrams'] = [{'id':'fixture-flow','mermaid':'flowchart LR\n A --> B','caption':'Fixture','alt':'Fixture','relationships':[{'text':'Contract','evidence':['released-fixture']}]}]
        target = self.root/'automation/blueprints/released.json'
        target.write_text(json.dumps(bp))
        raw = target.read_bytes()
        self.advance()
        opened = self.call('draft_open',{'version':'released'})
        self.assertEqual(opened['summary']['unresolvedEvidenceIds'], ['released-fixture'])
        self.assertTrue(opened['summary']['staleReferences'])
        record = dict(bp['evidence'][0],commit=self.current,excerpt='NEW PUBLIC CONTRACT',sourceHash=self.bridge.p.digest(b'NEW PUBLIC CONTRACT\nline two\n'))
        for mutation in ({'sourceHash':'0'*64},{'excerpt':'invented'},{'commit':self.commit},{'anchor':{'start':'absent','end':'absent'}},{'path':'.env'}):
            with self.assertRaises(ValueError):
                self.call('draft_edit',{'version':'released','expectedRevision':opened['revision'],'edits':[{'op':'putEvidence','record':dict(record,**mutation)}]})
            self.assertEqual(self.call('draft_open',{'version':'released'}),opened)
        updated = self.call('draft_edit',{'version':'released','expectedRevision':opened['revision'],'edits':[{'op':'putEvidence','record':record}]})
        self.assertEqual(updated['summary']['unresolvedEvidenceIds'], [])
        with self.assertRaises(ValueError):
            self.call('draft_finalize',{'version':'released','expectedRevision':updated['revision']})
        self.assertEqual(target.read_bytes(), raw)
        section = copy.deepcopy(bp['pages'][0]['sections'][0]); section['paragraphs']=['Current snapshot '+self.current[:12]]
        links = [{'href':'https://github.com/pubky/paykit-rs/tree/'+self.current,'label':'Current '+self.current[:7]}]
        diagram = copy.deepcopy(bp['diagrams'][0]); diagram['caption']='Explicitly reviewed contract'
        updated = self.call('draft_edit',{'version':'released','expectedRevision':updated['revision'],'edits':[{'op':'replaceSection','pageSlug':'overview','sectionId':'fixture','section':section},{'op':'replaceSourceLinks','pageSlug':'overview','sourceLinks':links},{'op':'replaceDiagram','diagramId':'fixture-flow','diagram':diagram}]})
        self.assertEqual(updated['summary']['staleReferences'], [])
        self.assertTrue(self.call('draft_finalize',{'version':'released','expectedRevision':updated['revision']})['saved'])
        saved = json.loads(target.read_bytes())
        self.assertEqual(saved['pages'][1],bp['pages'][1])
        self.assertEqual(saved['evidence'][0]['claim'],bp['evidence'][0]['claim'])


    def test_rebinding_uses_exact_git_blob_identity_not_normalized_text(self):
        import subprocess
        bp, raw = self.seed()
        self.git(self.source, 'config', 'core.autocrlf', 'false')
        original = subprocess.check_output(['git', 'show', self.commit+':README.md'], cwd=self.source)
        for label, content in [('crlf', original.replace(b'\n', b'\r\n')),
                               ('whitespace', original.replace(b'line two', b'line  two')),
                               ('deleted', None), ('identical', original)]:
            with self.subTest(label=label):
                path = self.source/'README.md'
                if content is None: path.unlink()
                else: path.write_bytes(content)
                self.git(self.source, 'add', '-A')
                self.git(self.source, 'commit', '--allow-empty', '-qm', label)
                current = self.git(self.source, 'rev-parse', 'HEAD')
                if content is not None:
                    actual = subprocess.check_output(['git', 'show', current+':README.md'], cwd=self.source)
                    self.assertEqual(actual, content)
                self.lock['released']['commit'] = current
                p = self.bridge.p
                owner = p.read_json(p.CACHE/'run.lock/owner.json')
                owner['sourceHash'] = p.digest(self.lock)
                p.save_json(p.CACHE/'run.lock/owner.json', owner)
                p.save_json(p.CACHE/'runs'/self.runid/'sources.json', self.lock)
                session = 'blob-'+label
                opened = self.call('draft_open', {'version':'released'}, session=session)
                unchanged = content == original
                self.assertEqual(opened['summary']['unresolvedEvidenceIds'], [] if unchanged else ['released-fixture'])
                state = p.read_json(self.bridge.session_path(self.runid, session, 'draft-released.json'))
                record = state['blueprint']['evidence'][0]
                self.assertEqual(record, dict(bp['evidence'][0], commit=current if unchanged else self.commit))
                self.assertEqual((self.root/'automation/blueprints/released.json').read_bytes(), raw)
                if not unchanged:
                    with self.assertRaises(ValueError):
                        self.call('draft_finalize', {'version':'released','expectedRevision':opened['revision']}, session=session)

    def test_rebinding_rejects_noncanonical_blob_oid(self):
        self.seed()
        real = self.bridge.p.run
        def invalid(args, **kwargs):
            if args[:3] == ['git', 'rev-parse', '--verify']: return 'A'*40
            return real(args, **kwargs)
        with patch.object(self.bridge.p, 'run', invalid), self.assertRaises(ValueError):
            self.call('draft_open', {'version':'released'})

    def test_bootstrap_has_exact_public_policy_paths_and_delta_plan(self):
        self.seed()
        result = self.call('bootstrap')
        protocol = json.loads(Path('/workspace/paykit-reports/native-draft-protocol.json').read_text())
        self.assertEqual(result['requiredPolicyPaths'], protocol['policyPaths'])
        self.assertEqual(result['deltaPlan'], self.bridge.p.plan_changes(self.lock))

    def test_diagram_only_dependency_stays_unresolved_and_cannot_finalize(self):
        bp, _ = self.seed()
        bp['diagrams'] = [{'id':'fixture-flow','mermaid':'flowchart LR\n A --> B','caption':'Fixture','alt':'Fixture','relationships':[{'text':'Contract','evidence':['released-fixture']}]}]
        for page in bp['pages']:
            page['sections'][0]['evidence'] = ['released-static']
        static = dict(bp['evidence'][0],id='released-static',path='static.txt',excerpt='UNCHANGED',sourceHash=self.bridge.p.digest(b'UNCHANGED\n'))
        (self.source/'static.txt').write_text('UNCHANGED\n')
        self.git(self.source,'add','static.txt'); self.git(self.source,'commit','-qm','static source')
        self.commit = self.git(self.source,'rev-parse','HEAD')
        for record in bp['evidence']+[static]: record['commit'] = self.commit
        bp['sourceCommit'] = self.commit; bp['evidence'].append(static)
        for version in self.lock: 
            if version in self.bridge.VERSIONS: self.lock[version]['commit']=self.commit
        (self.root/'automation/sources.lock.json').write_text(json.dumps(self.lock))
        (self.root/'automation/blueprints/released.json').write_text(json.dumps(bp))
        self.advance()
        opened = self.call('draft_open',{'version':'released'})
        self.assertEqual(opened['summary']['unresolvedEvidenceIds'],['released-fixture'])
        self.assertIn('fixture-flow',json.dumps(opened['summary']['dependencyChanges']))
        with self.assertRaises(ValueError):
            self.call('draft_finalize',{'version':'released','expectedRevision':opened['revision']})


    def test_revisions_are_session_bound_and_tool_checks_never_nest_state_lock(self):
        import contextlib
        self.seed()
        b = self.bridge
        record = b.p.read_json(b.p.CACHE/'run.lock/owner.json'); record['tools']={'fixture':'v1'}
        b.p.save_json(b.p.CACHE/'run.lock/owner.json',record)
        depth = [0]
        real = b.p.state_lock
        @contextlib.contextmanager
        def observed(*args, **kwargs):
            with real(*args, **kwargs):
                depth[0] += 1
                try: yield
                finally: depth[0] -= 1
        def tools():
            self.assertEqual(depth[0],0,'tool subprocess checks may deadlock inside state lock')
            return {'fixture':'v1'}
        with patch.object(b.p,'state_lock',observed),patch.object(b.p,'tool_versions',tools):
            first = self.call('draft_open',{'version':'released'})
            second = self.call('draft_open',{'version':'released'},session='other-author')
            self.assertNotEqual(first['revision'],second['revision'])
            with self.assertRaises(ValueError):
                self.call('draft_read',{'version':'released','revision':first['revision'],'selector':{'kind':'summary'},'offset':0,'limit':6000},session='other-author')
            self.call('draft_finalize',{'version':'released','expectedRevision':first['revision']})


    def test_current_pinned_whole_public_corpus_noop_preserves_exact_bytes(self):
        corpus = Path(__file__).parents[2]
        current = json.loads((corpus/'automation/sources.lock.json').read_bytes())
        directory = self.root/'automation/blueprints'; directory.mkdir()
        originals = {}
        for version in self.bridge.VERSIONS:
            originals[version] = (corpus/f'automation/blueprints/{version}.json').read_bytes()
            (directory/(version+'.json')).write_bytes(originals[version])
        (self.root/'automation/sources.lock.json').write_text(json.dumps(current))
        (self.root/'automation/config.json').write_bytes((corpus/'automation/config.json').read_bytes())
        p = self.bridge.p
        owner = p.read_json(p.CACHE/'run.lock/owner.json')
        owner.update(sourceHash=p.digest(current),acceptanceHash=p.acceptance_digest())
        p.save_json(p.CACHE/'run.lock/owner.json',owner)
        p.save_json(p.CACHE/'runs'/self.runid/'sources.json',current)
        with patch.object(p,'REPO',corpus/'.automation/sources/paykit-rs'):
            for version in self.bridge.VERSIONS:
                with self.subTest(version=version):
                    commit = current[version]['commit']
                    self.assertRegex(commit, r'^[a-f0-9]{40}$')
                    self.assertEqual(self.git(p.REPO,'rev-parse','--verify',commit+'^{commit}'),commit)
                    old = json.loads(originals[version])
                    self.bridge.validate_public_blueprint(old,current,version)
                    opened = self.call('draft_open',{'version':version})
                    self.assertEqual(opened['summary']['unresolvedEvidenceIds'],[])
                    self.assertEqual(opened['summary']['staleReferences'],[])
                    self.assertEqual(opened['summary']['baselineCommit'],commit)
                    self.assertEqual(opened['summary']['sourceCommit'],commit)
                    state = p.read_json(self.bridge.session_path(self.runid,'fixture-author',f'draft-{version}.json'))
                    self.assertEqual(state['blueprint'],old)
                    self.assertLess(len(self.bridge.encode_message({'ok':True,'result':opened})),self.bridge.MAX_RESPONSE)
                    with patch.object(p,'write_artifact',side_effect=AssertionError('unchanged must not write')):
                        saved = self.call('draft_finalize',{'version':version,'expectedRevision':opened['revision']})
                    self.assertTrue(saved['saved'])
                    self.assertTrue(saved['unchanged'])
                    self.assertEqual(saved['sha256'],p.digest(originals[version]))
                    self.assertEqual((directory/(version+'.json')).read_bytes(),originals[version])

    def test_fixed_public_source_delta_rebinds_only_unchanged_rust(self):
        import subprocess
        repo = Path(__file__).parents[2]/'.automation/sources/paykit-rs'
        before = '1fdbedd29de28bd174529aa3da3c5b3c0471b1f6'
        after = 'ed42d4e873e0a3ab63c756241679bc8c2b33dca2'
        for commit in (before,after):
            self.assertRegex(commit,r'^[a-f0-9]{40}$')
            self.assertEqual(self.git(repo,'rev-parse','--verify',commit+'^{commit}'),commit)
        self.seed()
        # Synthetic blueprint and minimal test locks, NOT historical published metadata.
        previous = copy.deepcopy(self.lock)
        previous['coming-soon'] = {'commit':before}
        current = copy.deepcopy(previous)
        current['coming-soon'] = {'commit':after}
        bp = self.blueprint()
        bp['sourceCommit'] = before
        bp['evidence'] = []
        cases = [('coming-soon-lib-package','paykit-lib/Cargo.toml',1,14),
                 ('coming-soon-sdk-package','paykit-sdk/Cargo.toml',1,14),
                 ('coming-soon-ffi-package','paykit-ffi/Cargo.toml',1,12),
                 ('coming-soon-sdk-lib','paykit-sdk/Cargo.toml',28,28),
                 ('coming-soon-app-scope','paykit-lib/src/app.rs',7,26)]
        changed = {eid for eid,_,_,_ in cases[:-1]}
        for eid,path,start,end in cases:
            blobs = [subprocess.check_output(['git','show',commit+':'+path],cwd=repo) for commit in (before,after)]
            oids = [self.git(repo,'rev-parse','--verify',commit+':'+path) for commit in (before,after)]
            self.assertEqual(blobs[0] != blobs[1],eid in changed)
            self.assertEqual(oids[0] != oids[1],eid in changed)
            bp['evidence'].append({'id':eid,'version':'coming-soon','commit':before,'path':path,
                'startLine':start,'endLine':end,'excerpt':'\n'.join(blobs[0].decode().splitlines()[start-1:end]),
                'sourceHash':self.bridge.p.digest(blobs[0]),'kind':'package' if eid in changed else 'implemented',
                'claim':'Synthetic fixture cites this canonical public source range.'})
        ids = [e['id'] for e in bp['evidence']]
        for page in bp['pages']:
            page['sections'][0]['evidence'] = ids[:]
            page['sections'][0]['paragraphs'] = ['Synthetic fixture snapshot '+before[:12]]
            page['sourceLinks'] = [{'href':'https://github.com/pubky/paykit-rs/tree/'+before,'label':'Fixture source'}]
        bp['diagrams'] = [{'id':'fixture-flow','mermaid':'flowchart LR\n A --> B',
            'caption':'Synthetic source dependency fixture','alt':'Synthetic fixture dependencies',
            'relationships':[{'text':'Fixture sources','evidence':ids[:]}]}]
        target = self.root/'automation/blueprints/coming-soon.json'
        raw = (json.dumps(bp,ensure_ascii=False,separators=(',',':'))+'\n').encode()
        target.write_bytes(raw)
        (self.root/'automation/sources.lock.json').write_text(json.dumps(previous))
        p = self.bridge.p
        owner = p.read_json(p.CACHE/'run.lock/owner.json')
        owner['sourceHash'] = p.digest(current)
        p.save_json(p.CACHE/'run.lock/owner.json',owner)
        p.save_json(p.CACHE/'runs'/self.runid/'sources.json',current)
        with patch.object(p,'REPO',repo):
            self.bridge.validate_public_blueprint(bp,previous,'coming-soon')
            opened = self.call('draft_open',{'version':'coming-soon'})
            self.assertEqual(set(opened['summary']['unresolvedEvidenceIds']),changed)
            self.assertEqual(len(opened['summary']['unresolvedEvidenceIds']),4)
            self.assertEqual(opened['summary']['baselineCommit'],before)
            self.assertEqual(opened['summary']['sourceCommit'],after)
            state = p.read_json(self.bridge.session_path(self.runid,'fixture-author','draft-coming-soon.json'))
            expected = copy.deepcopy(bp)
            expected['sourceCommit'] = after
            expected['evidence'][-1]['commit'] = after
            self.assertEqual(state['blueprint'],expected)
            self.assertEqual(state['blueprint']['pages'],bp['pages'])
            self.assertEqual(state['blueprint']['diagrams'],bp['diagrams'])
            self.assertTrue(opened['summary']['staleReferences'])
            self.assertEqual({d['evidenceId'] for d in opened['summary']['dependencyChanges']},changed)
            self.assertTrue(all(d['sections'] and d['diagrams'] == ['fixture-flow'] for d in opened['summary']['dependencyChanges']))
            self.assertLess(len(self.bridge.encode_message({'ok':True,'result':opened})),self.bridge.MAX_RESPONSE)
            for record in bp['evidence'][:-1]:
                with self.assertRaises(ValueError):
                    self.call('draft_edit',{'version':'coming-soon','expectedRevision':opened['revision'],
                        'edits':[{'op':'putEvidence','record':dict(record,commit=after)}]})
                self.assertEqual(self.call('draft_open',{'version':'coming-soon'}),opened)
            # Remove stale presentation independently: changed blobs must still block finalization.
            edits = []
            for page in bp['pages']:
                section = copy.deepcopy(page['sections'][0])
                section['paragraphs'] = ['Synthetic fixture snapshot '+after[:12]]
                edits.extend([{'op':'replaceSection','pageSlug':page['slug'],'sectionId':'fixture','section':section},
                    {'op':'replaceSourceLinks','pageSlug':page['slug'],
                     'sourceLinks':[{'href':'https://github.com/pubky/paykit-rs/tree/'+after,'label':'Fixture source'}]}])
            opened = self.call('draft_edit',{'version':'coming-soon','expectedRevision':opened['revision'],'edits':edits})
            self.assertEqual(opened['summary']['staleReferences'],[])
            self.assertEqual(set(opened['summary']['unresolvedEvidenceIds']),changed)
            with patch.object(p,'write_artifact',side_effect=AssertionError('unresolved must not write')):
                with self.assertRaisesRegex(ValueError,'Unresolved draft evidence'):
                    self.call('draft_finalize',{'version':'coming-soon','expectedRevision':opened['revision']})
            self.assertEqual(target.read_bytes(),raw)

    def test_interrupted_receipt_recovers_only_own_matching_target(self):
        bp, original = self.seed()
        opened = self.call('draft_open',{'version':'released'})
        section = dict(bp['pages'][0]['sections'][0],paragraphs=['Changed'])
        changed = self.call('draft_edit',{'version':'released','expectedRevision':opened['revision'],'edits':[{'op':'replaceSection','pageSlug':'overview','sectionId':'fixture','section':section}]})
        real = self.bridge.p.save_json
        def interrupt(path, value):
            if Path(path).name == 'draft-released.json' and value.get('receipt'):
                raise OSError('fixture interruption after target replacement')
            return real(path,value)
        with patch.object(self.bridge.p,'save_json',interrupt),self.assertRaises(OSError):
            self.call('draft_finalize',{'version':'released','expectedRevision':changed['revision']})
        result = self.call('draft_finalize',{'version':'released','expectedRevision':changed['revision']})
        self.assertTrue(result['saved'])
        target = self.root/'automation/blueprints/released.json'
        target.write_bytes(original)
        with self.assertRaises(ValueError):
            self.call('draft_finalize',{'version':'released','expectedRevision':changed['revision']})
        target.write_bytes(target.read_bytes()+b' ')
        with self.assertRaises(ValueError):
            self.call('draft_finalize',{'version':'released','expectedRevision':changed['revision']})

    def test_roles_selectors_expired_lease_frozen_hash_and_acceptance_fail_closed(self):
        self.seed()
        opened = self.call('draft_open',{'version':'released'})
        for role in ('analyst','diagram','reviewer','walkthrough'):
            for op, args in [('draft_open',{'version':'released'}),('draft_read',{'version':'released','revision':opened['revision'],'selector':{'kind':'summary'},'offset':0,'limit':10}),('draft_edit',{'version':'released','expectedRevision':opened['revision'],'edits':[]}),('draft_finalize',{'version':'released','expectedRevision':opened['revision']})]:
                with self.subTest(role=role,op=op),self.assertRaises(ValueError): self.call(op,args,role=role,session='other-'+role)
        for selector in ({'kind':'summary','path':'.env'},{'kind':'section','pageSlug':'overview','sectionId':'fixture','command':'pwd'},{'kind':'evidence','evidenceId':'../x'},{'kind':'path','path':'AGENTS.md'}):
            with self.assertRaises(ValueError):self.call('draft_read',{'version':'released','revision':opened['revision'],'selector':selector,'offset':0,'limit':6000})
        for offset,limit in [(-1,1),(0,6001),(True,1),(0,False)]:
            with self.assertRaises(ValueError):self.call('draft_read',{'version':'released','revision':opened['revision'],'selector':{'kind':'summary'},'offset':offset,'limit':limit})
        with self.assertRaises(ValueError): self.call('read_inputs',{'path':'.automation/runs/'+self.runid+'/bridge/fixture-author/draft-released.json'})
        p=self.bridge.p; owner=p.read_json(p.CACHE/'run.lock/owner.json')
        for mutation in ({'deadline':0},{'sourceHash':'0'*64},{'acceptanceHash':'0'*64}):
            p.save_json(p.CACHE/'run.lock/owner.json',dict(owner,**mutation))
            with self.assertRaises(ValueError):self.call('draft_finalize',{'version':'released','expectedRevision':opened['revision']})
            p.save_json(p.CACHE/'run.lock/owner.json',owner)


    def test_competing_sessions_and_legacy_writer_cannot_overwrite_stale_baseline(self):
        import multiprocessing
        import queue
        bp, raw = self.seed()
        first = self.call('draft_open',{'version':'released'})
        second = self.call('draft_open',{'version':'released'},session='other-author')
        changed = copy.deepcopy(bp); changed['pages'][0]['sections'][0]['paragraphs']=['Legacy writer']
        context = multiprocessing.get_context('fork')
        messages = context.Queue()
        def legacy():
            messages.put('started')
            try:
                self.call('submit_blueprint',{'version':'released','blueprint':changed},session='legacy-author')
                messages.put('saved')
            except Exception as error: messages.put(repr(error))
        with self.bridge.draft_lock(self.runid):
            process = context.Process(target=legacy)
            process.start()
            self.assertEqual(messages.get(timeout=3),'started')
            with self.assertRaises(queue.Empty):messages.get(timeout=.2)
            self.assertEqual((self.root/'automation/blueprints/released.json').read_bytes(),raw)
        process.join(timeout=10)
        if process.is_alive(): process.kill(); process.join(); self.fail('shared write lock deadlocked')
        self.assertEqual(messages.get(timeout=3),'saved')
        for session, opened in [('fixture-author',first),('other-author',second)]:
            with self.assertRaises(ValueError):self.call('draft_finalize',{'version':'released','expectedRevision':opened['revision']},session=session)
        self.assertEqual(json.loads((self.root/'automation/blueprints/released.json').read_bytes()),changed)

    def test_current_snapshot_baseline_uses_frozen_lock_and_unknown_commit_fails(self):
        bp, _ = self.seed()
        self.advance()
        bp['sourceCommit'] = self.current
        bp['evidence'][0].update(commit=self.current,excerpt='NEW PUBLIC CONTRACT',sourceHash=self.bridge.p.digest(b'NEW PUBLIC CONTRACT\nline two\n'))
        target = self.root/'automation/blueprints/released.json'
        target.write_text(json.dumps(bp))
        opened = self.call('draft_open',{'version':'released'})
        self.assertEqual(opened['summary']['unresolvedEvidenceIds'],[])
        self.assertEqual(opened['summary']['baselineCommit'],self.current)
        bp['sourceCommit']='0'*40
        target.write_text(json.dumps(bp))
        with self.assertRaises(ValueError):self.call('draft_open',{'version':'released'},session='unapproved-baseline')


if __name__ == '__main__':
    unittest.main()
