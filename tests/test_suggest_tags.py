import json
import unittest
from unittest.mock import patch
from studio import project as db, novelai


class SuggestTagsTests(unittest.TestCase):
    def test_builds_read_only_get_and_parses_tags(self):
        body = json.dumps({'tags': [{'tag': 'hug', 'count': 10000, 'confidence': 0.0}]}).encode()
        with patch.object(novelai, 'request', return_value=body) as call:
            result = novelai.suggest_tags('hug')
        endpoint = call.call_args.args[0]
        self.assertTrue(endpoint.startswith('/ai/generate-image/suggest-tags?'))
        self.assertIn('model=nai-diffusion-5-curated', endpoint)
        self.assertEqual(result, [{'tag': 'hug', 'count': 10000, 'confidence': 0.0}])

    def test_rejects_bad_queries_and_bad_shapes(self):
        for bad in ('', '   ', 'x' * 101, 'a\nb'):
            with self.assertRaises(db.StudioError):
                novelai.suggest_tags(bad)
        with patch.object(novelai, 'request', return_value=b'{"tags":[{"tag":"x"}]}'):
            with self.assertRaises(db.StudioError):
                novelai.suggest_tags('x')

    def test_request_allowlist_still_rejects_other_endpoints(self):
        with self.assertRaises(db.StudioError):
            novelai.request('/user/data')
        with self.assertRaises(db.StudioError):
            novelai.request('/ai/upscale')


if __name__ == '__main__':
    unittest.main()
