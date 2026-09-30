"""Quality before price, using owner-approved independent-group evidence.

Rubrics belong to the offline judgments. Runtime consumes difficulty-level
outcomes and provenance, never a requirement to match an evaluation rubric.
"""
import math

DEMANDS = {'simple', 'standard', 'deep'}


def validate(value, variants):
    if value is None:
        return
    if (not isinstance(value, dict) or set(value) != {
            'minimum_groups', 'minimum_pass_rate', 'maximum_quality_gap', 'cohorts'}):
        raise ValueError('Invalid configured quality policy')
    if type(value['minimum_groups']) is not int or not 4 <= value['minimum_groups'] <= 100000:
        raise ValueError('Quality evidence needs independent groups')
    for name in ('minimum_pass_rate', 'maximum_quality_gap'):
        number = value[name]
        if type(number) not in (int, float) or not math.isfinite(number) or not 0 <= number <= 1:
            raise ValueError('Invalid configured quality threshold')
    cohorts = value['cohorts']
    if not isinstance(cohorts, dict) or set(cohorts) - DEMANDS:
        raise ValueError('Invalid quality difficulty cohorts')
    for cohort in cohorts.values():
        if (not isinstance(cohort, dict) or set(cohort) != {'source', 'source_sha256', 'groups', 'outcomes'}
                or not isinstance(cohort['source'], str) or not 1 <= len(cohort['source']) <= 1024
                or not isinstance(cohort['source_sha256'], str) or len(cohort['source_sha256']) != 64
                or any(c not in '0123456789abcdef' for c in cohort['source_sha256'])):
            raise ValueError('Invalid quality evidence provenance')
        groups = cohort['groups']
        if (not isinstance(groups, list) or not 1 <= len(groups) <= 100000
                or any(not isinstance(g, str) or not 1 <= len(g) <= 256 for g in groups)
                or len(groups) != len(set(groups))):
            raise ValueError('Duplicate or invalid quality evidence group')
        outcomes = cohort['outcomes']
        if not isinstance(outcomes, dict) or not outcomes or set(outcomes) - set(variants):
            raise ValueError('Quality evidence references an unknown configured variant')
        for votes in outcomes.values():
            if (not isinstance(votes, list) or len(votes) != len(groups)
                    or any(v not in ('pass', 'fail', 'unknown') for v in votes)):
                raise ValueError('Quality evidence must preserve every matched group')


def frontier(policy, variants, demand):
    """Screen quality on identical independent groups, then let price rank ties.

Unknown groups remain in the denominator, never promoted into successes.
Bounds are a conservative ranking statistic, not per-request guarantees or a
paired noninferiority certificate. The operator owns the declared thresholds.
"""
    cohort = (policy or {}).get('cohorts', {}).get(demand)
    result = {'demand': demand, 'status': 'quality_evidence_unavailable',
              'eligible': [], 'assessments': {}, 'quality_guaranteed': False}
    if cohort is None:
        return result
    n = len(cohort['groups'])
    result.update(source=cohort['source'], source_sha256=cohort['source_sha256'],
                  independent_groups=n, minimum_pass_rate=policy['minimum_pass_rate'],
                  maximum_quality_gap=policy['maximum_quality_gap'])
    if n < policy['minimum_groups']:
        result['status'] = 'insufficient_independent_quality_groups'
        return result
    for variant in variants:
        votes = cohort['outcomes'].get(variant)
        if votes is None:
            result['assessments'][variant] = {'status': 'quality_evidence_unavailable'}
            continue
        passed, failed, unknown = (votes.count(x) for x in ('pass', 'fail', 'unknown'))
        rate = passed / n
        z = 1.959963984540054
        lower = (rate + z*z/(2*n) - z*math.sqrt(rate*(1-rate)/n + z*z/(4*n*n))) / (1+z*z/n)
        result['assessments'][variant] = {
            'pass': passed, 'fail': failed, 'unknown': unknown, 'independent_groups': n,
            'pass_rate': rate, 'conservative_score': max(0, lower),
            'status': 'meets_quality_floor' if rate >= policy['minimum_pass_rate'] else 'below_quality_floor'}
    supported = {v: row['conservative_score'] for v, row in result['assessments'].items()
                 if row['status'] == 'meets_quality_floor'}
    if not supported:
        result['status'] = 'no_model_meets_quality_floor'
        return result
    best = max(supported.values())
    result['eligible'] = [v for v, score in supported.items() if best-score <= policy['maximum_quality_gap']+1e-12]
    result.update(status='quality_screened', best_conservative_score=best)
    for vid, row in result['assessments'].items():
        if row['status'] == 'meets_quality_floor' and vid not in result['eligible']:
            row['status'] = 'outside_quality_tolerance'
    return result
