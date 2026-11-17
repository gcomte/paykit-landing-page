"""Offline bridge fixtures only: no live Paykit, Cargo, Next or publication."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import zlib
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location("fixture_host_bridge", Path(__file__).parents[1] / "host_bridge.py")


class BridgeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bridge = None
        if Path(SPEC.origin).exists():
            cls.bridge = importlib.util.module_from_spec(SPEC)
            SPEC.loader.exec_module(cls.bridge)

    def test_01_bridge_exists_and_codec_is_bounded_and_strict(self):
        self.assertIsNotNone(self.bridge, "narrow public bridge is missing")
        b = self.bridge
        obj = {"op": "bootstrap", "args": {}}
        self.assertEqual(b.decode_message(b.encode_message(obj)), obj)
        for raw in (b'{"op":"a","op":"b"}', b'{"x":NaN}', b'[]', b'{} trailing'):
            with self.assertRaises(ValueError):
                b.decode_message('pkhex1:' + zlib.compress(raw).hex())
        with self.assertRaises(ValueError):
            b.decode_message('pkhex1:' + zlib.compress(b'x' * 1048577).hex())
        with self.assertRaises(ValueError):
            b.decode_message('$(cat /proc/self/environ)')

    def setUp(self):
        if self.bridge is None:
            return
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source = self.root / '.automation/sources/paykit-rs'
        self.source.mkdir(parents=True)
        self.git(self.source, 'init', '-q')
        self.git(self.source, 'config', 'user.email', 'fixture@example.invalid')
        self.git(self.source, 'config', 'user.name', 'Fixture')
        (self.source / 'README.md').write_text('PUBLIC FIXTURE\nline two\n')
        (self.source / '.env').write_text('SECRET_FIXTURE')
        (self.source / 'escape').symlink_to('/proc/self/environ')
        self.git(self.source, 'add', '.')
        self.git(self.source, 'commit', '-qm', 'public fixture snapshot')
        self.commit = self.git(self.source, 'rev-parse', 'HEAD')
        (self.source / 'outside.txt').write_text('not frozen')
        self.git(self.root, 'init', '-q')
        (self.root / '.gitignore').write_text('.automation/\n')
        (self.root / 'AGENTS.md').write_text('Public instructions')
        (self.root / 'unrelated.txt').write_text('PRIVATE_CANARY')
        (self.root / 'automation').mkdir()
        config = {'canonicalRepository':'pubky/paykit-rs', 'sections':{'Understand Paykit':['overview'], 'Integrate Paykit':['wallets']}}
        (self.root / 'automation/config.json').write_text(json.dumps(config))
        p = self.bridge.p
        for name, value in {'ROOT':self.root, 'CACHE':self.root/'.automation', 'REPO':self.source, 'CONFIG':self.root/'automation/config.json'}.items():
            patcher = patch.object(p, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        record = p.acquire_lease()
        self.runid = record['id']
        self.lock = {'repository':'pubky/paykit-rs', 'released':{'commit':self.commit}, 'coming-soon':{'commit':self.commit}}
        record.update(acceptanceHash=p.acceptance_digest(), sourceHash=p.digest(self.lock))
        p.save_json(p.CACHE/'run.lock/owner.json', record)
        p.save_json(p.CACHE/'runs'/self.runid/'sources.json', self.lock)

    def git(self, root, *args):
        return subprocess.check_output(['git', *args], cwd=root, text=True, stderr=subprocess.DEVNULL).strip()

    def call(self, op, args=None, role='author', session='fixture-author'):
        self.assertTrue(hasattr(self.bridge, 'dispatch'), 'capability dispatcher is missing')
        return self.bridge.dispatch(self.runid, role, session, {'op':op, 'args':args or {}})

    def test_public_frozen_source_is_readable_but_private_paths_are_not(self):
        self.assertEqual(self.call('read_source', {'version':'released','path':'README.md'})['text'], 'PUBLIC FIXTURE\nline two\n')
        for path in ('.env', 'escape', 'outside.txt', '../README.md', '/proc/self/environ', '.git/config', 'x/../../README.md', './README.md', 'README.md/'):
            with self.subTest(path=path), self.assertRaises(ValueError):
                self.call('read_source', {'version':'released','path':path})
        inventory = self.call('source_inventory', {'version':'released'})['items']
        self.assertEqual(inventory, ['README.md'])

    def test_unknown_fields_roles_and_arbitrary_operations_are_denied(self):
        for op,args,role in [('read_source',{'version':'released','path':'README.md','commit':'0'*40},'author'), ('shell',{'command':'pwd'},'author'), ('submit_blueprint',{},'reviewer'), ('read_inputs',{'path':'AGENTS.md'},'walkthrough')]:
            with self.subTest(op=op), self.assertRaises(ValueError):
                self.call(op,args,role)
        with self.assertRaises(ValueError):
            self.bridge.dispatch(self.runid,'author','fixture-author',{'op':'bootstrap','args':{},'summary':'PRIVATE_CANARY'})

    def test_site_reader_has_publication_scope_and_symlink_boundaries(self):
        self.assertEqual(self.call('read_inputs', {'path':'AGENTS.md'})['text'], 'Public instructions')
        (self.root/'automation/leak.txt').symlink_to(self.root/'unrelated.txt')
        (self.root/'automation/.env').write_text('PRIVATE_CANARY')
        for path in ('unrelated.txt','.automation/run.lock/owner.json','automation/leak.txt','automation/.env','/proc/self/environ'):
            with self.subTest(path=path), self.assertRaises(ValueError):
                self.call('read_inputs',{'path':path})
        public = json.dumps(self.call('bootstrap'))
        self.assertNotIn('PRIVATE_CANARY',public)
        self.assertNotIn('unrelated.txt',public)
        self.assertNotIn('leak.txt',public)

    def test_bootstrap_exports_only_issued_current_run_public_review_feedback(self):
        p=self.bridge.p
        report={'status':'fail','artifactHash':p.artifact_digest(),'findings':['Public fixture claim needs a contract anchor'],
                'versions':['released','coming-soon'],'notes':'Public source review fixture'}
        self.call('submit_review',{'report':report},role='reviewer',session='fixture-reviewer')
        result=self.call('bootstrap')
        self.assertIn('repairFeedback',result,'Public review feedback is not exported for bounded repairs')
        exported=result['repairFeedback']
        self.assertEqual(exported['findings'],report['findings'])
        self.assertNotIn('reviewerRunId',exported)
        self.assertNotIn('PRIVATE_CANARY',json.dumps(exported))
        changed=p.read_json(p.CACHE/'review.json');changed['notes']='PRIVATE_CANARY'
        p.save_json(p.CACHE/'review.json',changed)
        with self.assertRaises(ValueError):self.call('bootstrap')

    def blueprint(self):
        text = 'PUBLIC FIXTURE\nline two\n'
        eid = 'released-fixture'
        ev = {'id':eid,'version':'released','commit':self.commit,'path':'README.md','startLine':1,'endLine':1,'excerpt':'PUBLIC FIXTURE','sourceHash':self.bridge.p.digest(text.encode()),'kind':'implemented','claim':'Public fixture claim'}
        return {'sourceCommit':self.commit, 'evidence':[ev], 'diagrams':[], 'pages':[{'slug':slug,'title':slug,'description':'Fixture page','group':group,'kind':'tutorial','sections':[{'id':'fixture','title':'Fixture','evidence':[eid],'paragraphs':['Public fixture']}], 'sourceLinks':[]} for group,slug in [('Understand Paykit','overview'),('Integrate Paykit','wallets')]]}

    def test_blueprint_submission_checks_frozen_evidence_and_only_writes_blueprint(self):
        bp = self.blueprint()
        result = self.call('submit_blueprint', {'version':'released','blueprint':bp})
        self.assertTrue(result['saved'])
        self.assertEqual(json.loads((self.root/'automation/blueprints/released.json').read_text()), bp)
        self.assertFalse((self.root/'content').exists())
        self.assertFalse((self.root/'public').exists())
        for mutate in (lambda x:x.update(summary='PRIVATE_CANARY'), lambda x:x['evidence'][0].update(path='.env'), lambda x:x['evidence'][0].update(commit='0'*40), lambda x:x['evidence'][0].update(excerpt='invented'), lambda x:x['pages'][0]['sections'][0].update(code=[{'language':'text','artifact':'../../.env','caption':'bad'}])):
            bad = json.loads(json.dumps(bp))
            mutate(bad)
            with self.assertRaises(ValueError):
                self.call('submit_blueprint',{'version':'released','blueprint':bad})
        self.assertEqual(json.loads((self.root/'automation/blueprints/released.json').read_text()),bp)

    def test_diagram_role_cannot_change_prose_or_review_itself(self):
        bp = self.blueprint()
        self.call('submit_blueprint', {'version':'released','blueprint':bp})
        bp['pages'][0]['title'] = 'altered prose'
        with self.assertRaises(ValueError):
            self.call('submit_blueprint',{'version':'released','blueprint':bp},role='diagram',session='fixture-diagram')
        with self.assertRaises(ValueError):
            self.call('bootstrap',role='reviewer',session='fixture-author')

    def test_review_is_artifact_and_trusted_session_bound(self):
        report = {'status':'pass','artifactHash':self.bridge.p.artifact_digest(),'findings':[], 'versions':['released','coming-soon'],'notes':'All fixture claims inspected'}
        result = self.call('submit_review',{'report':report},role='reviewer',session='fixture-reviewer')
        self.assertTrue(result['saved'])
        saved = self.bridge.p.read_json(self.root/'.automation/review.json')
        self.assertEqual(saved['reviewerRunId'],'fixture-reviewer')
        self.assertEqual(saved['runId'],self.runid)
        for extra in ({'artifactHash':'0'*64}, {'reviewerRunId':'forged'}, {'status':'pass','findings':['Unsupported fixture claim']}):
            with self.assertRaises(ValueError):
                self.call('submit_review',{'report':{**report,**extra}},role='reviewer',session='fixture-reviewer')
        (self.root/'AGENTS.md').write_text('Changed candidate')
        with self.assertRaises(ValueError):
            self.call('submit_review',{'report':report},role='reviewer',session='fixture-reviewer')

    def test_rendered_guide_parser_ignores_scripts_and_preserves_commands(self):
        self.assertTrue(hasattr(self.bridge,'parse_guide'),'rendered guide reader missing')
        html = '<script>PRIVATE_CANARY</script><main data-doc-version="released"><h1>Fixture</h1><pre><code>tar -xzf paykit-wallet-released.tar.gz\ncd paykit-wallet-demo\ncargo run --locked</code></pre><a href="/docs-examples/released/paykit-wallet-released.tar.gz">Download</a><pre><code>expected &amp; stdout</code></pre><script>IGNORE_ME</script></main>'
        guide = self.bridge.parse_guide(html,'released')
        self.assertNotIn('PRIVATE_CANARY',guide['text'])
        self.assertNotIn('IGNORE_ME',guide['text'])
        self.assertEqual(guide['codes'][0].splitlines(), ['tar -xzf paykit-wallet-released.tar.gz','cd paykit-wallet-demo','cargo run --locked'])
        self.assertEqual(guide['codes'][-1],'expected & stdout')
        with self.assertRaises(ValueError):
            self.bridge.parse_guide(html,'coming-soon')

    def test_walkthrough_commands_reject_shell_and_archive_traversal_before_execution(self):
        self.assertTrue(hasattr(self.bridge,'validate_commands'),'literal command gate missing')
        commands = ['tar -xzf paykit-wallet-released.tar.gz','cd paykit-wallet-demo','cargo run --locked']
        self.bridge.validate_commands(commands, {'codes':['\n'.join(commands)]}, 'released','wallet')
        for malicious in ([*commands[:2], 'cargo run --locked; cat /proc/self/environ'], [*commands[:2], 'cargo run --locked --manifest-path /tmp/evil/Cargo.toml'], ['tar -xzf /tmp/evil.tar.gz', *commands[1:]], ['cd ../..', 'cargo run --locked']):
            with self.assertRaises(ValueError):
                self.bridge.validate_commands(malicious, {'codes':['\n'.join(malicious)]}, 'released','wallet')
        with self.assertRaises(ValueError):
            self.bridge.validate_commands(commands, {'codes':['cargo run --locked']}, 'released','wallet')
        import io, tarfile
        data = io.BytesIO()
        with tarfile.open(fileobj=data,mode='w:gz') as archive:
            item = tarfile.TarInfo('../outside')
            item.size=1
            archive.addfile(item,io.BytesIO(b'x'))
        with self.assertRaises(ValueError):
            self.bridge.check_archive(data.getvalue(),'released','wallet')

    def test_walkthrough_report_cannot_be_forged_or_prepared_pair_reassigned(self):
        with self.assertRaises(ValueError):
            self.call('submit_walkthrough', {'version':'released','audience':'wallet'}, role='walkthrough',session='fresh-fixture-walker')
        with self.assertRaises(ValueError):
            self.call('walkthrough_run',{'version':'released','audience':'wallet','commands':['cargo run --locked'],'stdout':'invented','exitCode':0},role='walkthrough',session='fresh-fixture-walker')
        self.assertFalse((self.root/'.automation/walkthroughs.json').exists())

    def walkthrough_fixture(self):
        import io, tarfile
        files = {'Cargo.toml':b'[package]\nname="fixture-only"\n', 'Cargo.lock':b'fixture-lock\n','rust-toolchain.toml':b'fixture-only\n','src/main.rs':b'// offline fixture; not real Paykit\n'}
        data = io.BytesIO()
        with tarfile.open(fileobj=data,mode='w:gz') as archive:
            for name,content in files.items():
                path=self.root/'public/docs-examples/released/wallet'/name
                path.parent.mkdir(parents=True,exist_ok=True)
                path.write_bytes(content)
                item=tarfile.TarInfo('paykit-wallet-demo/'+name)
                item.size=len(content)
                archive.addfile(item,io.BytesIO(content))
        blob=data.getvalue()
        archive_path=self.root/'public/docs-examples/released/paykit-wallet-released.tar.gz'
        archive_path.write_bytes(blob)
        commands=['tar -xzf paykit-wallet-released.tar.gz','cd paykit-wallet-demo','cargo run --locked']
        expected=self.bridge.p.EXPECTED['wallet']
        html='<main data-doc-version="released"><p>'+self.commit+'</p><pre><code>'+'\n'.join(commands)+'</code></pre><a href="/docs-examples/released/paykit-wallet-released.tar.gz">Download</a><pre><code>'+expected+'</code></pre></main>'
        (self.root/'content/docs').mkdir(parents=True,exist_ok=True)
        doc={'pages':[{'slug':'wallets','title':self.commit,'description':self.commit,'sections':[{'title':self.commit,'code':[{'code':'\n'.join(commands)},{'code':expected}]}]}]}
        (self.root/'content/docs/released.json').write_text(json.dumps(doc))
        return html,blob,commands

    def test_walkthrough_offline_fixture_executes_tar_and_records_only_actual_results(self):
        import contextlib
        html,blob,commands=self.walkthrough_fixture()
        b=self.bridge
        self.assertTrue(hasattr(b,'prepare_walkthrough'),'walkthrough execution capability missing')
        @contextlib.contextmanager
        def fixture_server(runid):
            yield 'http://127.0.0.1:3174'
        def fixture_fetch(url,**kwargs):
            return blob if url.endswith('.tar.gz') else html.encode()
        with patch.object(b,'production_server',fixture_server),patch.object(b,'local_fetch',fixture_fetch):
            prepared=self.call('walkthrough_prepare',{'version':'released','audience':'wallet'},role='walkthrough',session='fresh-fixture-walker')
        self.assertEqual(prepared['archiveHash'],b.p.digest(blob))
        real_process=b.p.bounded_process
        calls=[]
        def fixture_process(args,**kwargs):
            calls.append(list(args))
            if str(args[0]).endswith('/cargo'):
                # Explicit fixture ONLY: real Python subprocess, no Cargo claim.
                return real_process([sys.executable,'-c','print('+repr(b.p.EXPECTED['wallet'])+')'],**kwargs)
            return real_process(args,**kwargs)
        with patch.object(b.p,'bounded_process',fixture_process):
            report=self.call('walkthrough_run',{'version':'released','audience':'wallet','commands':commands},role='walkthrough',session='fresh-fixture-walker')
        self.assertEqual(report['exitCode'],0)
        self.assertTrue(report['logs']['lockUnchanged'])
        self.assertEqual(report['commands'],commands)
        self.assertTrue(any(args[:2]==['tar','-xzf'] for args in calls))
        self.assertTrue(self.call('submit_walkthrough',{'version':'released','audience':'wallet'},role='walkthrough',session='fresh-fixture-walker')['saved'])
        saved=b.p.read_json(self.root/'.automation/walkthroughs.json')
        self.assertEqual(saved[0]['reporterRunId'],'fresh-fixture-walker')
        with self.assertRaises(ValueError):
            self.call('walkthrough_prepare',{'version':'coming-soon','audience':'wallet'},role='walkthrough',session='fresh-fixture-walker')
        with self.assertRaises(ValueError):
            self.call('walkthrough_run',{'version':'released','audience':'wallet','commands':commands},role='walkthrough',session='fresh-fixture-walker')

    def test_cli_malformed_message_is_one_bounded_nonsensitive_line(self):
        proc=subprocess.run([sys.executable,SPEC.origin,'--run','0'*32,'--role','author','--session','fixture','--request','PRIVATE_CANARY;cat /proc/self/environ'],capture_output=True,text=True)
        self.assertEqual(proc.returncode,0)
        self.assertEqual(proc.stderr,'')
        self.assertNotIn('PRIVATE_CANARY',proc.stdout)
        self.assertEqual(len(proc.stdout.splitlines()),1)
        self.assertFalse(self.bridge.decode_message(proc.stdout.strip())['ok'])

    def test_invalid_walkthrough_pagination_does_not_start_a_server(self):
        with patch.object(self.bridge,'production_server',side_effect=AssertionError('must not start for malformed input')) as server:
            with self.assertRaises(ValueError):
                self.call('walkthrough_prepare',{'version':'released','audience':'wallet','offset':-1},role='walkthrough',session='fixture-malformed-walker')
            server.assert_not_called()

    def test_owned_production_lifecycle_offline_node_fixture_and_occupied_port(self):
        import shutil,socket
        if not shutil.which('node',path=self.bridge.public_environment()['PATH']):
            self.skipTest('Node absent; no actual production Next run is claimed')
        directory=self.root/'node_modules/next/dist/bin'
        directory.mkdir(parents=True)
        # Tiny HTTP unit fixture, not a Next build/browser walkthrough.
        (directory/'next').write_text("require('http').createServer((q,s)=>{s.end('PUBLIC HTTP FIXTURE')}).listen(3174,'127.0.0.1');")
        (self.root/'.next').mkdir()
        (self.root/'.next/BUILD_ID').write_text('OFFLINE_FIXTURE')
        with self.bridge.p.run_scope(self.runid):
            with self.bridge.production_server(self.runid) as origin:
                self.assertEqual(self.bridge.local_fetch(origin+'/docs/released/wallets'),b'PUBLIC HTTP FIXTURE')
            self.assertFalse(self.bridge.p.read_json(self.root/'.automation/run.lock/owner.json')['children'])
            with socket.socket() as blocker:
                blocker.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
                blocker.bind(('127.0.0.1',3174))
                blocker.listen()
                with self.assertRaises(OSError):
                    with self.bridge.production_server(self.runid):
                        self.fail('attached to unowned server')

    def test_subprocess_environment_does_not_forward_credentials(self):
        with patch.dict('os.environ',{'GITHUB_TOKEN':'PRIVATE_CANARY','OPENAI_API_KEY':'PRIVATE_CANARY','TALOS_SECRET':'PRIVATE_CANARY'}):
            env=self.bridge.public_environment(self.root/'fixture-home')
        self.assertNotIn('PRIVATE_CANARY',json.dumps(env))
        result=self.bridge.p.bounded_process([sys.executable,'-c','import os; print(os.getcwd()); print(os.getenv("GITHUB_TOKEN","absent"))'],cwd=self.root,env=env,timeout=3)
        self.assertEqual(result.stdout.splitlines(),[str(self.root),'absent'])

    def test_server_registration_failure_still_stops_new_owned_process_group(self):
        import shutil
        if not shutil.which('node',path=self.bridge.public_environment()['PATH']):
            self.skipTest('Node unavailable for offline lifecycle fixture')
        (self.root/'.next').mkdir()
        (self.root/'.next/BUILD_ID').write_text('OFFLINE_FIXTURE')
        real_popen=subprocess.Popen
        processes=[]
        def harmless_child(*args,**kwargs):
            proc=real_popen([sys.executable,'-c','import time; time.sleep(30)'],**kwargs)
            processes.append(proc)
            return proc
        real_save=self.bridge.p.save_json
        def fail_registration(path,value):
            if str(path).endswith('run.lock/owner.json'):
                raise OSError('offline fixture registration failure')
            return real_save(path,value)
        try:
            with patch.object(subprocess,'Popen',harmless_child),patch.object(self.bridge.p,'save_json',fail_registration):
                with self.assertRaises(OSError):
                    with self.bridge.production_server(self.runid):
                        self.fail('registration failure must not yield')
            self.assertTrue(processes)
            self.assertIsNotNone(processes[0].poll(),'spawned process escaped finally on registration failure')
        finally:
            for proc in processes:
                if proc.poll() is None:
                    self.bridge.p.stop_group(proc.pid)
                proc.wait(timeout=3)

    def test_tracked_but_ignored_publication_file_is_not_a_public_input(self):
        private=self.root/'public/docs-assets/released/private.svg'
        private.parent.mkdir(parents=True)
        private.write_text('PRIVATE_CANARY')
        (self.root/'.gitignore').write_text('.automation/\npublic/docs-assets/released/private.svg\n')
        self.git(self.root,'add','-f','public/docs-assets/released/private.svg')
        self.assertNotIn('public/docs-assets/released/private.svg',self.call('bootstrap')['inputPaths'])
        with self.assertRaises(ValueError):
            self.call('read_inputs',{'path':'public/docs-assets/released/private.svg'})


if __name__ == '__main__':
    unittest.main()
