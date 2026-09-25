"""Explain measured admission without treating absent evidence as model failure."""
import copy


def cell_status(cell, policy):
    if cell.get('fail',0):return 'provider_refusal' if cell.get('refused')==cell['fail'] else 'measured_failure'
    if cell.get('disagreement',0):return 'disputed_assessment'
    if cell.get('unknown',0):return 'unresolved_measurement'
    if cell.get('delivery_failure',0):return 'incomplete_delivery'
    if not cell.get('compatible_contracts',False):return 'incompatible_measurement_contract'
    if cell.get('independent_templates',0)<policy['minimum_templates']:return 'insufficient_independent_templates'
    if cell.get('wilson95',[0])[0]<policy['minimum_wilson_lower']:return 'insufficient_quality_confidence'
    return 'provisional_qualified'


def uniform_assessment(contract, descriptor, demand):
    """V14 uses identical evidence scopes for every configured model identity.

    Broader cells require offline validation; a narrow negative cannot be hidden
    by pooling it with unrelated successes. Policy thresholds are frozen with
    the calibration snapshot, not derived from provider names or prices.
    """
    policy=contract['qualification_policy']
    result={'eligible':False,'family':descriptor.get('task_family'),'requested_demand':demand,
            'revision':contract['revision'],'source_sha256':contract['source_sha256'],
            'policy_revision':policy['revision']}
    profile=descriptor.get('work_profile')
    if not isinstance(profile,dict) or descriptor.get('needs_context') or descriptor.get('relation')=='ambiguous':
        return {**result,'status':'unresolved_task','sample_count':0}
    family=contract.get('family_evidence',{}).get(descriptor.get('task_family'),{})
    rows=family.get('profile_evidence',[])
    exact=[r for r in rows if r.get('work_profile')==profile and demand in r.get('demand_coverage',[])]
    if len(exact)>1:return {**result,'status':'conflicting_evidence_cells','sample_count':0}
    cell=exact[0] if exact else None
    basis='exact_profile'
    sparse={'insufficient_independent_templates','insufficient_quality_confidence'}
    if cell is None or cell_status(cell,policy) in sparse:
        pools=[r for r in family.get('validated_pools',[]) if demand in r.get('demand_coverage',[])
               and r.get('profile_scope') and all(profile.get(k) in v for k,v in r['profile_scope'].items())
               and set(r['profile_scope'])==set(profile) and r.get('pooling_validation',{}).get('passed') is True
               and profile in r.get('validated_profiles',[])
               and r['pooling_validation'].get('method')=='leave-template-out-v1'
               and r['pooling_validation'].get('independent_templates',0)>=policy['minimum_validation_templates']]
        # Ambiguous overlapping pools are a compiler defect, never best-of-pool shopping.
        if len(pools)>1:
            return {**result,'status':'conflicting_evidence_cells','sample_count':0}
        if pools:
            cell=pools[0];basis='validated_profile_pool'
        elif cell is None:
            return {**result,'status':'unmeasured_work_profile','sample_count':0}
    result.update({k:copy.deepcopy(cell[k]) for k in ('sample_count','independent_templates','pass','fail',
        'unknown','disagreement','delivery_failure','refused','wilson95','demand_coverage') if k in cell})
    result['evidence_basis']=basis
    if basis=='validated_profile_pool':result['pooling_validation']=copy.deepcopy(cell['pooling_validation'])
    status=cell_status(cell,policy);result['eligible']=status=='provisional_qualified'
    return {**result,'status':status}


def assess(contract, descriptor, demand):
    if 'qualification_policy' in contract:
        if contract['qualification_policy'].get('revision') != 'uniform-profile-v1':
            raise ValueError('Unknown qualification policy revision')
        return uniform_assessment(contract,descriptor,demand)
    family = descriptor.get('task_family')
    admitted = contract['families'].get(family)
    cell = contract.get('family_evidence', contract['families']).get(family)
    result = {'eligible': False, 'family': family, 'requested_demand': demand,
              'revision': contract['revision'], 'source_sha256': contract['source_sha256']}
    if cell is None:
        return {**result, 'status': 'unmeasured_family', 'sample_count': 0}
    if 'profile_evidence' in cell:
        matches = [row for row in cell['profile_evidence']
                   if row['work_profile'] == descriptor.get('work_profile')]
        if len(matches) != 1:
            return {**result, 'status': 'unmeasured_work_profile', 'sample_count': 0}
        cell = matches[0]
        admitted = cell if cell.get('eligible_local_beta') else None
        result['work_profile'] = copy.deepcopy(cell['work_profile'])
    result.update({k: copy.deepcopy(cell[k]) for k in (
        'sample_count', 'pass', 'fail', 'unknown', 'wilson95', 'demand_coverage',
        'quality_fail', 'refused', 'disagreement', 'delivery_failure') if k in cell})
    # A refusal is an unsuccessful delivery, not evidence of a correctness
    # judgment. Older profiles do not contain that distinction; retain their
    # original aggregate rather than retrospectively inferring one.
    if cell.get('fail', 0):
        status = ('provider_refusal' if cell.get('refused') == cell['fail'] else 'measured_failure')
    elif cell.get('disagreement', 0):
        status = 'disputed_assessment'
    elif cell.get('delivery_failure', 0):
        status = 'incomplete_delivery'
    elif cell.get('unknown', 0):
        status = 'unresolved_measurement'
    elif cell.get('sample_count', 0) < 3:
        status = 'insufficient_samples'
    elif not admitted:
        status = 'not_qualified'
    elif demand not in admitted['demand_coverage']:
        status = 'unobserved_demand'
    elif (descriptor.get('needs_context') or descriptor.get('relation') == 'ambiguous'
          or descriptor.get('operation') in {'other', 'greeting'}):
        status = 'unresolved_task'
    else:
        status = 'provisional_qualified'
        result['eligible'] = True
    return {**result, 'status': status}
