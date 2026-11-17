"""Offline wire fixtures; synthetic output transform, NOT real-SDK acceptance."""
import base64
import contextlib
import io
import json
from pathlib import Path
import random
import re
import shlex
import unittest
import zlib
from unittest.mock import patch

from automation import hermes_coordinator as c
from automation import host_bridge as h


def synthetic_suffix_shortening(text):
    """LABELLED synthetic fixture: parent-confirmed pinned SDK JWT regex.

    Matches agent/redact.py's supplied regex, with the observed 6/.../4 mask.
    Not an SDK hook or live runtime test. Only allowed public policy data and
    synthetic data enter this fixture. Never change the real SDK redactor.
    """
    return re.sub(r'eyJ[A-Za-z0-9_-]{10,}(?:\.[A-Za-z0-9_=-]{4,}){0,2}',
                  lambda match: match.group()[:6] + '...' + match.group()[-4:]
                  if len(match.group()) > 13 else match.group(), text)


class NativeCodecTests(unittest.TestCase):
    def test_cross_endpoint_native_bridge_roundtrip_public_policy(self):
        policy = Path(__file__).parents[1] / 'skills/coordinator.md'
        expected = h.paginate(policy.read_text(), {'offset': 0, 'limit': 6000})
        def labelled_offline_terminal_fixture(command, **kwargs):
            argv = shlex.split(command)
            request = h.decode_message(argv[-1])
            self.assertEqual(request, {'op': 'read_inputs', 'args': {'path': 'automation/skills/coordinator.md'}})
            output = io.StringIO()
            with patch.object(h, 'dispatch', return_value=expected) as dispatch, contextlib.redirect_stdout(output):
                self.assertEqual(h.main(argv[2:]), 0)
            dispatch.assert_called_once_with('fixture-run', 'author', 'fixture-worker', request)
            return {'exit_code': 0, 'output': synthetic_suffix_shortening(output.getvalue())}
        bridge = c.NativeBridge(terminal_tool=labelled_offline_terminal_fixture)
        result = bridge('read_inputs', runId='fixture-run', role='author', workerId='fixture-worker',
                        deadline=bridge.deadline, artifactHash=None, path='automation/skills/coordinator.md')
        self.assertEqual(result, expected)

    def test_new_framing_rejects_invalid_inner_json_and_compression(self):
        invalid_raw = (b'{"a":1,"a":2}', b'{"a":{"b":1,"b":2}}', b'{"a":NaN}',
                       b'{"a":Infinity}', b'{"a":-Infinity}', b'{} trailing', b'{"a":"\xff"}',
                       b'x' * 1048577)
        compressed = zlib.compress(b'{}')
        invalid_compressed = [zlib.compress(raw) for raw in invalid_raw]
        invalid_compressed += [compressed[:-1], compressed + b'extra', compressed + compressed, b'not-zlib']
        for decoder, error in ((h.decode_message, ValueError), (c.decode_message, c.CoordinatorError)):
            self.assertEqual(decoder('pkhex1:' + compressed.hex()), {})
            for data in invalid_compressed:
                with self.subTest(endpoint=decoder.__module__, data_length=len(data)), self.assertRaises(error):
                    decoder('pkhex1:' + data.hex())

    def test_malformed_or_legacy_frames_fail_closed(self):
        valid = h.encode_message({'op': 'bootstrap', 'args': {}})
        legacy = base64.urlsafe_b64encode(zlib.compress(b'{}')).decode()
        malformed = (legacy, legacy[:6] + '...' + legacy[-4:], '', None, 7,
                     'pkhex2:' + valid[7:], 'pkhex1:', valid.upper(), valid[:-1],
                     valid + ' ', valid[:10] + ' ' + valid[10:], valid + '00',
                     'pkhex1:' + '0' * 120000)
        for decoder, error in ((h.decode_message, ValueError), (c.decode_message, c.CoordinatorError)):
            for token in malformed:
                # Coordinator alone retains its existing stdout newline trim.
                if decoder == c.decode_message and token == valid + ' ':
                    continue
                with self.subTest(endpoint=decoder.__module__, token_type=type(token).__name__), self.assertRaises(error):
                    decoder(token)

    def test_expanded_and_encoded_budgets_are_not_broadened(self):
        self.assertEqual(h.MAX_REQUEST, 1048576)
        self.assertEqual(h.MAX_RESPONSE, 18000)
        self.assertEqual(h.MAX_TOKEN, c.MAX_TOKEN)
        self.assertLessEqual(h.MAX_TOKEN, 120000)
        empty_length = len(json.dumps({'text': ''}, separators=(',', ':')).encode())
        exact = {'text': 'a' * (1048576 - empty_length)}
        too_large = {'text': exact['text'] + 'a'}
        incompressible = {'text': random.Random(123).randbytes(100000).hex()}
        for encoder, error in ((h.encode_message, ValueError), (c.encode_message, c.CoordinatorError)):
            token = encoder(exact)
            self.assertRegex(token, r'^pkhex1:[0-9a-f]+$')
            self.assertEqual(h.decode_message(token), exact)
            self.assertEqual(c.decode_message(token), exact)
            for value in (too_large, incompressible, {'x': float('nan')}, {'x': float('inf')}):
                with self.subTest(endpoint=encoder.__module__), self.assertRaises(error):
                    encoder(value)

    def test_cli_response_budget_denies_oversized_public_result(self):
        value = {'text': random.Random(456).randbytes(18000).hex()}
        self.assertGreater(len(h.encode_message({'ok': True, 'result': value})), h.MAX_RESPONSE)
        output = io.StringIO()
        request = c.encode_message({'op': 'bootstrap', 'args': {}})
        with patch.object(h, 'dispatch', return_value=value), contextlib.redirect_stdout(output):
            self.assertEqual(h.main(['--run', 'fixture-run', '--role', 'author', '--session',
                                     'fixture-worker', '--request', request]), 0)
        self.assertEqual(len(output.getvalue().splitlines()), 1)
        self.assertLessEqual(len(output.getvalue().strip()), h.MAX_RESPONSE)
        self.assertEqual(c.decode_message(output.getvalue()), {'ok': False, 'error': {
            'code': 'denied', 'message': 'Invalid or denied public request'}})

    def test_fixed_public_fixture_survives_labelled_synthetic_suffix_shortening(self):
        # Keep the collision fixture independent of mutable project policy bytes.
        # This is labelled synthetic public data, NOT a captured provider response.
        # Current-policy end-to-end coverage is the separate cross-endpoint test.
        text = ('OFFLINE synthetic public codec regression fixture; not live SDK data.\n'
                + random.Random(45).randbytes(2048).hex())
        response = {'ok': True, 'result': h.paginate(text, {'offset': 0, 'limit': 6000})}
        raw = json.dumps(response, ensure_ascii=False, separators=(',', ':')).encode()
        legacy = base64.urlsafe_b64encode(zlib.compress(raw)).decode()
        # Prove this fixed fixture actually exercises the old failure.
        self.assertNotEqual(synthetic_suffix_shortening(legacy), legacy)
        for encoder in (c.encode_message, h.encode_message):
            with self.subTest(endpoint=encoder.__module__):
                token = encoder(response)
                transported = synthetic_suffix_shortening(token)
                self.assertEqual(transported, token, 'machine framing changed in synthetic output channel')
                self.assertEqual(c.decode_message(transported), response)
                self.assertEqual(h.decode_message(transported), response)


if __name__ == '__main__':
    unittest.main()
