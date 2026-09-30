"""Trusted deployment eligibility, distinct from measured qualification.

Policy owners declare a pool and task ceilings. Exact calibration remains
visible and adverse evidence is retained; absent cells are not execution bans.
"""
import copy
import re
from decimal import Decimal

from .fallback_config import validate as native_contract
from .pricing import Tokens
from .v7.retrieval import digest
from .v7.task_profile import FAMILIES

BLOCKED = {'adverse_exact_scope', 'provider_refusal', 'measured_failure',
           'disputed_assessment', 'unresolved_measurement', 'incomplete_delivery',
           'incompatible_measurement_contract', 'conflicting_evidence_cells'}
DEMANDS = {'simple', 'standard', 'deep'}
OPERATIONS = {'greeting', 'creative', 'transform', 'analysis', 'design', 'other'}


def validate(value):
    keys = {'revision', 'source', 'source_sha256', 'request_scope_ids',
            'maximum_user_turns', 'allow_tools', 'scenario_output_tokens', 'variants'}
    if not isinstance(value, dict) or set(value) != keys:
        raise ValueError('Invalid configured selection policy')
    if (not isinstance(value['revision'], str) or not 1 <= len(value['revision']) <= 128
            or not isinstance(value['source'], str) or not 1 <= len(value['source']) <= 1024
            or not isinstance(value['source_sha256'], str)
            or not re.fullmatch('[0-9a-f]{64}', value['source_sha256'])
            or type(value['maximum_user_turns']) is not int or not 1 <= value['maximum_user_turns'] <= 128
            or type(value['allow_tools']) is not bool
            or type(value['scenario_output_tokens']) is not int
            or not 1 <= value['scenario_output_tokens'] <= 1_000_000):
        raise ValueError('Invalid configured selection policy bounds')
    scopes = value['request_scope_ids']
    if (not isinstance(scopes, list) or not 1 <= len(scopes) <= 32
            or any(not isinstance(s, str) or not 1 <= len(s) <= 256 for s in scopes)
            or len(scopes) != len(set(scopes))):
        raise ValueError('Invalid configured selection scopes')
    variants = value['variants']
    if not isinstance(variants, dict) or not 1 <= len(variants) <= 256:
        raise ValueError('Invalid configured selection pool')
    for vid, entry in variants.items():
        if (not isinstance(vid, str) or not re.fullmatch('[a-zA-Z0-9_-]{1,100}', vid)
                or not isinstance(entry, dict)
                or set(entry) != {'native', 'families', 'operations', 'evidence_ref'}):
            raise ValueError('Invalid configured selection entry')
        native = native_contract(entry['native'])
        if native['output_allowance'] < value['scenario_output_tokens']:
            raise ValueError('Cost scenario exceeds a configured output contract')
        families = entry['families']
        if (not isinstance(families, dict) or not families or set(families) - set(FAMILIES)
                or any(not isinstance(ds, list) or not ds or any(not isinstance(d, str) for d in ds)
                       or set(ds) - DEMANDS or len(ds) != len(set(ds)) for ds in families.values())):
            raise ValueError('Invalid configured family demand policy')
        ops = entry['operations']
        if (not isinstance(ops, list) or not ops or any(not isinstance(o, str) for o in ops)
                or set(ops) - OPERATIONS or len(ops) != len(set(ops))):
            raise ValueError('Invalid configured operations')
        ref = entry['evidence_ref']
        if ref is not None and (not isinstance(ref, dict) or set(ref) != {'profile', 'revision', 'variant', 'snapshot_sha256'}
                or any(not isinstance(x, str) or not x or len(x) > 256 for x in ref.values())):
            raise ValueError('Invalid configured evidence reference')
    return copy.deepcopy(value)


def install(catalog, policy):
    from .bundle import snapshot
    from .v7.catalog import compile_uniform_catalog
    policy = validate(policy)
    result = copy.deepcopy(catalog)
    sources = {}
    for key, entry in policy['variants'].items():
        vid = 'configured-' + key
        if vid in result['variants']:
            raise ValueError('Configured selection identity collision')
        native = entry['native']
        evidence = None
        ref = entry['evidence_ref']
        if ref is not None:
            source_key = (ref['profile'], ref['revision'])
            if source_key not in sources:
                value, source_hash = snapshot(*source_key)
                sources[source_key] = (compile_uniform_catalog(value,
                    classifier_variant=value['classifier_variant'], baseline_variant=value['baseline_variant']), source_hash)
            source, source_hash = sources[source_key]
            if source_hash != ref['snapshot_sha256']:
                raise ValueError('Configured evidence snapshot changed')
            measured = source['variants'].get(ref['variant'])
            if measured is None:
                raise ValueError('Configured evidence variant is unavailable')
            evidence = copy.deepcopy(measured['calibration_contract'])
            if (measured['effort'] != native['effort'] or any(evidence.get(k) != native[k]
                    for k in ('transport', 'reasoning_fields', 'output_allowance'))):
                raise ValueError('Configured evidence native contract differs')
            # An explicit alias mapping is an owner assertion, never a name
            # heuristic. Evidence labels and original source remain unchanged.
            entry['evidence_binding'] = {**ref, 'snapshot_sha256': source_hash,
                                         'original_model': measured['model']}
        demands = {d for ds in entry['families'].values() for d in ds}
        variant = {'model': native['model_binding']['name'], 'effort': native['effort'],
            'enabled': True, 'tasks': entry['operations'],
            'max_demand': max(demands, key={'simple': 0, 'standard': 1, 'deep': 2}.get),
            'cost_band': 2, 'cache_write_mode': native['cache_write_mode'],
            'configured_routing_contract': native, 'configured_eligibility': entry}
        if evidence is not None:
            variant['calibration_contract'] = evidence
            variant['configured_evidence_scope_matches'] = (source.get('request_scope') == catalog.get('request_scope'))
        result['variants'][vid] = variant
    result['configured_selection_policy'] = policy
    result['revision'] += '-configured-selection-' + digest(policy)[:16]
    return result


def covers(policy, task_contract, messages, tools):
    return (task_contract in policy['request_scope_ids']
            and sum(m.get('role') == 'user' for m in messages) <= policy['maximum_user_turns']
            and (policy['allow_tools'] or not tools and not any(m.get('role') == 'tool' for m in messages)))


def rank(selection, messages, catalog, *, session, gateway, cap, tools=None, previous=None):
    """Use a common price scenario when comparable forecasts do not exist.

    The scenario is a policy parameter, not expected usage or a savings claim.
    Provider capacities never become model-specific output forecasts.
    """
    from .v7.measured_economics import rank as measured_rank
    measured_catalog = copy.deepcopy(catalog)
    for variant in measured_catalog['variants'].values():
        if variant.get('configured_routing_contract') and (not variant.get('configured_evidence_scope_matches')
                or selection['family_qualification'].get('measurement_scope_gap')):
            variant.pop('calibration_contract', None)
    measured = measured_rank(selection, messages, measured_catalog, session=session,
                             gateway=gateway, cap=cap, tools=tools, previous=previous)
    if measured['economics']['forecast_comparable']:
        return measured
    eligible = [r['variant'] for r in selection['candidates'] if r['eligible']]
    size = measured['economics']['input_size_proxy']
    output = min(catalog['configured_selection_policy']['scenario_output_tokens'],
                 *(getattr(gateway, 'output_caps', {}).get(v, cap) for v in eligible))
    quotes = {}
    for vid in eligible:
        variant = catalog['variants'][vid]
        cold = gateway.prices.quote(variant['model'], Tokens(size, output))
        write = (cold if variant['cache_write_mode'] == 'ordinary_input' else
                 gateway.prices.quote(variant['model'], Tokens(0, output, write=size)))
        if cold['usd'] is None or write['usd'] is None:
            raise ValueError('Configured selection price scenario is incomplete')
        quotes[vid] = {'ranking_usd': str(max(Decimal(cold['usd']), Decimal(write['usd']))),
                      'scenario_input_units': size, 'scenario_output_tokens': output,
                      'price_revision': gateway.prices.revision}
    chosen = min(eligible, key=lambda v: (Decimal(quotes[v]['ranking_usd']), v != previous, v))
    return {**selection, **copy.deepcopy(catalog['variants'][chosen]), 'variant': chosen,
        'reason': 'CONFIGURED_ELIGIBILITY_COMMON_PRICE_SCENARIO',
        'predicted_output_tokens': None, 'currency_cost_estimate': None,
        'economics': {'reason': 'COMMON_PRICE_SCENARIO', 'quotes': quotes,
            'forecast_comparable': False, 'missing_support': measured['economics']['missing_support'],
            'input_size_proxy': size, 'expected_cost': None, 'savings_claim': False,
            'cache_ranking_enabled': False, 'switching_evidence': None,
            'basis': 'Same input-byte proxy and configured output scenario for every eligible model; cold/write prices, no cache-hit assumption'}}
