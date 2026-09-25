"""Whole-pool replacement must not preserve old capability grants by omission."""
import copy
import json

import pytest

from routing.v7.catalog import compile_uniform_catalog
from routing.v7.candidate import CalibratedRouter
from routing.v7.task_profile import FAMILIES
from test_v14_qualification import CELL, DESC, POLICY


def snapshot():
    variants = {vid: dict(model=model, effort=None, transport='chat_completions',
                         total_output_allowance=8000, cache_write_mode='ordinary_input')
                for vid, model in [('old', 'gpt-5.4'), ('new', 'global.anthropic.claude-opus-5-5'),
                                   ('classifier', 'global.openai.gpt-5.6-luna')]}
    records = []
    for vid in variants:
        for family in FAMILIES:
            records.append(dict(variant=vid, family=family, sample_count=8, **{'pass': 8}, fail=0,
                unknown=0, wilson95=[.67, 1], demand_coverage=['standard'], eligible_local_beta=True,
                profile_evidence=[copy.deepcopy(CELL)] if family == 'development' and vid != 'classifier' else []))
    return dict(revision='test-v14', source_sha256='fixture', variants=variants,
                qualification_policy=copy.deepcopy(POLICY), records=records)


def compile(value):
    return compile_uniform_catalog(value, classifier_variant='classifier', baseline_variant='old')


def test_complete_pool_contains_only_snapshot_identities_and_disables_missing_evidence():
    value = snapshot();before = copy.deepcopy(value)
    catalog = compile(value)
    assert set(catalog['variants']) == set(value['variants'])
    assert not catalog['variants']['classifier']['enabled']
    assert all(v['calibration_contract']['qualification_policy'] == POLICY for v in catalog['variants'].values())
    assert catalog['variants']['old']['calibration_contract']['families'].keys() == {'development'}
    assert all(v['cache_write_mode']=='ordinary_input' for v in catalog['variants'].values())
    assert value == before


@pytest.mark.parametrize('which', ['old', 'new'])
def test_only_evidence_supported_identity_is_selected_regardless_of_model_age(which):
    value = snapshot()
    for row in value['records']:
        if row['variant'] != which:
            row['profile_evidence'] = []
    router = CalibratedRouter(object(), catalog=compile(value))
    result = router.select({**DESC, 'operation': 'design', 'demand': 'standard', 'effort_need': 'low'})
    assert result['variant'] == which
    assert router.classifier.variant == 'classifier'
    assert result['family_qualification']['eligible'] == [which]


def test_stale_positive_family_aggregate_cannot_override_narrow_failure():
    value = snapshot()
    for row in value['records']:
        if row['variant'] == 'new' and row['family'] == 'development':
            row['profile_evidence'][0].update(fail=1)
    catalog = compile(value)
    assert not catalog['variants']['new']['enabled']
    assert catalog['variants']['new']['calibration_contract']['family_evidence']['development']['profile_evidence'][0]['fail'] == 1


@pytest.mark.parametrize('mutation', ['missing_family', 'duplicate', 'unknown_variant', 'unknown_policy', 'missing_cache_contract'])
def test_incomplete_or_ambiguous_snapshot_does_not_restore_legacy_pool(mutation):
    value = snapshot()
    if mutation == 'missing_family':value['records'].pop()
    if mutation == 'duplicate':value['records'].append(copy.deepcopy(value['records'][0]))
    if mutation == 'unknown_variant':value['records'][0]['variant'] = 'unknown'
    if mutation == 'unknown_policy':value['qualification_policy']['revision'] = 'unrecognized'
    if mutation == 'missing_cache_contract':value['variants']['old'].pop('cache_write_mode')
    with pytest.raises(ValueError):compile(value)


def test_uniform_catalog_rejects_separately_injected_legacy_policy():
    with pytest.raises(ValueError, match='owns its qualification policy'):
        CalibratedRouter(object(), catalog=compile(snapshot()), policy={'eligible_by_family': {}})


def test_zero_evidence_has_no_unmeasured_fallback_from_old_catalog():
    value = snapshot()
    for row in value['records']:row['profile_evidence'] = []
    router = CalibratedRouter(object(), catalog=compile(value))
    with pytest.raises(ValueError, match='No eligible configured model'):
        router.select({**DESC, 'operation': 'design', 'demand': 'standard', 'effort_need': 'low'})


def test_uniform_pool_survives_real_service_inventory_pricing_and_signed_binding():
    from routing.service import GenerationRouter, ClassifierTransport, resolve, decode_pin
    from test_auto_routing_service import fixture, request
    value = snapshot()
    for row in value['records']:
        if row['variant'] != 'new':row['profile_evidence'] = []
    catalog = compile(value)
    args = fixture()
    args['router'] = GenerationRouter(ClassifierTransport(), catalog=catalog, output_policy='measured')
    names = [v['model'] for v in catalog['variants'].values()]
    args['models'] = [dict(name=n, project_id=7, context_window=128000, max_output_tokens=16000,
                           openai_compatible=True) for n in names]
    args['price_snapshot']['entries'] = [dict(model_name=n, input_cost_per_token='.000001',
        output_cost_per_token='.000002', cache_read_input_token_cost='.0000001') for n in names]
    calls = []
    def complete(model, messages, **kwargs):
        calls.append(model)
        descriptor = {**DESC, 'operation': 'design', 'demand': 'standard', 'effort_need': 'low',
            'input_status': 'provided', 'retrieval_source_ids': [], 'reference_ids': [], 'reason': 'Bounded implementation',
            'task_continuity': {'action': 'independent', 'task_id': None, 'summary': ''}}
        return {'message': {'content': json.dumps(descriptor)}, 'finish_reason': 'stop'}
    result = resolve(request('Implement the reservation ledger under the supplied contract.'), complete=complete, **args)
    assert calls == [value['variants']['classifier']['model']]
    assert result['config']['model_name'] == value['variants']['new']['model']
    assert result['trace']['selection']['family_qualification']['eligible'] == ['new']
    pin = decode_pin(result['pin'], args['signing_key'], project_id=7, user_id=2, settings=args['settings'], now=101)
    assert pin['config'] == result['config']
