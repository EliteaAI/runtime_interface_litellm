"""Comparable measured expenditure, with conservative unknown-data fallback.

Admission still uses the full output allowance. Predictions never enlarge caps
or grant quality eligibility. A cache quote is advisory and keeps a cold bound.
"""
import copy
import json
from decimal import Decimal, ROUND_CEILING
from ..pricing import Tokens
from .state import cache_quote
from .admission import annotate, cell as admission_cell, variant_head


def forecast(variant, family, size, cap):
    cell=(variant.get('calibration_contract') or {}).get('families',{}).get(family,{})
    usage=cell.get('usage_profile')
    if not usage or usage['tasks']<3 or not usage['input_bytes']['min']<=size<=usage['input_bytes']['max']:
        return None
    estimated_input=int((Decimal(size)*Decimal(str(usage['input_tokens_per_byte']))).to_integral_value(rounding=ROUND_CEILING))
    return {'input_tokens':min(size,estimated_input),'output_tokens':usage['output_tokens']['p90'] if cap is None else min(cap,usage['output_tokens']['p90']),
            'calls':usage['calls_per_task']['p90'],'sample_tasks':usage['tasks'],
            'basis':'Calibration p90 output/call count and measured token/byte ratio; within observed input-size range'}


def rank(selection, messages, catalog, *, session, gateway, cap, tools=None, previous=None):
    if selection['reason'] == 'UNCERTAIN_TASK_BASELINE':
        return selection
    eligible = [row['variant'] for row in selection['candidates'] if row['eligible']]
    size = len(json.dumps({'messages': messages, 'tools': tools or [], 'output_schema': getattr(gateway, 'output_schema', None)}, ensure_ascii=False).encode()) + 64*len(messages)
    size = max(size, getattr(gateway, 'generation_input_bytes', 0))
    quotes = {}
    family=selection.get('family_qualification',{}).get('family')
    forecasts={v:forecast(catalog['variants'][v],family,size,getattr(gateway,'output_caps',{}).get(v,cap)) for v in eligible}
    # Do not reward a candidate merely because its competitor has no usage data.
    comparable=bool(eligible) and all(forecasts.values())
    product=catalog.get('admission') is not None
    admitted=product and 'admission' in selection
    rows={row['variant']:row for row in selection['candidates']}
    expected={}
    if product:
        # The head's expected output, never the provider maximum: cell p90 from the admission
        # table (a greeting uses the simple row), else the usage profile p90, else the explicit
        # cap, else max_output_tokens.
        comparable=False
        for vid in eligible:
            effective=getattr(gateway,'output_caps',{}).get(vid,cap)
            profile=forecast(catalog['variants'][vid],family,size,effective)
            found=rows[vid].get('expected_output_p90') if admitted else (
                (admission_cell(catalog['admission'],variant_head(catalog['variants'][vid]),'simple') or {}).get('expected_output_tokens_p90'))
            if found:
                expected[vid]=(found,'admission_table_p90')
            elif profile:
                expected[vid]=(profile['output_tokens'],'usage_profile_p90')
            else:
                expected[vid]=(effective,'explicit_cap' if getattr(gateway,'cap_basis','provider_default')=='explicit' else 'max_output_tokens')
    for vid in eligible:
        variant_cap = expected[vid][0] if product else getattr(gateway, 'output_caps', {}).get(vid, cap)
        # Price the live deployment bound to this measured variant (#6826).
        model = getattr(gateway, 'deployments', {}).get(vid, catalog['variants'][vid]['model'])
        cold = gateway.prices.quote(model, Tokens(size, variant_cap))
        # V7's upper scenario includes a cache miss/write, without assuming a hit.
        write = (cold if catalog['variants'][vid].get('cache_write_mode') == 'ordinary_input'
                 else gateway.prices.quote(model, Tokens(0, variant_cap, write=size)))
        if cold['usd'] is None or write['usd'] is None:
            raise ValueError('Auto pricing is incomplete for an eligible model')
        # The cache identity carries the call's own cap (None = provider default), as observed.
        evidence = cache_quote(session, gateway, vid, catalog['variants'][vid], messages, tools, cap if product else variant_cap)
        read = min(evidence['read_token_upper_bound'], size) if evidence else 0
        lower = gateway.prices.quote(model, Tokens(size-read, 0, read=read))
        quotes[vid] = {'lower_usd': lower['usd'], 'cache_evidence': evidence, 'upper_usd': str(max(Decimal(cold['usd']), Decimal(write['usd']))),
                       'price_revision': gateway.prices.revision, 'output_allowance': variant_cap}
        if product:
            quotes[vid]['output_basis']=expected[vid][1]
        estimate=forecasts[vid] if comparable else {'input_tokens':size,'output_tokens':variant_cap,'calls':1}
        est_input,est_output=estimate['input_tokens'],estimate['output_tokens']
        est_cold=gateway.prices.quote(model,Tokens(est_input,est_output))
        est_write=(est_cold if catalog['variants'][vid].get('cache_write_mode')=='ordinary_input'
                   else gateway.prices.quote(model,Tokens(0,est_output,write=est_input)))
        if est_cold['usd'] is None or est_write['usd'] is None:
            # A tier can be unpriced even when the conservative envelope is
            # priced. Reprice every candidate on the same conservative basis.
            if comparable:
                uncapped = copy.deepcopy(catalog)
                for candidate in uncapped['variants'].values():
                    for cell in (candidate.get('calibration_contract') or {}).get('families', {}).values():
                        cell.pop('usage_profile', None)
                result = rank(selection, messages, uncapped, session=session, gateway=gateway,
                              cap=cap, tools=tools, previous=previous)
                result['economics']['forecast_fallback'] = 'UNPRICED_FORECAST_TIER'
                return result
            raise ValueError('Auto pricing is incomplete for an eligible model')
        estimate_cost=max(Decimal(est_cold['usd']),Decimal(est_write['usd']))
        observations=evidence.get('historical_observations',0) if evidence else 0
        if observations>=3:
            # Observed mixture, not a promise of a future hit or a trained
            # probability. Misses are retained, and the cold bound stays visible.
            rate=Decimal(evidence['historical_read_hits'])/Decimal(observations)
            est_read=min(read,est_input)
            warm=gateway.prices.quote(model,Tokens(est_input-est_read,est_output,read=est_read))
            if warm['usd'] is not None:
                estimate_cost=(1-rate)*estimate_cost+rate*Decimal(warm['usd'])
                quotes[vid]['cache_observed_mix']={'hits':evidence['historical_read_hits'],'observations':observations,
                    'future_hit_probability_known':False}
        quotes[vid]['ranking_usd']=str(estimate_cost*estimate['calls'])
        quotes[vid]['usage_forecast']=forecasts[vid] if comparable else None
    order = {None: 0, 'low': 1, 'medium': 2, 'high': 3}
    chosen = min(eligible, key=lambda v: (Decimal(quotes[v]['ranking_usd']), v!=previous, order[catalog['variants'][v]['effort']], v))
    reason = 'COMPARABLE_CALIBRATION_EXPENDITURE' if comparable else 'CONSERVATIVE_EXPENDITURE'
    if product:
        reason = 'ADMISSION_P90_EXPENDITURE'
    result = {**selection, **copy.deepcopy(catalog['variants'][chosen]), 'variant': chosen,
            'reason': 'V6_PRICE_AFTER_ELIGIBILITY', 'economics': {'reason': reason, 'quotes': quotes,
            'output_range': [0, quotes[chosen]['output_allowance']], 'input_size_proxy': size,
            'forecast_comparable':comparable,'forecast_fallback':None if comparable else 'MISSING_OR_OUT_OF_RANGE_USAGE_SUPPORT',
            'switching_policy':'Previous variant breaks equal-cost ties; no fixed percentage premium',
            'input_proxy_kind': 'Serialized UTF-8 bytes plus framing allowance; not exact provider tokens',
            'price_basis': 'Current platform Costs snapshot; no cache-hit promise'}}
    if admitted:
        # The re-ranked winner may differ from the chooser's pick: carry its own tier.
        info=selection['admission']
        annotate(result,rows[chosen],demand=info['demand'],family=info['family'],
                 admission={'rule_revision':info['rule_revision'],'sha256':info['table_sha256']},
                 allowance_rejected=info.get('allowance_rejected',()),rejections=info.get('rejections'))
        if selection.get('reason')=='UNCERTAIN_CHEAPEST_CLEARING_FLOOR':
            result['reason']='UNCERTAIN_CHEAPEST_CLEARING_FLOOR'
    return result
