"""Tier selection, cache accounting and unknown-cost exclusion contracts."""
import importlib.util
from pathlib import Path
import sys
import unittest

path = Path(__file__).resolve().parents[1]/'routing/pricing.py'
spec = importlib.util.spec_from_file_location('routing_price_contract', path)
pricing = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = pricing
spec.loader.exec_module(pricing)


def entry(**updates):
    return {'model_name': 'exact-model', 'input_cost_per_token': 0.000004,
            'output_cost_per_token': 0.000020, 'cache_read_input_token_cost': 0.0000004,
            'cache_creation_input_token_cost': 0.000005, 'extra': {
                'input_cost_per_token_above_272k_tokens': 0.000008,
                'output_cost_per_token_above_272k_tokens': 0.000030,
                'cache_read_input_token_cost_above_272k_tokens': 0.0000008,
                'cache_creation_input_token_cost_above_272k_tokens': 0.000010,
            }, **updates}


class TestPricing(unittest.TestCase):
    def setUp(self):
        self.book = pricing.PriceBook([entry()], source_revision='fixture-1')

    def test_boundary_uses_input_only_and_applies_to_whole_request(self):
        low = self.book.quote('exact-model', pricing.Tokens(272000, 100000))
        high = self.book.quote('exact-model', pricing.Tokens(272001, 1))
        self.assertIsNone(low['threshold'])
        self.assertEqual(pricing.Decimal(low['usd']), pricing.Decimal('3.088'))
        self.assertEqual(high['threshold'], 272000)
        self.assertEqual(pricing.Decimal(high['usd']), pricing.Decimal('2.176038'))

    def test_cached_input_counts_toward_threshold_without_double_billing(self):
        result = self.book.quote('exact-model', pricing.Tokens(1, 10, read=272000))
        self.assertEqual(result['threshold'], 272000)
        self.assertEqual(pricing.Decimal(result['usd']), pricing.Decimal('0.217908'))

    def test_unknown_nonzero_component_is_not_free(self):
        book = pricing.PriceBook([entry(output_cost_per_token=None)], source_revision='fixture')
        self.assertIsNone(book.quote('exact-model', pricing.Tokens(1, 1))['usd'])
        self.assertIsNotNone(book.quote('exact-model', pricing.Tokens(1, 0))['usd'])

    def test_no_region_prefix_or_alias_guessing(self):
        self.assertIsNone(self.book.quote('eu.exact-model', pricing.Tokens(1, 1))['usd'])

    def test_custom_base_does_not_authorize_imported_tier(self):
        book = pricing.PriceBook([entry(is_custom=True)], source_revision='fixture')
        self.assertIsNotNone(book.quote('exact-model', pricing.Tokens(10, 1))['usd'])
        self.assertEqual(book.quote('exact-model', pricing.Tokens(272001, 1))['reason'], 'CUSTOM_TIER_UNCONFIRMED')

    def test_snapshot_is_detached_from_mutable_source(self):
        source = entry()
        book = pricing.PriceBook([source], source_revision='fixture')
        before = book.quote('exact-model', pricing.Tokens(10, 1))
        source['input_cost_per_token'] = 0
        source['extra'].clear()
        self.assertEqual(book.quote('exact-model', pricing.Tokens(10, 1)), before)

    def test_invalid_token_buckets_rejected(self):
        for value in [-1, True, 1.5]:
            with self.assertRaises(ValueError):
                pricing.Tokens(value, 1)

    def test_missing_or_invalid_cache_rate_is_unknown(self):
        book = pricing.PriceBook([entry(cache_read_input_token_cost='NaN')], source_revision='fixture')
        self.assertIsNone(book.quote('exact-model', pricing.Tokens(0, 1, read=10))['usd'])


if __name__ == '__main__':
    unittest.main()
