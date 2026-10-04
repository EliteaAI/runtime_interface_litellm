"""Quality before price, using owner-approved independent-group evidence.

Rubrics belong to the offline judgments. Runtime consumes reviewed outcomes
and typed workload support, never a requirement to match an evaluation rubric.
"""
import math
from statistics import NormalDist
from .v7.retrieval import digest
from .v7.task_profile import ENUMS, PROFILE_SEMANTICS_VERSION

DEMANDS = {'simple', 'standard', 'deep'}


def validate(value, variants):
    if value is None:
        return
    if not isinstance(value, dict):
        raise ValueError('Invalid configured quality policy')
    development = value.get('version') == 4 and type(value.get('version')) is int
    scoped = value.get('version') in (3, 4) and type(value.get('version')) is int
    paired = scoped or value.get('version') == 2 and type(value.get('version')) is int
    thresholds = (('minimum_observed_success', 'maximum_observed_paired_adverse_fraction') if development
                  else ('minimum_success_lower_bound', 'maximum_paired_loss_upper_bound') if paired
                  else ('minimum_pass_rate', 'maximum_quality_gap'))
    expected = {'minimum_groups', 'cohorts', *thresholds} | ({'version'} if paired else set())
    if scoped:
        expected.add('native_contract_sha256')
        if 'profile_semantics' in value:
            expected.add('profile_semantics')
            if value['profile_semantics'] != PROFILE_SEMANTICS_VERSION:
                raise ValueError('Quality evidence profile semantics differ from the classifier')
    if set(value) != expected:
        raise ValueError('Invalid configured quality policy')
    if type(value['minimum_groups']) is not int or not (8 if development else 4) <= value['minimum_groups'] <= 100000:
        raise ValueError('Quality evidence needs independent groups')
    for name in thresholds:
        number = value[name]
        if type(number) not in (int, float) or not math.isfinite(number) or not 0 <= number <= 1:
            raise ValueError('Invalid configured quality threshold')
    cohorts = value['cohorts']
    if (not isinstance(cohorts, dict) or len(cohorts) > 256
            or (not scoped and set(cohorts) - DEMANDS)
            or any(not isinstance(k, str) or not 1 <= len(k) <= 128 for k in cohorts)):
        raise ValueError('Invalid quality difficulty cohorts')
    for cohort in cohorts.values():
        fields = {'source', 'source_sha256', 'groups', 'outcomes'}
        if paired:
            fields |= {'reference_variant', 'evaluation_scope'}
        if scoped:
            fields |= {'demand', 'work_profile'}
        if development:
            fields.add('training_groups')
        if (not isinstance(cohort, dict) or set(cohort) != fields
                or not isinstance(cohort['source'], str) or not 1 <= len(cohort['source']) <= 1024
                or not isinstance(cohort['source_sha256'], str) or len(cohort['source_sha256']) != 64
                or any(c not in '0123456789abcdef' for c in cohort['source_sha256'])):
            raise ValueError('Invalid quality evidence provenance')
        if scoped:
            profile = cohort['work_profile']
            if (cohort['demand'] not in DEMANDS or not isinstance(profile, dict)
                    or set(profile) != set(ENUMS)
                    or any(not isinstance(profile[k], str) or profile[k] not in choices
                           for k, choices in ENUMS.items())):
                raise ValueError('Invalid quality workload scope')
        groups = cohort['groups']
        if (not isinstance(groups, list) or not 1 <= len(groups) <= 100000
                or any(not isinstance(g, str) or not 1 <= len(g) <= 256 for g in groups)
                or len(groups) != len(set(groups))):
            raise ValueError('Duplicate or invalid quality evidence group')
        if development:
            training = cohort['training_groups']
            if (not isinstance(training, list) or not training
                    or any(not isinstance(g, str) for g in training)
                    or len(set(training)) != len(training) or not set(training) <= set(groups)):
                raise ValueError('Development quality needs explicit training-group provenance')
        outcomes = cohort['outcomes']
        if not isinstance(outcomes, dict) or not outcomes or set(outcomes) - set(variants):
            raise ValueError('Quality evidence references an unknown configured variant')
        if paired:
            if (not isinstance(cohort['reference_variant'], str)
                    or cohort['reference_variant'] not in outcomes):
                raise ValueError('Paired quality evidence needs a matched reference variant')
            scope = cohort['evaluation_scope']
            if (not isinstance(scope, dict)
                    or set(scope) != {'request_scope_ids', 'maximum_user_turns', 'allow_tools', 'allow_history'}
                    or type(scope['maximum_user_turns']) is not int
                    or not 1 <= scope['maximum_user_turns'] <= 128
                    or type(scope['allow_tools']) is not bool
                    or type(scope['allow_history']) is not bool):
                raise ValueError('Invalid quality evaluation scope')
            ids = scope['request_scope_ids']
            if (not isinstance(ids, list) or not 1 <= len(ids) <= 32
                    or any(not isinstance(s, str) or not 1 <= len(s) <= 256 for s in ids)
                    or len(set(ids)) != len(ids)):
                raise ValueError('Invalid quality evaluation scope IDs')
        for votes in outcomes.values():
            if (not isinstance(votes, list) or len(votes) != len(groups)
                    or any(v not in ('pass', 'fail', 'unknown') for v in votes)):
                raise ValueError('Quality evidence must preserve every matched group')
    if scoped:
        identities = value['native_contract_sha256']
        measured = {v for c in cohorts.values() for v in c['outcomes']}
        if (not isinstance(variants, dict) or not isinstance(identities, dict)
                or set(identities) != measured
                or any(identities[v] != digest(variants[v]['native']) for v in measured)):
            raise ValueError('Quality evidence native deployment/effort contract changed')


def frontier(policy, variants, demand, *, request_scope=None, work_profile=None):
    """Screen quality on identical independent groups, then let price rank ties.

Unknown groups remain in the denominator, never promoted into successes.
Bounds are a conservative ranking statistic, not per-request guarantees or a
paired noninferiority certificate. The operator owns the declared thresholds.
"""
    if ('profile_semantics' in (policy or {})
            and policy['profile_semantics'] != PROFILE_SEMANTICS_VERSION):
        return {'demand': demand, 'status': 'quality_profile_semantics_mismatch',
                'eligible': [], 'assessments': {}, 'quality_guaranteed': False,
                'evidence_profile_semantics': policy['profile_semantics'],
                'classifier_profile_semantics': PROFILE_SEMANTICS_VERSION}
    if (policy or {}).get('version') == 4:
        return _development_frontier(policy, variants, demand, request_scope, work_profile)
    if (policy or {}).get('version') in (2, 3):
        return _paired_frontier(policy, variants, demand, request_scope, work_profile)
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


def _score_bounds(successes, total, z):
    rate = successes / total
    center = (rate + z*z/(2*total)) / (1+z*z/total)
    radius = z*math.sqrt(rate*(1-rate)/total + z*z/(4*total*total)) / (1+z*z/total)
    return max(0, center-radius), min(1, center+radius)


def _within_scope(scope, request):
    return bool(request and request['id'] in scope['request_scope_ids']
        and request['user_turns'] <= scope['maximum_user_turns']
        and (not request['has_tools'] or scope['allow_tools'])
        and (not request['has_history'] or scope['allow_history']))


def _development_matches(policy, demand, request_scope, work_profile):
    return [(key, c) for key, c in policy['cohorts'].items()
            if c['demand'] == demand and c['work_profile'] == work_profile
            and _within_scope(c['evaluation_scope'], request_scope)]


def development_adverse_contracts(policy, demand, *, request_scope=None, work_profile=None):
    """An administrative fallback cannot bypass a known exact-scope negative."""
    if (policy or {}).get('version') != 4:
        return set()
    return {policy['native_contract_sha256'][v]
            for _, c in _development_matches(policy, demand, request_scope, work_profile)
            for v, votes in c['outcomes'].items() if any(x != 'pass' for x in votes)}


def _development_frontier(policy, variants, demand, request_scope, work_profile):
    """Observed development support, explicitly not release qualification.

    Rates use only declared connected training groups. Any known failure or
    unknown in the entire exact cohort vetoes selection, including later
    validation/review evidence. No wins offset negatives, no bounds are claimed.
    """
    result = {'version':4, 'demand':demand, 'work_profile':work_profile,
              'status':'missing_quality_workload_scope', 'eligible':[], 'assessments':{},
              'evidence_stage':'development', 'release_qualified':False, 'quality_guaranteed':False}
    if 'profile_semantics' in policy:
        result['profile_semantics'] = policy['profile_semantics']
    matches = _development_matches(policy, demand, request_scope, work_profile)
    if len(matches) != 1:
        if matches:
            result['status'] = 'ambiguous_quality_workload_scope'
        return result
    key, cohort = matches[0]
    training = set(cohort['training_groups'])
    indexes = [i for i, g in enumerate(cohort['groups']) if g in training]
    n = len(indexes)
    reference = cohort['reference_variant']
    result.update(cohort_id=key, source=cohort['source'], source_sha256=cohort['source_sha256'],
                  evaluation_scope=cohort['evaluation_scope'], training_groups=n,
                  observed_groups=len(cohort['groups']), reference_variant=reference,
                  minimum_observed_success=policy['minimum_observed_success'],
                  maximum_observed_paired_adverse_fraction=policy['maximum_observed_paired_adverse_fraction'])
    for variant in variants:
        all_votes = cohort['outcomes'].get(variant)
        if all_votes is None:
            result['assessments'][variant] = {'status':'quality_evidence_unavailable'}
            continue
        votes = [all_votes[i] for i in indexes]
        reference_votes = [cohort['outcomes'][reference][i] for i in indexes]
        passed = votes.count('pass')
        losses = 0 if variant == reference else sum(v != 'pass' and r != 'fail' for v, r in zip(votes, reference_votes))
        failed, unknown = all_votes.count('fail'), all_votes.count('unknown')
        if failed or unknown:
            status = 'adverse_or_unknown_exact_evidence'
        elif n < policy['minimum_groups']:
            status = 'insufficient_connected_training_groups'
        elif passed/n < policy['minimum_observed_success']:
            status = 'below_observed_development_success'
        elif losses/n > policy['maximum_observed_paired_adverse_fraction']:
            status = 'observed_development_loss_exceeded'
        else:
            status = 'meets_development_screen'
            result['eligible'].append(variant)
        result['assessments'][variant] = {'status':status, 'training_groups':n,
            'training_passes':passed, 'observed_success':passed/n, 'paired_adverse_groups':losses,
            'observed_paired_adverse_fraction':losses/n, 'all_observed_failures':failed,
            'all_observed_unknowns':unknown}
    result['status'] = 'development_screened' if result['eligible'] else 'no_model_meets_development_screen'
    return result


def _paired_frontier(policy, variants, demand, request_scope, work_profile):
    """An absolute quality target plus a conservative paired-loss screen.

    Loss is candidate-not-pass against reference-not-fail on the same group.
    Unknowns take their adverse interpretation; candidate wins do not cancel
    losses. This bounds a stricter quantity than the mean quality difference.
    One-sided Wilson bounds use a Bonferroni adjustment over the declared
    cohorts/variants, not the live surviving pool. Bounds are approximate,
    conditional on independent representative groups, never a quality promise.
    """
    result = {'version': policy['version'], 'demand': demand, 'status': 'quality_evidence_unavailable',
              'eligible': [], 'assessments': {}, 'quality_guaranteed': False}
    if 'profile_semantics' in policy:
        result['profile_semantics'] = policy['profile_semantics']
    if policy['version'] == 3:
        matches = [(key, c) for key, c in policy['cohorts'].items()
                   if c['demand'] == demand and c['work_profile'] == work_profile
                   and _within_scope(c['evaluation_scope'], request_scope)]
        result['work_profile'] = work_profile
        if len(matches) != 1:
            result['status'] = ('ambiguous_quality_workload_scope' if matches
                                else 'missing_quality_workload_scope')
            return result
        key, cohort = matches[0]
        result['cohort_id'] = key
    else:
        cohort = policy['cohorts'].get(demand)
    if cohort is None:
        return result
    scope = cohort['evaluation_scope']
    if not _within_scope(scope, request_scope):
        result['status'] = 'outside_quality_evaluation_scope'
        return result
    n = len(cohort['groups'])
    reference = cohort['reference_variant']
    comparisons = sum(2*len(c['outcomes'])-1 for c in policy['cohorts'].values())
    z = NormalDist().inv_cdf(1-.05/comparisons)
    result.update(source=cohort['source'], source_sha256=cohort['source_sha256'],
                  evaluation_scope=scope, independent_groups=n, reference_variant=reference,
                  minimum_success_lower_bound=policy['minimum_success_lower_bound'],
                  maximum_paired_loss_upper_bound=policy['maximum_paired_loss_upper_bound'],
                  bound_method='one_sided_wilson_bonferroni', nominal_confidence=.95,
                  adjusted_comparisons=comparisons, approximate_bounds=True)
    if n < policy['minimum_groups']:
        result['status'] = 'insufficient_independent_quality_groups'
        return result
    reference_votes = cohort['outcomes'][reference]
    for variant in variants:
        votes = cohort['outcomes'].get(variant)
        if votes is None:
            result['assessments'][variant] = {'status': 'quality_evidence_unavailable'}
            continue
        passed, failed, unknown = (votes.count(x) for x in ('pass', 'fail', 'unknown'))
        lower, _ = _score_bounds(passed, n, z)
        losses = sum(v != 'pass' and r != 'fail' for v, r in zip(votes, reference_votes))
        # The reference compared with its own identical outcomes has zero loss;
        # its absolute quality still has to pass the same evidence requirement.
        upper = 0 if variant == reference else _score_bounds(losses, n, z)[1]
        if lower < policy['minimum_success_lower_bound']:
            status = 'below_absolute_quality_bound'
        elif upper > policy['maximum_paired_loss_upper_bound']:
            status = 'paired_quality_loss_not_supported'
        else:
            status = 'meets_quality_contract'
            result['eligible'].append(variant)
        result['assessments'][variant] = {
            'pass': passed, 'fail': failed, 'unknown': unknown, 'independent_groups': n,
            'pass_rate': passed/n, 'success_lower_bound': lower,
            'paired_adverse_groups': 0 if variant == reference else losses,
            'paired_loss_upper_bound': upper, 'status': status}
    result['status'] = 'quality_screened' if result['eligible'] else 'no_model_meets_quality_contract'
    return result
