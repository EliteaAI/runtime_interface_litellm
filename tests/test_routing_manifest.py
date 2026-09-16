"""Verify shipped routing provenance without the lab workspace or Pylon."""
import hashlib
import json
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1] / 'routing' / 'v7'


class RoutingManifestTests(unittest.TestCase):
    def setUp(self):
        self.manifest = json.loads((ROOT / 'SOURCE-MANIFEST.json').read_text())

    def test_product_hashes_match_current_shipped_files(self):
        expected = {name: value['product_sha256']
                    for name, value in self.manifest['sources'].items()}
        expected.update(self.manifest['product_support_files'])
        for name, digest in expected.items():
            with self.subTest(path=name):
                self.assertEqual(Path(name).name, name)
                self.assertEqual(hashlib.sha256((ROOT / name).read_bytes()).hexdigest(), digest)

    def test_manifest_covers_every_policy_file(self):
        actual = {p.name for p in ROOT.iterdir()
                  if p.is_file() and p.suffix in {'.py', '.json'} and p.name != 'SOURCE-MANIFEST.json'}
        expected = set(self.manifest['sources']) | set(self.manifest['product_support_files'])
        self.assertEqual(actual, expected)

    def test_origin_hashes_are_retained_separately_from_product(self):
        for name, value in self.manifest['sources'].items():
            with self.subTest(path=name):
                self.assertTrue(value['source'].startswith('autorouting_impl/'))
                self.assertFalse(Path(value['source']).is_absolute())
                self.assertIsNotNone(re.fullmatch('[0-9a-f]{64}', value['sha256']))
                self.assertIsNotNone(re.fullmatch('[0-9a-f]{64}', value['product_sha256']))


if __name__ == '__main__':
    unittest.main()
