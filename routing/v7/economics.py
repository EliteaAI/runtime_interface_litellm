"""V7 upper-scenario ranking using the product's exact Costs price snapshot."""
import copy
import json
from decimal import Decimal
from ..pricing import Tokens
from .state import cache_quote


def rank(selection, messages, catalog, *, session, gateway, cap, tools=None, previous=None):
    if selection['reason'] == 'UNCERTAIN_TASK_BASELINE':
        return selection
    eligible = [row['variant'] for row in selection['candidates'] if row['eligible']]
    size = len(json.dumps({'messages': messages, 'tools': tools or [], 'output_schema': getattr(gateway, 'output_schema', None)}, ensure_ascii=False).encode()) + 64*len(messages)
    size = max(size, getattr(gateway, 'generation_input_bytes', 0))
    quotes = {}
    for vid in eligible:
        variant_cap = getattr(gateway, 'output_caps', {}).get(vid, cap)
        model = catalog['variants'][vid]['model']
        cold = gateway.prices.quote(model, Tokens(size, variant_cap))
        # V7's upper scenario includes a cache miss/write, without assuming a hit.
        write = (cold if catalog['variants'][vid].get('cache_write_mode') == 'ordinary_input'
                 else gateway.prices.quote(model, Tokens(0, variant_cap, write=size)))
        if cold['usd'] is None or write['usd'] is None:
            raise ValueError('Auto pricing is incomplete for an eligible model')
        evidence = cache_quote(session, gateway, vid, catalog['variants'][vid], messages, tools, variant_cap)
        read = min(evidence['read_token_upper_bound'], size) if evidence else 0
        lower = gateway.prices.quote(model, Tokens(size-read, 0, read=read))
        quotes[vid] = {'lower_usd': lower['usd'], 'cache_evidence': evidence, 'upper_usd': str(max(Decimal(cold['usd']), Decimal(write['usd']))),
                       'price_revision': gateway.prices.revision, 'output_allowance': variant_cap}
    order = {None: 0, 'low': 1, 'medium': 2, 'high': 3}
    chosen = min(eligible, key=lambda v: (Decimal(quotes[v]['upper_usd']), order[catalog['variants'][v]['effort']], v))
    reason = 'MINIMUM_CONFIGURED_COST_UPPER_SCENARIO'
    if previous in eligible and Decimal(quotes[previous]['upper_usd']) <= Decimal(quotes[chosen]['upper_usd'])*Decimal('1.05'):
        chosen, reason = previous, 'QUALIFIED_WITHIN_FIVE_PERCENT_UPPER_SCENARIO'
    return {**selection, **copy.deepcopy(catalog['variants'][chosen]), 'variant': chosen,
            'reason': 'V6_PRICE_AFTER_ELIGIBILITY', 'economics': {'reason': reason, 'quotes': quotes,
            'output_range': [0, quotes[chosen]['output_allowance']], 'input_size_proxy': size,
            'input_proxy_kind': 'Serialized UTF-8 bytes plus framing allowance; not exact provider tokens',
            'price_basis': 'Current platform Costs snapshot; no cache-hit promise'}}
