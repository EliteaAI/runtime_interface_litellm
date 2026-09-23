"""Explain measured admission without treating absent evidence as model failure."""
import copy


def assess(contract, descriptor, demand):
    family = descriptor.get('task_family')
    admitted = contract['families'].get(family)
    cell = contract.get('family_evidence', contract['families']).get(family)
    result = {'eligible': False, 'family': family, 'requested_demand': demand,
              'revision': contract['revision'], 'source_sha256': contract['source_sha256']}
    if cell is None:
        return {**result, 'status': 'unmeasured_family', 'sample_count': 0}
    result.update({k: copy.deepcopy(cell[k]) for k in (
        'sample_count', 'pass', 'fail', 'unknown', 'wilson95', 'demand_coverage',
        'quality_fail', 'refused') if k in cell})
    # A refusal is an unsuccessful delivery, not evidence of a correctness
    # judgment. Older profiles do not contain that distinction; retain their
    # original aggregate rather than retrospectively inferring one.
    if cell.get('fail', 0):
        status = ('provider_refusal' if cell.get('refused') == cell['fail'] else 'measured_failure')
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
