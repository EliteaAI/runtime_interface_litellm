"""#6826: canonical identity join, deterministic duplicates and the configured classifier."""
import copy
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from routing.inventory import qualified_inventory, select_deployment
from routing.service import resolve, decode_pin, RoutingUnavailable, is_anthropic
from routing.v7.catalog import compile_catalog
from test_auto_routing_service import fixture, request, classifier_design, identity, CANONICAL

GRAMMAR = re.compile(r'^[a-z]+/[a-z0-9][a-z0-9-]*$')
LUNA = 'openai/gpt-5-6-luna'
CATALOG = {'variants': {
    'luna-default': {'model': 'global.openai.gpt-5.6-luna', 'canonical_model': LUNA, 'effort': None},
    'luna-high': {'model': 'global.openai.gpt-5.6-luna', 'canonical_model': LUNA, 'effort': 'high'}}}
# Customer (Azure/DIAL, Bedrock without region) names from fixtures/model_limits.csv.
DIAL = {'global.openai.gpt-5.6-luna': 'gpt-5.6-luna-2026-07-09', 'gpt-5.4': 'gpt-5.4-2026-03-05',
        'gpt-5.4-mini': 'gpt-5.4-mini-2026-03-17',
        'eu.anthropic.claude-haiku-4-5-20251001-v1:0': 'anthropic.claude-haiku-4-5-20251001-v1:0',
        'eu.anthropic.claude-sonnet-4-6': 'anthropic.claude-sonnet-4-6'}


def item(name, canonical=LUNA, owner=7, available=None, **kwargs):
    value = {'name': name, 'project_id': owner, 'identity': identity(canonical, **kwargs)}
    if available is not None:
        value['available'] = available
    return value


def inventory(*items):
    return {i['name']: i for i in items}


def customer(args):
    for model in args['models']:
        model['name'] = DIAL[model['name']]
    for entry in args['price_snapshot']['entries']:
        entry['model_name'] = DIAL[entry['model_name']]
    args['settings']['classifier']['name'] = DIAL['global.openai.gpt-5.6-luna']
    return args


@pytest.mark.parametrize('revision', ['v9', 'v12'])
def test_every_compiled_variant_declares_the_expected_canonical_model(revision):
    variants = compile_catalog(revision)['variants']
    assert {v['model'] for v in variants.values()} == set(CANONICAL)
    for vid, variant in variants.items():
        assert GRAMMAR.fullmatch(variant['canonical_model']), vid
        assert variant['canonical_model'] == CANONICAL[variant['model']], vid


def test_customer_name_joins_measured_contract_by_canonical_identity():
    models = inventory(item('gpt-5.6-luna-2026-07-09'))
    available, exclusions = qualified_inventory(models, CATALOG, project_id=7)
    assert {vid: m['name'] for vid, m in available.items()} == {'luna-default': 'gpt-5.6-luna-2026-07-09',
                                                                 'luna-high': 'gpt-5.6-luna-2026-07-09'}
    assert exclusions == []


def test_reasoning_deployment_joins_only_effort_variants_and_is_preferred_there():
    models = inventory(item('gpt-5.6-luna-2026-07-09'), item('gpt-5.6-luna-2026-07-09-reasoning', contract='reasoning'))
    available, exclusions = qualified_inventory(models, CATALOG, project_id=7)
    assert available['luna-default']['name'] == 'gpt-5.6-luna-2026-07-09'
    assert available['luna-high']['name'] == 'gpt-5.6-luna-2026-07-09-reasoning'
    assert exclusions == []


def test_reasoning_deployment_without_effort_contract_is_unmeasured():
    catalog = {'variants': {'luna-default': CATALOG['variants']['luna-default']}}
    models = inventory(item('gpt-5.6-luna-2026-07-09-reasoning', contract='reasoning'))
    assert qualified_inventory(models, catalog, project_id=7) == ({}, [
        {'model': 'gpt-5.6-luna-2026-07-09-reasoning', 'model_project_id': 7, 'reason': 'NO_CALIBRATED_MODEL_CONTRACT'}])


def test_websearch_and_non_chat_deployments_never_join():
    models = inventory(item('luna-websearch', contract='websearch'), item('luna-embed', kind='embedding'))
    available, exclusions = qualified_inventory(models, CATALOG, project_id=7)
    assert available == {}
    assert [e['reason'] for e in exclusions] == ['NO_CALIBRATED_MODEL_CONTRACT'] * 2


def test_duplicate_loser_is_listed_with_the_selected_winner_never_merged():
    models = inventory(item('global.openai.gpt-5.6-luna'), item('gpt-5.6-luna-2026-07-09'))
    available, exclusions = qualified_inventory(models, CATALOG, project_id=7)
    assert {m['name'] for m in available.values()} == {'gpt-5.6-luna-2026-07-09'}
    assert exclusions == [{'model': 'global.openai.gpt-5.6-luna', 'model_project_id': 7,
                           'reason': 'DUPLICATE_CANONICAL_DEPLOYMENT', 'selected': 'gpt-5.6-luna-2026-07-09'}]


@pytest.mark.parametrize('winner,loser', [
    (item('zzz-long-explicit-name', source='explicit'), item('a')),
    (item('zzz-long-project-name'), item('a', owner=1)),
    (item('zzz-long-reasoning-name', contract='reasoning'), item('a')),
    (item('bb'), item('aaa')),
    (item('ab'), item('ba')),
])
@pytest.mark.parametrize('reverse', [False, True])
def test_select_deployment_order_is_deterministic(winner, loser, reverse):
    candidates = [loser, winner] if reverse else [winner, loser]
    assert select_deployment(candidates, CATALOG['variants']['luna-high'], project_id=7) is winner


def test_default_variant_prefers_default_contract_over_shorter_reasoning_name():
    default, reasoning = item('luna-default-name'), item('l', contract='reasoning')
    assert select_deployment([reasoning, default], {'effort': None}, project_id=7) is default


def test_unavailable_duplicate_keeps_its_own_reason_and_cannot_win():
    models = inventory(item('a', available=False), item('gpt-5.6-luna-2026-07-09'))
    models['a']['exclusion_reason'] = 'unhealthy_configuration'
    available, exclusions = qualified_inventory(models, CATALOG, project_id=7)
    assert {m['name'] for m in available.values()} == {'gpt-5.6-luna-2026-07-09'}
    assert exclusions == [{'model': 'a', 'model_project_id': 7, 'reason': 'unhealthy_configuration'}]


def test_legacy_item_without_identity_joins_by_exact_name_only():
    models = {'global.openai.gpt-5.6-luna': {'name': 'global.openai.gpt-5.6-luna', 'project_id': 7},
              'gpt-5.6-luna-2026-07-09': {'name': 'gpt-5.6-luna-2026-07-09', 'project_id': 7}}
    available, exclusions = qualified_inventory(models, CATALOG, project_id=7)
    assert {m['name'] for m in available.values()} == {'global.openai.gpt-5.6-luna'}
    assert exclusions == [{'model': 'gpt-5.6-luna-2026-07-09', 'model_project_id': 7,
                           'reason': 'NO_CALIBRATED_MODEL_CONTRACT'}]


def test_unresolved_identity_is_reported_and_never_joins_by_name():
    models = inventory(item('global.openai.gpt-5.6-luna', canonical=None, source='unresolved'))
    assert qualified_inventory(models, CATALOG, project_id=7) == ({}, [
        {'model': 'global.openai.gpt-5.6-luna', 'model_project_id': 7,
         'reason': 'NO_CALIBRATED_MODEL_CONTRACT', 'identity_source': 'unresolved'}])


def test_customer_named_inventory_routes_greeting_and_binds_exact_deployment():
    args = customer(fixture())
    result = resolve(request(), complete=lambda *a, **k: pytest.fail('Greeting classified'), **args)
    assert result['config']['model_name'] in DIAL.values()
    assert result['trace']['inventory']['excluded'] == []
    assert result['trace']['inventory']['qualified_variants'] == 8
    pin = decode_pin(result['pin'], args['signing_key'], project_id=7, user_id=2, settings=args['settings'], now=101)
    assert pin['model_binding']['name'] == result['config']['model_name']


def test_configured_classifier_is_called_on_its_exact_deployment_without_effort():
    args = customer(fixture())
    calls = []
    def complete(model, messages, **kwargs):
        calls.append((model, kwargs['effort']))
        return classifier_design()
    result = resolve(request('Design recovery durability.'), complete=complete, **args)
    assert calls == [('gpt-5.6-luna-2026-07-09', None)]
    assert result['config']['model_name'] in DIAL.values()
    assert result['trace']['classifier']['classifier_variant'] == 'configured-classifier'
    assert result['trace']['classifier']['deployment'] == {
        'name': 'gpt-5.6-luna-2026-07-09', 'project_id': 7, 'source': 'platform'}
    assert result['trace']['classifier']['called'] is True


def test_uncalibrated_low_tier_model_can_classify():
    args = fixture()
    args['models'].append({'name': 'claude-haiku-5-5@default', 'project_id': 1, 'shared': True,
                           'identity': identity('anthropic/claude-haiku-5-5')})
    args['price_snapshot']['entries'].append(dict(args['price_snapshot']['entries'][0], model_name='claude-haiku-5-5@default'))
    args['settings']['classifier'] = {'name': 'claude-haiku-5-5@default', 'project_id': 1, 'source': 'project_low_tier'}
    calls = []
    result = resolve(request('Design recovery durability.'),
                     complete=lambda model, *a, **k: calls.append(model) or classifier_design(), **args)
    assert calls == ['claude-haiku-5-5@default']
    assert result['trace']['classifier']['deployment'] == {
        'name': 'claude-haiku-5-5@default', 'project_id': 1, 'source': 'project_low_tier'}


def no_classifier_key(args):
    args['settings'].pop('classifier')


def unpriced(args):
    args['price_snapshot']['entries'] = [e for e in args['price_snapshot']['entries']
                                         if e['model_name'] != 'global.openai.gpt-5.6-luna']


def unresolved_pool(args):
    for model in args['models']:
        model['identity'] = identity(None, source='unresolved')


@pytest.mark.parametrize('change,reason', [
    (no_classifier_key, 'CLASSIFIER_NOT_CONFIGURED'),
    (lambda a: a['settings'].update(classifier=None), 'CLASSIFIER_NOT_CONFIGURED'),
    (lambda a: a['settings']['classifier'].update(project_id=1), 'CLASSIFIER_UNAVAILABLE'),
    (lambda a: a['settings']['classifier'].update(name='gone'), 'CLASSIFIER_UNAVAILABLE'),
    (lambda a: next(m for m in a['models'] if m['name'] == 'global.openai.gpt-5.6-luna').update(available=False),
     'CLASSIFIER_UNAVAILABLE'),
    (lambda a: next(m for m in a['models'] if m['name'] == 'global.openai.gpt-5.6-luna').update(
        identity=identity(LUNA, kind='embedding')), 'CLASSIFIER_UNAVAILABLE'),
    (unpriced, 'CLASSIFIER_PRICE_UNAVAILABLE'),
    (unresolved_pool, 'NO_QUALIFIED_MODELS'),
])
def test_classifier_and_pool_failures_are_reason_coded_never_a_default_model(change, reason):
    args = fixture()
    change(args)
    with pytest.raises(RoutingUnavailable) as raised:
        resolve(request(), complete=lambda *a, **k: pytest.fail('No classifier call'), **args)
    assert raised.value.reason == reason


def test_older_configurations_without_identity_still_route_by_exact_name():
    args = fixture()
    for model in args['models']:
        model.pop('identity')
    result = resolve(request(), complete=lambda *a, **k: pytest.fail('Greeting classified'), **args)
    assert result['trace']['inventory']['qualified_variants'] == 8


def test_trace_records_price_match_kind():
    args = fixture()
    entry = next(e for e in args['price_snapshot']['entries'] if e['model_name'] == 'gpt-5.4')
    entry.update(match='alias', matched_name='azure/gpt-5.4')
    result = resolve(request(), complete=lambda *a, **k: pytest.fail('Greeting classified'), **args)
    assert result['trace']['prices']['match']['gpt-5.4'] == 'alias'
    assert result['trace']['prices']['match']['gpt-5.4-mini'] == 'exact'
    assert result['trace']['prices']['aliases'] == {'gpt-5.4': 'azure/gpt-5.4'}


@pytest.mark.parametrize('model,variant,expected', [
    ({'identity': identity('anthropic/claude-haiku-5-5')}, {'model': 'custom-name'}, True),
    ({'identity': identity('openai/gpt-5-4')}, {'model': 'eu.anthropic.claude-sonnet-4-6'}, False),
    ({}, {'model': 'eu.anthropic.claude-sonnet-4-6'}, True),
    ({}, {'model': 'gpt-5.4'}, False),
])
def test_vendor_comes_from_identity_when_present(model, variant, expected):
    assert is_anthropic(model, variant) is expected


@pytest.mark.parametrize('why,reason,message', [
    ({'code': 'CLASSIFIER_UNAVAILABLE', 'message': 'Classifier model luna is no longer available to this project',
      'model': {'name': 'luna', 'project_id': 2}}, 'CLASSIFIER_UNAVAILABLE',
     'Classifier model luna is no longer available to this project'),
    ({'code': 'CLASSIFIER_NOT_CHAT', 'message': 'Classifier model embed is not a chat model', 'model': None},
     'CLASSIFIER_NOT_CHAT', 'Classifier model embed is not a chat model'),
    ({'code': 'CLASSIFIER_NOT_CONFIGURED', 'message': 'No classifier is selected', 'model': None},
     'CLASSIFIER_NOT_CONFIGURED', 'No classifier is selected'),
    (None, 'CLASSIFIER_NOT_CONFIGURED', 'No Auto classifier model is configured'),
    ({'code': 'SOMETHING_ELSE', 'message': 'Untrusted code'}, 'CLASSIFIER_NOT_CONFIGURED',
     'No Auto classifier model is configured'),
])
def test_unresolved_classifier_passes_configurations_reason_through(why, reason, message):
    args = fixture()
    args['settings'].update(classifier=None, classifier_reason=why)
    with pytest.raises(RoutingUnavailable) as raised:
        resolve(request(), complete=lambda *a, **k: pytest.fail('No classifier call'), **args)
    assert (raised.value.reason, str(raised.value)) == (reason, message)


def test_older_configurations_without_classifier_reason_keeps_not_configured_text():
    args = fixture()
    args['settings']['classifier'] = None
    with pytest.raises(RoutingUnavailable) as raised:
        resolve(request(), complete=lambda *a, **k: pytest.fail('No classifier call'), **args)
    assert (raised.value.reason, str(raised.value)) == ('CLASSIFIER_NOT_CONFIGURED', 'No Auto classifier model is configured')
