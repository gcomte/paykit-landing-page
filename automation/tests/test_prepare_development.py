"""Synthetic public HTTP fixtures only; no SDK execution or live-source claims."""
import hashlib
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import urllib.error
import urllib.response
from email.message import Message

sys.path.insert(0, str(Path(__file__).parents[1]))

SCRIPT = Path(__file__).parents[1] / 'prepare_development.py'
OLD, NEW, OBJECT = 'a' * 40, 'b' * 40, 'c' * 40
API = 'https://api.github.com/repos/pubky/paykit-rs'
RAW = 'https://raw.githubusercontent.com/pubky/paykit-rs'
PACKAGES = ('paykit-lib', 'paykit-sdk', 'paykit-ffi')


class Response(io.BytesIO):
    status = 200
    headers = {}


class PrepareDevelopmentTests(unittest.TestCase):
    def fixture(self, kind='tag', ref='synthetic/v-next'):
        self.assertTrue(SCRIPT.is_file(), 'Public-source preparation command is missing')
        spec = importlib.util.spec_from_file_location('prepare_development', SCRIPT)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        temporary = tempfile.TemporaryDirectory(prefix='synthetic-preparation-')
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name) / 'site'
        (root / 'automation/blueprints').mkdir(parents=True)
        (root / 'content/docs').mkdir(parents=True)
        source = {'repository': 'pubky/paykit-rs', 'kind': kind, 'ref': ref}
        (root / 'docs-generation.json').write_text(json.dumps({'source': source}))
        (root / 'content/docs/coming-soon.json').write_text(json.dumps({
            'version': {'commit': OLD}, 'lastUpdated': '2000-01-01'}))
        (root / 'automation/blueprints/coming-soon.json').write_text(json.dumps({
            'evidence': [{'path': 'paykit-lib/src/synthetic.rs'}]}))
        namespace = 'tags' if kind == 'tag' else 'heads'
        self.qualified = f'refs/{namespace}/{ref}'
        self.ref_url = API + '/git/ref/' + namespace + '/' + ref
        self.compare_url = API + f'/compare/{OLD}...{NEW}'
        self.replies = {
            self.ref_url: json.dumps({'ref': self.qualified, 'object': {
                'type': 'tag' if kind == 'tag' else 'commit', 'sha': OBJECT if kind == 'tag' else NEW}}).encode(),
            API + '/git/tags/' + OBJECT: json.dumps({'sha': OBJECT, 'object': {'type': 'commit', 'sha': NEW}}).encode(),
            self.compare_url: json.dumps({'base_commit': {'sha': OLD}, 'files': [
                {'filename': 'docs/synthetic-change.md', 'status': 'modified'},
                {'filename': 'vectors/huge.json', 'status': 'modified'},
                {'filename': 'deleted.rs', 'status': 'removed'}]}).encode(),
        }
        self.files = {p: ('Synthetic public fixture: ' + p + '\n').encode() for p in (
            'README.md', 'CHANGELOG.md', 'skills/paykit/SKILL.md',
            'paykit-lib/src/synthetic.rs', 'docs/synthetic-change.md')}
        for number, package in enumerate(PACKAGES, 1):
            self.files[package + '/Cargo.toml'] = (
                f'[package]\nname = "{package}"\nversion = "0.0.{number}-synthetic"\n'
                'rust-version = "1.80"\n[dependencies]\nfixture = { version = "1", features = ["synthetic"] }\n').encode()
            url = f'https://crates.io/api/v1/crates/{package}/0.0.{number}-synthetic'
            self.replies[url] = urllib.error.HTTPError(url, 404, 'Synthetic missing', {}, io.BytesIO(b'{"errors":[{"detail":"synthetic missing"}]}'))
        self.replies.update({f'{RAW}/{NEW}/{path}': data for path, data in self.files.items()})
        self.previous = b'[package]\nname = "paykit-lib"\nversion = "0.0.0-synthetic"\n'
        self.replies[f'{RAW}/{OLD}/paykit-lib/Cargo.toml'] = self.previous
        self.calls = []
        def http(request, timeout):
            self.assertEqual(timeout, 30)
            self.assertNotIn('Authorization', request.headers)
            self.calls.append(request.full_url)
            response = self.replies[request.full_url]
            if isinstance(response, Exception):
                raise response
            if isinstance(response, tuple):
                status, body = response
                response = Response(body)
                response.status = status
                return response
            return Response(response)
        self.http = http
        return root, Path(temporary.name) / 'frozen', module

    def prepare(self, module, root, output, commit=NEW):
        with patch.object(module.urllib.request.OpenerDirector, 'open', side_effect=self.http):
            return module.prepare_development(root, output, commit)

    def test_annotated_tag_freezes_renderer_inputs_with_exact_hashes(self):
        root, output, module = self.fixture()
        before = {p.relative_to(root): p.read_bytes() for p in root.rglob('*') if p.is_file()}
        self.prepare(module, root, output)
        index = json.loads((output / 'index.json').read_bytes())
        self.assertEqual({k: index[k] for k in ('repository', 'kind', 'ref', 'commit', 'previousCommit')}, {
            'repository': 'pubky/paykit-rs', 'kind': 'tag', 'ref': 'synthetic/v-next', 'commit': NEW, 'previousCommit': OLD})
        self.assertEqual({k: index['version'][k] for k in ('label', 'ref', 'tag', 'kind', 'qualifiedRef', 'object', 'commit', 'sourceUrl')}, {
            'label': 'synthetic/v-next', 'ref': 'synthetic/v-next', 'tag': 'synthetic/v-next', 'kind': 'tag',
            'qualifiedRef': self.qualified, 'object': OBJECT, 'commit': NEW,
            'sourceUrl': f'https://github.com/pubky/paykit-rs/tree/{NEW}'})
        self.assertNotIn('branch', index['version'])
        self.assertNotIn('lastUpdated', index)
        self.assertNotIn('lastUpdated', index['version'])
        self.assertNotIn('readiness', index['version'])
        rows = {row['path']: row for row in index['files']}
        self.assertEqual(set(rows), set(self.files))
        for path, data in self.files.items():
            self.assertEqual((output / path).read_bytes(), data)
            self.assertEqual(rows[path], {'path': path, 'url': f'{RAW}/{NEW}/{path}',
                'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()})
        self.assertEqual((output / 'previous/paykit-lib/Cargo.toml').read_bytes(), self.previous)
        self.assertEqual(index['previousLibraryManifest']['commit'], OLD)
        self.assertEqual(index['previousLibraryManifest']['sha256'], hashlib.sha256(self.previous).hexdigest())
        for number, package in enumerate(PACKAGES, 1):
            self.assertEqual(index['version']['packages'][package], {'version': f'0.0.{number}-synthetic',
                'rustVersion': '1.80', 'dependencies': {'fixture': {'version': '1', 'features': ['synthetic']}}, 'published': False})
        for name, url in (('ref.json', self.ref_url), ('tag.json', API + '/git/tags/' + OBJECT), ('compare.json', self.compare_url)):
            self.assertEqual((output / name).read_bytes(), self.replies[url])
        observations = json.loads((output / 'registry-observations.json').read_bytes())
        self.assertEqual([row['status'] for row in observations], [404, 404, 404])
        for row in observations:
            self.assertIn(b'synthetic missing', (output / row['bodyPath']).read_bytes())
        self.assertEqual(before, {p.relative_to(root): p.read_bytes() for p in root.rglob('*') if p.is_file()})

    def test_moved_ref_is_rejected_before_source_downloads_or_writes(self):
        root, output, module = self.fixture()
        self.replies[API + '/git/tags/' + OBJECT] = json.dumps({
            'sha': OBJECT, 'object': {'type': 'commit', 'sha': 'd' * 40}}).encode()
        with self.assertRaisesRegex(ValueError, 'moved|expected commit'):
            self.prepare(module, root, output)
        self.assertFalse(output.exists())
        self.assertEqual(self.calls, [self.ref_url, API + '/git/tags/' + OBJECT])

    def test_bad_literal_ref_is_not_normalized_or_looked_up(self):
        root, output, module = self.fixture(ref='synthetic//v-next')
        with self.assertRaisesRegex(ValueError, 'Invalid source ref'):
            self.prepare(module, root, output)
        self.assertEqual(self.calls, [])
        self.assertFalse(output.exists())

    def test_invalid_selection_identity_is_rejected_before_http(self):
        for field, value in (('repository', 'other/paykit-rs'), ('kind', 'revision')):
            with self.subTest(field=field):
                root, output, module = self.fixture()
                path = root / 'docs-generation.json'
                config = json.loads(path.read_bytes())
                config['source'][field] = value
                path.write_text(json.dumps(config))
                self.replies[API + '/git/ref/heads/synthetic/v-next'] = self.replies[self.ref_url]
                with self.assertRaisesRegex(ValueError, 'Unsupported public source'):
                    self.prepare(module, root, output)
                self.assertEqual(self.calls, [])
                self.assertFalse(output.exists())
        for value in ('B' * 40, NEW + ' ', 'not-a-commit'):
            with self.subTest(commit=value):
                root, output, module = self.fixture()
                with self.assertRaisesRegex(ValueError, 'Invalid commit'):
                    self.prepare(module, root, output, commit=value)
                self.assertEqual(self.calls, [])
        root, output, module = self.fixture()
        (root / 'content/docs/coming-soon.json').write_text(json.dumps({'version': {'commit': 'bad'}}))
        self.replies[API + f'/compare/bad...{NEW}'] = self.replies[self.compare_url]
        self.replies[f'{RAW}/bad/paykit-lib/Cargo.toml'] = self.previous
        with self.assertRaisesRegex(ValueError, 'Invalid commit'):
            self.prepare(module, root, output)
        self.assertEqual(self.calls, [])

    def test_ref_receipt_must_match_exact_namespace_and_object(self):
        for fault in ('wrong-namespace', 'bad-object', 'different-tag-object'):
            with self.subTest(fault=fault):
                root, output, module = self.fixture()
                selected = json.loads(self.replies[self.ref_url])
                if fault == 'wrong-namespace':
                    selected['ref'] = 'refs/heads/synthetic/v-next'
                elif fault == 'bad-object':
                    selected['object']['sha'] = 'not-a-sha'
                    self.replies[API + '/git/tags/not-a-sha'] = self.replies[API + '/git/tags/' + OBJECT]
                else:
                    tag = json.loads(self.replies[API + '/git/tags/' + OBJECT])
                    tag['sha'] = 'd' * 40
                    self.replies[API + '/git/tags/' + OBJECT] = json.dumps(tag).encode()
                self.replies[self.ref_url] = json.dumps(selected).encode()
                with self.assertRaisesRegex(ValueError, 'ref receipt|tag object'):
                    self.prepare(module, root, output)
                self.assertFalse(output.exists())

    def test_published_registry_metadata_is_validated_and_retained(self):
        root, output, module = self.fixture()
        url = 'https://crates.io/api/v1/crates/paykit-lib/0.0.1-synthetic'
        self.replies[url] = json.dumps({'version': {'crate': 'paykit-lib', 'num': '0.0.1-synthetic',
            'yanked': True, 'checksum': 'd' * 64}}).encode()
        self.prepare(module, root, output)
        package = json.loads((output / 'index.json').read_bytes())['version']['packages']['paykit-lib']
        self.assertTrue(package['published'])
        self.assertEqual({key: package.get(key) for key in ('yanked', 'checksum', 'referenceUrl')}, {
            'yanked': True, 'checksum': 'd' * 64,
            'referenceUrl': 'https://docs.rs/paykit-lib/0.0.1-synthetic/paykit_lib/'})
        observations = json.loads((output / 'registry-observations.json').read_bytes())
        self.assertEqual(observations[0]['status'], 200)
        self.assertEqual((output / observations[0]['bodyPath']).read_bytes(), self.replies[url])

    def test_registry_success_cannot_claim_a_different_or_malformed_package(self):
        for field, value in (('crate', 'unrelated'), ('num', '9.9.9'), ('yanked', 0), ('checksum', 'bad')):
            with self.subTest(field=field):
                root, output, module = self.fixture()
                registry = {'crate': 'paykit-lib', 'num': '0.0.1-synthetic', 'yanked': False, 'checksum': 'd' * 64}
                registry[field] = value
                self.replies['https://crates.io/api/v1/crates/paykit-lib/0.0.1-synthetic'] = json.dumps({'version': registry}).encode()
                with self.assertRaisesRegex(ValueError, 'Registry'):
                    self.prepare(module, root, output)
                self.assertFalse(output.exists())

    def test_unsafe_source_paths_are_rejected_before_download_or_write(self):
        for origin in ('evidence', 'compare'):
            with self.subTest(origin=origin):
                root, output, module = self.fixture()
                path = '../escaped.md'
                if origin == 'evidence':
                    (root / 'automation/blueprints/coming-soon.json').write_text(json.dumps({'evidence': [{'path': path}]}))
                else:
                    compare = json.loads(self.replies[self.compare_url])
                    compare['files'].append({'filename': path, 'status': 'modified'})
                    self.replies[self.compare_url] = json.dumps(compare).encode()
                self.replies[f'{RAW}/{NEW}/{path}'] = b'Synthetic traversal probe'
                with self.assertRaisesRegex(ValueError, 'Unsafe source path'):
                    self.prepare(module, root, output)
                self.assertNotIn(f'{RAW}/{NEW}/{path}', self.calls)
                self.assertFalse(output.exists())
                self.assertFalse((output.parent / 'escaped.md').exists())

    def test_output_must_be_empty_and_not_a_symlink_before_http(self):
        for mode in ('nonempty', 'symlink'):
            with self.subTest(mode=mode):
                root, output, module = self.fixture()
                if mode == 'nonempty':
                    output.mkdir()
                    (output / 'sentinel').write_bytes(b'Synthetic owned sentinel')
                else:
                    target = output.parent / 'empty-target'
                    target.mkdir()
                    output.symlink_to(target, target_is_directory=True)
                with self.assertRaisesRegex(ValueError, 'empty.*absolute|symlink'):
                    self.prepare(module, root, output)
                self.assertEqual(self.calls, [])
                if mode == 'nonempty':
                    self.assertEqual(list(output.iterdir()), [output / 'sentinel'])
                else:
                    self.assertEqual(list(target.iterdir()), [])

    def test_wrong_or_capped_compare_is_not_treated_as_complete(self):
        for fault in ('wrong-base', 'capped-files'):
            with self.subTest(fault=fault):
                root, output, module = self.fixture()
                compare = json.loads(self.replies[self.compare_url])
                if fault == 'wrong-base':
                    compare['base_commit']['sha'] = 'd' * 40
                else:
                    compare['files'] = [compare['files'][0]] * 300
                self.replies[self.compare_url] = json.dumps(compare).encode()
                with self.assertRaisesRegex(ValueError, 'compare|Compare'):
                    self.prepare(module, root, output)
                self.assertFalse(any(url.startswith(RAW) for url in self.calls))
                self.assertFalse(output.exists())

    def test_only_bounded_text_source_files_are_frozen(self):
        for payload in (b'x' * 2_000_001, b'Synthetic binary\x00probe'):
            with self.subTest(size=len(payload)):
                root, output, module = self.fixture()
                self.replies[f'{RAW}/{NEW}/README.md'] = payload
                with self.assertRaisesRegex(ValueError, 'large|binary|text'):
                    self.prepare(module, root, output)
                self.assertFalse(output.exists())

    def test_redirects_cannot_contact_an_unapproved_host(self):
        root, output, module = self.fixture()
        foreign = 'https://unapproved.invalid/synthetic'
        def http(request):
            url = request.full_url
            self.calls.append(url)
            headers = Message()
            code, data = 200, self.replies.get(url)
            if url == self.ref_url:
                code, data = 302, b'Synthetic redirect'
                headers['Location'] = foreign
            elif url == foreign:
                data = self.replies[self.ref_url]
            if isinstance(data, Exception):
                raise data
            response = urllib.response.addinfourl(io.BytesIO(data), headers, url, code)
            response.msg = 'Synthetic HTTP fixture'
            return response
        with patch.object(module.urllib.request.HTTPSHandler, 'https_open', side_effect=http):
            with self.assertRaisesRegex(ValueError, 'redirect'):
                module.prepare_development(root, output, NEW)
        self.assertEqual(self.calls, [self.ref_url])
        self.assertFalse(output.exists())

    def test_cli_uses_explicit_commit_and_reports_failures_without_success(self):
        for offline in (False, True):
            with self.subTest(offline=offline):
                root, output, module = self.fixture()
                self.assertTrue(callable(getattr(module, 'main', None)), 'Preparation CLI entry point is missing')
                if offline:
                    self.replies[self.ref_url] = OSError('Synthetic offline error')
                stdout = io.StringIO()
                with patch.object(module, '__file__', str(root / 'automation/prepare_development.py')), \
                        patch.object(sys, 'argv', ['prepare_development.py', '--output', str(output), '--commit', NEW]), \
                        patch.object(module.urllib.request.OpenerDirector, 'open', side_effect=self.http), \
                        contextlib.redirect_stdout(stdout):
                    if offline:
                        with self.assertRaises(SystemExit) as stopped:
                            module.main()
                        self.assertEqual(stopped.exception.code, 1)
                    else:
                        module.main()
                result = json.loads(stdout.getvalue())
                self.assertEqual(result['action'], 'error' if offline else 'prepared')
                self.assertEqual((output / 'index.json').exists(), not offline)
                if not offline:
                    self.assertEqual(result['commit'], NEW)

    def test_registry_errors_are_never_reported_as_unpublished(self):
        for status in (201, 403, 429, 500):
            with self.subTest(status=status):
                root, output, module = self.fixture()
                url = 'https://crates.io/api/v1/crates/paykit-lib/0.0.1-synthetic'
                self.replies[url] = ((status, b'Synthetic unexpected success') if status == 201 else
                    urllib.error.HTTPError(url, status, 'Synthetic registry error', {}, io.BytesIO(b'Synthetic failure')))
                with self.assertRaises((OSError, ValueError)):
                    self.prepare(module, root, output)
                self.assertFalse(output.exists())

    def test_package_metadata_cannot_use_a_different_manifest_name(self):
        root, output, module = self.fixture()
        url = f'{RAW}/{NEW}/paykit-lib/Cargo.toml'
        self.replies[url] = self.replies[url].replace(b'name = "paykit-lib"', b'name = "unrelated"')
        with self.assertRaisesRegex(ValueError, 'manifest'):
            self.prepare(module, root, output)
        self.assertFalse(output.exists())

    def test_branch_and_lightweight_tag_keep_exclusive_identity(self):
        for kind in ('branch', 'tag'):
            with self.subTest(kind=kind):
                root, output, module = self.fixture(kind=kind)
                self.replies[self.ref_url] = json.dumps({'ref': self.qualified,
                    'object': {'type': 'commit', 'sha': NEW}}).encode()
                self.prepare(module, root, output)
                version = json.loads((output / 'index.json').read_bytes())['version']
                self.assertEqual(version[kind], 'synthetic/v-next')
                self.assertEqual(version['object'], NEW)
                self.assertEqual(version['qualifiedRef'], self.qualified)
                self.assertNotIn('tag' if kind == 'branch' else 'branch', version)
                self.assertEqual(self.calls[0], self.ref_url)
                self.assertFalse((output / 'tag.json').exists())

    def test_network_and_malformed_registry_failures_leave_no_snapshot(self):
        for error in (TimeoutError('Synthetic timeout'), b'{not-json'):
            with self.subTest(error=repr(error)):
                root, output, module = self.fixture()
                self.replies['https://crates.io/api/v1/crates/paykit-lib/0.0.1-synthetic'] = error
                with self.assertRaises((OSError, ValueError)):
                    self.prepare(module, root, output)
                self.assertFalse(output.exists())
        root, output, module = self.fixture()
        url = f'{RAW}/{NEW}/README.md'
        self.replies[url] = urllib.error.HTTPError(url, 404, 'Synthetic missing source', {}, io.BytesIO(b'Synthetic failure'))
        with self.assertRaises(urllib.error.HTTPError):
            self.prepare(module, root, output)
        self.assertFalse(output.exists())

    def test_nested_annotated_tags_are_peeled_without_losing_selected_object(self):
        root, output, module = self.fixture()
        inner = 'd' * 40
        self.replies[API + '/git/tags/' + OBJECT] = json.dumps({
            'sha': OBJECT, 'object': {'type': 'tag', 'sha': inner}}).encode()
        self.replies[API + '/git/tags/' + inner] = json.dumps({
            'sha': inner, 'object': {'type': 'commit', 'sha': NEW}}).encode()
        try:
            self.prepare(module, root, output)
        except ValueError as error:
            self.fail('Valid synthetic nested tag must resolve: ' + str(error))
        version = json.loads((output / 'index.json').read_bytes())['version']
        self.assertEqual((version['object'], version['commit']), (OBJECT, NEW))
        self.assertEqual((output / f'tag-{inner}.json').read_bytes(), self.replies[API + '/git/tags/' + inner])

    def policy_fixture(self):
        from test_release_source import POLICY, release
        root, output, module = self.fixture()
        source = {'repository': 'pubky/paykit-rs', 'kind': 'tag', 'ref': 'synthetic/v-next'}
        (root / 'docs-generation.json').write_text(json.dumps({'source': POLICY}))
        selected = release(tag=source['ref'])
        frozen = {'action': 'refresh_needed', 'sourcePolicy': POLICY, 'sourceSelection': source,
                  'sourceCommit': NEW, 'release': {k: selected[k] for k in ('id', 'tag_name', 'published_at', 'prerelease', 'html_url')}}
        selection = output.parent / 'gate.json'
        selection.write_text(json.dumps(frozen))
        self.replies[API + '/releases/' + str(selected['id'])] = json.dumps(selected).encode()
        return root, output, module, selection, frozen

    def test_frozen_gate_selection_prevents_midrun_retarget_to_a_new_release(self):
        from test_release_source import POLICY, release
        root, output, module, selection, frozen = self.policy_fixture()
        source, selected = frozen['sourceSelection'], frozen['release']
        # A newer release has appeared since the gate; preparation must never list it.
        self.replies[API + '/releases?per_page=100&page=1'] = json.dumps([release(99, 'newer-tag')]).encode()
        with patch.object(module.urllib.request.OpenerDirector, 'open', side_effect=self.http):
            try:
                module.prepare_development(root, output, NEW, selection=selection)
            except TypeError as error:
                self.fail('Preparation must consume the gate frozen selection: ' + str(error))
        index = json.loads((output / 'index.json').read_bytes())
        self.assertEqual({k: index[k] for k in source}, source)
        self.assertEqual(index['sourcePolicy'], POLICY)
        self.assertEqual(index['version']['release'], frozen['release'])
        self.assertEqual(index['version']['tag'], source['ref'])
        self.assertEqual(index['commit'], NEW)
        self.assertEqual(self.calls[0], API + '/releases/' + str(selected['id']))
        self.assertNotIn(API + '/releases?per_page=100&page=1', self.calls)
        self.assertEqual((output / 'release.json').read_bytes(), self.replies[self.calls[0]])

    def test_cli_accepts_frozen_gate_json_without_rediscovery(self):
        root, output, module, selection, frozen = self.policy_fixture()
        stdout = io.StringIO()
        with patch.object(module, '__file__', str(root / 'automation/prepare_development.py')), \
                patch.object(sys, 'argv', ['prepare_development.py', '--output', str(output), '--commit', NEW,
                                          '--selection', str(selection)]), \
                patch.object(module.urllib.request.OpenerDirector, 'open', side_effect=self.http), \
                contextlib.redirect_stdout(stdout):
            try:
                module.main()
            except SystemExit as error:
                self.fail('Preparation CLI must accept --selection: ' + str(error))
        self.assertEqual(json.loads(stdout.getvalue())['action'], 'prepared')
        index = json.loads((output / 'index.json').read_bytes())
        self.assertEqual(index['ref'], frozen['sourceSelection']['ref'])

    def test_standalone_preparation_discovers_newest_once_then_checks_expected_commit(self):
        from test_release_source import release
        root, output, module, _, frozen = self.policy_fixture()
        self.replies[API + '/releases?per_page=100&page=1'] = json.dumps([
            release(1, 'old', '2030-01-01T00:00:00Z', prerelease=False),
            release(tag=frozen['sourceSelection']['ref'])]).encode()
        with patch.object(module.urllib.request.OpenerDirector, 'open', side_effect=self.http):
            try:
                module.prepare_development(root, output, NEW)
            except (KeyError, AttributeError) as error:
                self.fail('Standalone preparation must resolve the release policy: ' + str(error))
        index = json.loads((output / 'index.json').read_bytes())
        self.assertEqual(index['version']['release'], frozen['release'])
        self.assertEqual(self.calls.count(API + '/releases?per_page=100&page=1'), 1)
        self.assertEqual(index['commit'], NEW)

    def test_frozen_selection_is_validated_against_policy_before_http(self):
        root, output, module, selection, frozen = self.policy_fixture()
        (root / 'docs-generation.json').write_text(json.dumps({'source': {
            **frozen['sourcePolicy'], 'kind': 'tag', 'ref': frozen['sourceSelection']['ref']}}))
        with patch.object(module.urllib.request.OpenerDirector, 'open', side_effect=self.http):
            with self.assertRaisesRegex(ValueError, 'selection|policy'):
                module.prepare_development(root, output, NEW, selection=selection)
        self.assertEqual(self.calls, [])
        self.assertFalse(output.exists())

    def test_explicit_config_cannot_be_overridden_by_a_frozen_gate(self):
        root, output, module, selection, frozen = self.policy_fixture()
        (root / 'docs-generation.json').write_text(json.dumps({'source': {
            'repository': 'pubky/paykit-rs', 'kind': 'tag', 'ref': 'different-explicit-tag'}}))
        with patch.object(module.urllib.request.OpenerDirector, 'open', side_effect=self.http):
            with self.assertRaisesRegex(ValueError, 'selection|source'):
                module.prepare_development(root, output, NEW, selection=selection)
        self.assertEqual(self.calls, [])
        self.assertFalse(output.exists())


if __name__ == '__main__':
    unittest.main()
