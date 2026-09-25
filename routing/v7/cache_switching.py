"""Apply only an explicitly enabled, measured continuation policy.

Current quality eligibility and a live matching prefix observation are required.
The policy carries the full measured continuation costs; no future turn count,
cache hit ratio or five-percent switching premium is invented at runtime.
"""


def retained_variant(policies, *, task, previous, chosen, eligible, quotes, size, price_revision):
    for policy in policies:
        scope=policy.get('scope') or {}
        if (policy.get('enabled') is not True or policy.get('passed') is not True
                or policy.get('method')!='matched-continuation-groups-v1'
                or not policy.get('source_sha256')
                or policy.get('independent_groups',0)<policy.get('minimum_groups',1)
                or not policy.get('saving_interval95_usd') or policy['saving_interval95_usd'][0]<=0):continue
        if (previous not in eligible or chosen not in eligible
                or (scope.get('previous_variant'),scope.get('switch_variant'))!=(previous,chosen)
                or scope.get('price_revision')!=price_revision
                or scope.get('ttl_seconds')!=300  # Extended TTL needs a matching runtime wire contract before enabling.
                or any(scope.get(k)!=task.get(k) for k in ('family','demand','work_profile'))
                or not scope.get('input_bytes_min',1)<=size<=scope.get('input_bytes_max',0)):continue
        evidence=quotes.get(previous,{}).get('cache_evidence') or {}
        read=evidence.get('observed_read_tokens')
        if (not evidence.get('within_ttl') or not evidence.get('compatible_prefix')
                or not evidence.get('route_identity_matches') or type(read) is not int or read<=0):continue
        return previous,{'policy_source_sha256':policy['source_sha256'],
                         'basis':'Validated matched continuation policy with current observed prefix read',
                         'saving_interval95_usd':policy['saving_interval95_usd']}
    return chosen,None
