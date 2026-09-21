"""Strict cost scenarios over an immutable, exact-model catalog snapshot.

Input buckets are disjoint. Context-price thresholds use total input, including
cached input; output tokens do not decide the threshold. This is an estimate
from the platform catalog, not an invoice or a budget reservation.
"""
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from types import MappingProxyType
import hashlib
import json
import re

FIELDS = {
    'uncached': 'input_cost_per_token',
    'output': 'output_cost_per_token',
    'read': 'cache_read_input_token_cost',
    'write': 'cache_creation_input_token_cost',
}
TIER = re.compile(r'^(input_cost_per_token|output_cost_per_token|cache_read_input_token_cost|cache_creation_input_token_cost)_above_(\d+)k_tokens$')


@dataclass(frozen=True)
class Tokens:
    uncached: int
    output: int
    read: int = 0
    write: int = 0

    def __post_init__(self):
        if any(type(v) is not int or v < 0 for v in (self.uncached, self.output, self.read, self.write)):
            raise ValueError('Token buckets must be nonnegative integers')

    @property
    def total_input(self):
        return self.uncached + self.read + self.write


def _rate(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return result if result.is_finite() and result >= 0 else None


class PriceBook:
    """Compile once; each quote does bounded lookups and Decimal arithmetic."""
    def __init__(self, entries, *, source_revision):
        canonical = json.dumps(entries, sort_keys=True, separators=(',', ':'), allow_nan=False)
        self.revision = hashlib.sha256((source_revision+'\n'+canonical).encode()).hexdigest()
        compiled = {}
        for entry in entries:
            name = entry.get('model_name')
            if not isinstance(name, str) or not name or name in compiled:
                raise ValueError('Price entries require unique exact model names')
            tiers = {}
            for key, value in (entry.get('extra') or {}).items():
                match = TIER.fullmatch(key)
                if match:
                    tiers.setdefault(int(match[2])*1000, {})[match[1]] = _rate(value)
            compiled[name] = (
                MappingProxyType({field: _rate(entry.get(field)) for field in FIELDS.values()}),
                tuple((threshold, MappingProxyType(rates)) for threshold, rates in sorted(tiers.items())),
                bool(entry.get('is_custom')),
            )
        self.entries = MappingProxyType(compiled)

    def quote(self, model, tokens):
        """Return known USD or None; missing nonzero components never cost zero.

        A custom flat override does not confirm imported long-context rates.
        Cache TTL variants require a separately compiled rate contract; callers
        must not combine one-hour writes with the default write bucket here.
        """
        entry = self.entries.get(model)
        if entry is None:
            return {'usd': None, 'reason': 'MODEL_PRICE_MISSING', 'revision': self.revision}
        base, tiers, custom = entry
        selected, threshold = base, None
        for boundary, rates in tiers:
            if tokens.total_input > boundary:
                selected, threshold = rates, boundary
        if threshold is not None and custom:
            return {'usd': None, 'reason': 'CUSTOM_TIER_UNCONFIRMED', 'revision': self.revision}
        total = Decimal(0)
        for bucket, field in FIELDS.items():
            count = getattr(tokens, bucket)
            if count == 0:
                continue
            rate = selected.get(field)
            if rate is None:
                return {'usd': None, 'reason': 'COMPONENT_PRICE_MISSING', 'component': field,
                        'threshold': threshold, 'revision': self.revision}
            total += count * rate
        return {'usd': str(total), 'reason': 'CATALOG_ESTIMATE', 'threshold': threshold,
                'total_input': tokens.total_input, 'revision': self.revision}
