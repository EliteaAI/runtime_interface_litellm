"""Keep failure, missing support and untested demand distinct without new grants."""
import copy

import pytest

from routing.v7.catalog import compile_catalog, effort_candidate
from routing.v7.candidate import CalibratedRouter
from routing.v7.qualification import assess


DESC = dict(operation='analysis', task_family='architecture', demand='deep',
            effort_need='low', relation='independent', needs_context=False)
CELL = dict(sample_count=3, **{'pass': 3}, fail=0, unknown=0,
            wilson95=[.4385, 1], demand_coverage=['deep'])


def contract(cell=None, admitted=True):
    cell = copy.deepcopy(CELL if cell is None else cell)
    return dict(revision='fixture-r1', source_sha256='fixture',
                families={'architecture': cell} if admitted else {},
                family_evidence={'architecture': cell})


@pytest.mark.parametrize('change,status', [
    ({'pass': 2, 'fail': 1}, 'measured_failure'),
    ({'pass': 2, 'fail': 1, 'quality_fail': 0, 'refused': 1}, 'provider_refusal'),
    ({'pass': 2, 'unknown': 1}, 'unresolved_measurement'),
    ({'pass': 2, 'sample_count': 2}, 'insufficient_samples'),
])
def test_unsuccessful_and_insufficient_cells_remain_ineligible(change, status):
    row = assess(contract({**CELL, **change}, admitted=False), DESC, 'deep')
    assert row['status'] == status and row['eligible'] is False
    assert row['sample_count'] == change.get('sample_count', 3)


def test_unmeasured_family_and_unobserved_demand_are_not_failures():
    row = assess(contract(), {**DESC, 'task_family': 'development'}, 'deep')
    assert row['status'] == 'unmeasured_family' and row['sample_count'] == 0
    row = assess(contract(), DESC, 'standard')
    assert row['status'] == 'unobserved_demand' and row['fail'] == 0


def test_legacy_contract_and_context_checks_preserve_admission():
    value = contract()
    value.pop('family_evidence')
    before = copy.deepcopy(value)
    assert assess(value, DESC, 'deep')['eligible']
    for change in ({'needs_context': True}, {'relation': 'ambiguous'}, {'operation': 'other'}):
        assert assess(value, {**DESC, **change}, 'deep')['status'] == 'unresolved_task'
    assert value == before


def test_every_original_profile_cell_has_traceable_evidence():
    catalog = compile_catalog()
    for cell in effort_candidate()['records']:
        c = catalog['variants'][cell['variant']]['calibration_contract']
        evidence = c['family_evidence'][cell['family']]
        for key in ('sample_count', 'pass', 'fail', 'unknown', 'wilson95', 'demand_coverage'):
            assert evidence[key] == cell[key]
        assert (cell['family'] in c['families']) == cell['eligible_local_beta']


def test_completely_unqualified_preset_is_retained_disabled(monkeypatch):
    candidate = copy.deepcopy(effort_candidate())
    target = next(iter(candidate['variants']))
    for row in candidate['records']:
        if row['variant'] == target:
            row.update(eligible_local_beta=False, demand_coverage=[])
    monkeypatch.setattr('routing.v7.catalog.effort_candidate', lambda: candidate)
    variant = compile_catalog()['variants'][target]
    assert variant['enabled'] is False
    assert variant['calibration_contract']['families'] == {}
    assert len(variant['calibration_contract']['family_evidence']) == 20


def test_selector_reports_negative_evidence_and_retains_baseline():
    cell = next(r for r in effort_candidate()['records'] if r['fail'] > 0)
    result = CalibratedRouter(object()).select(
        {**DESC, 'task_family': cell['family']}, allowed=['gpt54-high', cell['variant']])
    assert result['variant'] == 'gpt54-high'
    evidence = result['family_qualification']['assessments'][cell['variant']]
    assert evidence['status'] == 'measured_failure'
    assert evidence['fail'] == cell['fail']
    assert evidence['sample_count'] == cell['sample_count']


def test_disagreement_is_not_reported_as_measured_failure():
    row = assess(contract({**CELL, 'pass': 2, 'disagreement': 1}, admitted=False), DESC, 'deep')
    assert row['status'] == 'disputed_assessment'
    assert not row['eligible'] and row['fail'] == 0


def test_profile_evidence_does_not_grant_other_engineering_work():
    profile = dict(work='implement', reasoning='multi_step', evidence='retrieve',
                   creativity='none', verification='check')
    good = {**CELL, 'work_profile': profile, 'eligible_local_beta': True, 'demand_coverage': ['standard']}
    disputed = {**good, 'work_profile': {**profile, 'reasoning': 'interacting_constraints'},
                'pass': 2, 'disagreement': 1, 'eligible_local_beta': False}
    value = contract({**CELL, 'profile_evidence': [good, disputed]})
    before = copy.deepcopy(value)
    assert assess(value, {**DESC, 'work_profile': profile}, 'standard')['eligible']
    assert assess(value, {**DESC, 'work_profile': profile}, 'deep')['status'] == 'unobserved_demand'
    assert assess(value, {**DESC, 'work_profile': disputed['work_profile']}, 'deep')['status'] == 'disputed_assessment'
    assert assess(value, DESC, 'standard')['status'] == 'unmeasured_work_profile'
    assert assess(value, {**DESC, 'work_profile': {**profile, 'work': 'design'}}, 'standard')['status'] == 'unmeasured_work_profile'
    assert value == before


def test_catalog_keeps_narrow_evidence_for_real_selector():
    candidate = copy.deepcopy(effort_candidate())
    candidate['variants'] = {'narrow-'+k: v for k, v in candidate['variants'].items()}
    for row in candidate['records']:
        row['variant'] = 'narrow-'+row['variant']
        row['profile_evidence'] = [{**CELL, 'work_profile': {'work': 'design'},
                                    'eligible_local_beta': False, 'disagreement': 1}]
    catalog = compile_catalog(candidate=candidate)
    for row in candidate['records']:
        observed = catalog['variants'][row['variant']]['calibration_contract']['family_evidence'][row['family']]
        assert observed['profile_evidence'] == row['profile_evidence']
