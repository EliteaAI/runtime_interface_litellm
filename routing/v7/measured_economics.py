"""V14 expenditure over measured tool trajectories, separate from capacity.

Each empirical trajectory is priced call by call, retaining context price tiers
and tool history growth. Templates carry equal weight. Sparse/out-of-range data
keeps an explicitly unpriced qualified fallback, never a full-capacity forecast.
"""
import copy
import json
import math
from collections import defaultdict
from decimal import Decimal, ROUND_CEILING
from ..pricing import Tokens
from .state import cache_quote


def support(variant, task, size):
    usage = (variant.get('calibration_contract') or {}).get('families', {}).get(task.get('family'), {}).get('usage_profile')
    if not usage or usage.get('revision') != 'v14-usage-trajectories-1':
        return None, 'MISSING_USAGE_PROFILE'
    matches = [p for p in usage['profiles'] if p['work_profile'] == task.get('work_profile') and p['demand'] == task.get('demand')]
    if len(matches) != 1:
        return None, 'MISSING_EXACT_USAGE_SCOPE'
    profile = matches[0]
    if profile['independent_templates'] < profile['minimum_templates']:
        return None, 'INSUFFICIENT_USAGE_TEMPLATES'
    if not profile['input_bytes']['min'] <= size <= profile['input_bytes']['max']:
        return None, 'OUTSIDE_MEASURED_INPUT_RANGE'
    return profile, None


def price_trajectory(prices, variant, trajectory, size, *, first_read=0):
    total = Decimal(0)
    calls = []
    for index, step in enumerate(trajectory['calls']):
        tokens = int((Decimal(size) * Decimal(str(step['input_tokens_per_initial_byte']))).to_integral_value(rounding=ROUND_CEILING))
        output = step['output_tokens']  # Already includes billed reasoning; never add it twice.
        read = min(first_read, tokens) if index == 0 else 0
        # Later cache hits are not inferred from a first-prefix observation.
        ordinary = prices.quote(variant['model'], Tokens(tokens-read, output, read=read))
        write = (ordinary if variant['cache_write_mode'] == 'ordinary_input' else
                 prices.quote(variant['model'], Tokens(0, output, read=read, write=tokens-read)))
        if ordinary['usd'] is None or write['usd'] is None:
            return None
        amount = max(Decimal(ordinary['usd']), Decimal(write['usd']))
        total += amount
        calls.append({'input_tokens':tokens,'output_tokens':output,'read_tokens':read,'usd':str(amount)})
    return {'usd':str(total),'calls':calls,'template_group':trajectory['template_group']}


def estimate(prices, variant, profile, size, evidence):
    cold = [price_trajectory(prices, variant, row, size) for row in profile['trajectories']]
    if not cold or any(row is None for row in cold):
        return None
    # A current matching observation supports a warm scenario, not its probability.
    read = evidence['read_token_upper_bound'] if evidence else 0
    warm = [price_trajectory(prices, variant, row, size, first_read=read) for row in profile['trajectories']]
    if any(row is None for row in warm):
        return None
    grouped = defaultdict(list)
    warm_grouped = defaultdict(list)
    for row, alternative in zip(cold, warm):
        grouped[row['template_group']].append(Decimal(row['usd']))
        warm_grouped[row['template_group']].append(Decimal(alternative['usd']))
    means = sorted(sum(v)/len(v) for v in grouped.values())
    expected = sum(means)/len(means)
    warm_expected = sum(sum(v)/len(v) for v in warm_grouped.values())/len(warm_grouped)
    return {'expected_cold_usd':str(expected),'warm_scenario_usd':str(warm_expected),
            'template_cost_range_usd':[str(means[0]),str(means[-1])],
            'template_p90_usd':str(means[max(0, math.ceil(.9*len(means))-1)]),
            'independent_templates':len(means),'trajectories':cold,
            'cache_evidence':evidence,'ranking_usd':str(expected),
            'cache_ranking_enabled':False,
            'basis':'Equal-template empirical cold/write expenditure; observed first-prefix warm scenario is separate'}


def rank(selection, messages, catalog, *, session, gateway, cap, tools=None, previous=None):
    eligible = [r['variant'] for r in selection['candidates'] if r['eligible']]
    # SDK projection is authoritative when supplied; routing metadata cannot
    # re-enter the estimate through a larger classifier packet serialization.
    projected = getattr(gateway, 'generation_input_bytes', None)
    size = (projected if type(projected) is int and projected > 0 else
            len(json.dumps({'messages':messages,'tools':tools or [],
                'output_schema':getattr(gateway,'output_schema',None)},ensure_ascii=False).encode())+64*len(messages))
    task = selection.get('economic_task', {})
    quotes, missing = {}, {}
    for vid in eligible:
        variant = catalog['variants'][vid]
        profile, reason = support(variant, task, size)
        if reason:
            missing[vid] = reason
            continue
        allowance = getattr(gateway, 'output_caps', {}).get(vid, cap)
        if any(call['output_tokens'] > allowance for row in profile['trajectories'] for call in row['calls']):
            missing[vid] = 'OUTPUT_CONTRACT_MISMATCH'
            continue
        evidence = cache_quote(session, gateway, vid, variant, messages, tools, allowance)
        result = estimate(gateway.prices, variant, profile, size, evidence)
        if result is None:
            missing[vid] = 'UNPRICED_USAGE_COMPONENT'
        else:
            # Request allowance is trace metadata, never expected expenditure.
            quotes[vid] = {**result,'output_allowance':allowance,'price_revision':gateway.prices.revision}
    comparable = bool(eligible) and not missing
    if comparable:
        chosen = min(eligible, key=lambda v:(Decimal(quotes[v]['ranking_usd']),v!=previous,v))
        reason = 'MEASURED_TRAJECTORY_EXPENDITURE'
    else:
        priorities = [previous, catalog.get('baseline'), *catalog['variants']]
        chosen = next((v for v in priorities if v in eligible), selection['variant'])
        reason = 'UNPRICED_QUALIFIED_FALLBACK'
    switching = None
    if comparable:
        from .cache_switching import retained_variant
        chosen,switching = retained_variant(catalog.get('switching_policies',[]),task=task,
            previous=previous,chosen=chosen,eligible=eligible,quotes=quotes,size=size,price_revision=gateway.prices.revision)
        if switching:reason='VALIDATED_CONTINUATION_EXPENDITURE'
    return {**selection, **copy.deepcopy(catalog['variants'][chosen]),'variant':chosen,
        'reason':reason,'economics':{'reason':reason,'quotes':quotes,'missing_support':missing,'switching_evidence':switching,
            'forecast_comparable':comparable,'input_size_proxy':size,
            'input_proxy_kind':'SDK provider-visible projection when available; bytes, not exact tokens',
            'output_range':[0,getattr(gateway,'output_caps',{}).get(chosen,cap)],
            'switching_policy':'Expected cold/write cost; previous breaks ties. Multi-turn/cache policy requires separate validation.'}}
