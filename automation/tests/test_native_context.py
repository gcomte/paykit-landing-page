"""Public synthetic fixtures for trusted native-coordinator handoff policy."""
import importlib.util
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('native_policy_pipeline', Path(__file__).parents[1]/'pipeline.py')
p = importlib.util.module_from_spec(spec)
spec.loader.exec_module(p)

class NativeContextTests(unittest.TestCase):
    def fixture(self, root):
        (root/'automation').mkdir()
        script = root/'automation/hermes_coordinator.py'
        script.write_text('# public synthetic coordinator fixture\n')
        config = root/'automation/config.json'
        config.write_text(json.dumps({'privacy': {'requireNativeCoordinator': True}}))
        metadata = {'hermesRevision': 'a'*40, 'coordinatorSha256': p.digest(script.read_bytes()),
                    'isolationVersion': 'native-public-tools-v1'}
        return config, metadata

    def test_missing_or_unapproved_runtime_is_not_accepted(self):
        self.assertTrue(hasattr(p, 'native_runtime_versions'))
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);config,metadata=self.fixture(root);cache=root/'.automation'
            with patch.object(p,'ROOT',root),patch.object(p,'CONFIG',config),patch.object(p,'CACHE',cache):
                with self.assertRaises(ValueError): p.native_runtime_versions(required=True)
                p.save_json(cache/'native-runtime.json',dict(metadata, privateSummary='SYNTHETIC_PRIVATE'))
                with self.assertRaises(ValueError): p.native_runtime_versions(required=True)
                p.save_json(cache/'native-runtime.json',dict(metadata,coordinatorSha256='b'*64))
                with self.assertRaises(ValueError): p.native_runtime_versions(required=True)

    def test_each_run_requires_fresh_consumable_consultation_receipt(self):
        self.assertTrue(hasattr(p,'record_native_preflight'))
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);config,metadata=self.fixture(root);cache=root/'.automation'
            with patch.object(p,'ROOT',root),patch.object(p,'CONFIG',config),patch.object(p,'CACHE',cache):
                record={'id':'c'*32}
                with self.assertRaises(ValueError):p.bind_native_context(record)
                p.record_native_preflight(metadata)
                first=p.read_json(cache/'native-preflight.json')
                p.bind_native_context(record)
                self.assertFalse((cache/'native-preflight.json').exists())
                p.require_native_context(record['id'])
                with self.assertRaises(ValueError):p.bind_native_context({'id':'d'*32})
                p.record_native_preflight(metadata)
                self.assertNotEqual(first['proofId'],p.read_json(cache/'native-preflight.json')['proofId'])
                p.bind_native_context({'id':'d'*32})
                (root/'automation/hermes_coordinator.py').write_text('# changed fixture\n')
                with self.assertRaises(ValueError):p.require_native_context(record['id'])

    def test_expired_preflight_is_rejected(self):
        self.assertTrue(hasattr(p,'record_native_preflight'))
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);config,metadata=self.fixture(root);cache=root/'.automation'
            with patch.object(p,'ROOT',root),patch.object(p,'CONFIG',config),patch.object(p,'CACHE',cache):
                p.record_native_preflight(metadata)
                receipt=p.read_json(cache/'native-preflight.json');receipt['createdAt']=time.time()-301
                p.save_json(cache/'native-preflight.json',receipt)
                with self.assertRaises(ValueError):p.bind_native_context({'id':'e'*32})

    def test_publish_cannot_reach_git_without_native_context(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);config,metadata=self.fixture(root);cache=root/'.automation'
            with patch.object(p,'ROOT',root),patch.object(p,'CONFIG',config),patch.object(p,'CACHE',cache):
                p.record_native_preflight(metadata)
                with patch.object(p,'frozen_sources',side_effect=AssertionError('publication proceeded before context gate')):
                    with self.assertRaisesRegex(ValueError,'context'):
                        p._publish('f'*32)

    def test_begin_consumes_receipt_before_source_resolution(self):
        import contextlib
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);config,metadata=self.fixture(root);cache=root/'.automation'
            cfg=p.read_json(config);cfg['limits']={'runtimeSeconds':600};p.save_json(config,cfg)
            with patch.object(p,'ROOT',root),patch.object(p,'CONFIG',config),patch.object(p,'CACHE',cache):
                p.record_native_preflight(metadata)
                with patch.object(p,'acquire_lease',return_value={'id':'f'*32}),patch.object(p,'run_scope',return_value=contextlib.nullcontext()),patch.object(p,'release_lease'),patch.object(p,'resolve_sources',side_effect=RuntimeError('synthetic source failure')):
                    with self.assertRaises(RuntimeError):p.begin_run(SimpleNamespace(reuse_candidate=False))
                self.assertFalse((cache/'native-preflight.json').exists())
                p.require_native_context('f'*32)

if __name__=='__main__':unittest.main()
