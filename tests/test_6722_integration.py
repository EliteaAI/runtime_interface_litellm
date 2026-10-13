"""#6722 x #6826 integration: installed calibration profiles use canonical identity and the configured classifier."""
import json
import re
from decimal import Decimal

from routing import bundle, service
from routing.pricing import PriceBook
from routing.v7.catalog import SNAPSHOT_CANONICAL_MODEL, compile_uniform_catalog
from test_6826_canonical_join import identity
from test_auto_routing_service import fixture, request
from test_configured_selection_policy import args_with_policy, matching, with_evidence
from test_v14_economics import fixture as economics_fixture, ranked
from test_v14_installed_bundle import installed  # noqa: F401 (pytest fixture)

REVISION = 'v14-application-r35'
GRAMMAR = re.compile(r'^[a-z]+/[a-z0-9][a-z0-9-]*$')


def bounded_args(models, classifier):
    settings = bundle.runtime_settings({'enabled': True, 'revision': 'unit-gate'},
        {'auto_routing_calibration_profiles': {'7': {'profile': 'bounded-text-conversation', 'revision': REVISION}}}, 7)
    settings['classifier'] = {'name': classifier, 'project_id': 7, 'source': 'project'}
    args = fixture(); args.pop('router'); args['settings'] = settings
    args['models'] = [dict(project_id=7, context_window=200000, max_output_tokens=128000, **m) for m in models]
    args['price_snapshot']['entries'] = [dict(model_name=m['name'], input_cost_per_token='.000001',
        output_cost_per_token='.000002', cache_read_input_token_cost='.0000001',
        cache_creation_input_token_cost='.00000125') for m in models]
    return args


def transform(calls):
    def complete(model, messages, **kw):
        calls.append(model)
        desc = dict(operation='transform', task_family='transformation', demand='simple', effort_need='low',
            input_status='provided', needs_context=False, relation='independent', retrieval_source_ids=[],
            reference_ids=[], reason='Bounded supplied text transformation',
            work_profile=dict(work='transform', reasoning='bounded', evidence='supplied', creativity='none', verification='check'),
            task_continuity=dict(action='independent', task_id=None, summary=''))
        return {'message': {'content': json.dumps(desc)}, 'finish_reason': 'stop'}
    return complete


PROMPT = 'Convert supplied entries alpha=1 and beta=2 to the requested JSON object.'


def test_every_installed_profile_variant_has_a_canonical_identity():
    manifest = json.loads((bundle.ROOT/'manifest.json').read_text())
    for profile in manifest['profiles']:
        value, _ = bundle.snapshot(profile, manifest['revision'])
        catalog = compile_uniform_catalog(value, classifier_variant=value['classifier_variant'],
                                          baseline_variant=value['baseline_variant'])
        for vid, variant in catalog['variants'].items():
            assert GRAMMAR.match(variant['canonical_model'] or ''), (profile, vid, variant['model'])
            assert variant['canonical_model'] == SNAPSHOT_CANONICAL_MODEL[variant['model']]
    # The reviewed snapshot bytes are untouched: their manifest digests still verify.
    assert all(bundle.snapshot(p, manifest['revision']) for p in manifest['profiles'])


def test_calibrated_router_calls_the_configured_classifier_not_the_snapshot_label():
    models = [dict(name='eu.anthropic.claude-sonnet-4-6', openai_compatible=False),
              dict(name='global.openai.gpt-5.6-luna', openai_compatible=True),
              dict(name='customer-haiku', openai_compatible=True,
                   identity=identity('anthropic/claude-haiku-4-5'))]
    args = bounded_args(models, 'customer-haiku')
    calls = []
    result = service.resolve(request(PROMPT), complete=transform(calls), **args)
    assert calls == ['customer-haiku']
    assert result['trace']['classifier']['classifier_variant'] == 'configured-classifier'
    assert result['trace']['classifier']['deployment'] == {'name': 'customer-haiku', 'project_id': 7, 'source': 'project'}
    # The snapshot's label remains visible on the compiled catalog only.
    assert service.configured_router(args['settings']).catalog['classifier_variant'] == 'm09-default'


def test_calibrated_router_without_configured_classifier_fails_closed():
    models = [dict(name='eu.anthropic.claude-sonnet-4-6', openai_compatible=False),
              dict(name='global.openai.gpt-5.6-luna', openai_compatible=True)]
    args = bounded_args(models, 'global.openai.gpt-5.6-luna')
    args['settings'].pop('classifier')
    try:
        service.resolve(request(PROMPT), complete=transform([]), **args)
    except service.RoutingUnavailable as exc:
        assert exc.reason == 'CLASSIFIER_NOT_CONFIGURED'
    else:
        raise AssertionError('A calibrated profile must not fall back to its snapshot classifier')


def test_measured_snapshot_variant_binds_a_renamed_deployment_by_canonical_identity():
    models = [dict(name='claude-sonnet-4-6-customer', openai_compatible=False,
                   identity=identity('anthropic/claude-sonnet-4-6')),
              dict(name='gpt-5.6-luna-2026-07-09', openai_compatible=True,
                   identity=identity('openai/gpt-5-6-luna'))]
    args = bounded_args(models, 'gpt-5.6-luna-2026-07-09')
    calls = []
    result = service.resolve(request(PROMPT), complete=transform(calls), **args)
    assert calls == ['gpt-5.6-luna-2026-07-09']
    inventory = result['trace']['inventory']
    assert inventory['excluded'] == [] and inventory['qualified_variants'] == 2
    assert result['config']['model_name'] in {'claude-sonnet-4-6-customer', 'gpt-5.6-luna-2026-07-09'}
    pin = service.decode_pin(result['pin'], args['signing_key'], project_id=7, user_id=2,
                             settings=args['settings'], now=101)
    assert pin['model_binding']['name'] == result['config']['model_name']


def test_configured_pool_binds_its_exact_deployment_when_inventory_carries_identity(installed):
    args = args_with_policy(installed)
    for model in args['models']:
        model['identity'] = identity('openai/' + re.sub('[^a-z0-9-]', '-', model['name'].lower()))
    result = service.resolve(request('Design this component.'), complete=matching, **args)
    assert result['config']['model_name'] == 'customer-small'
    excluded = {row['model'] for row in result['trace']['inventory']['excluded']}
    assert not {'customer-small', 'customer-large'} & excluded


def test_alias_deployment_cannot_hide_adverse_evidence_of_its_canonical_model(installed):
    args = with_evidence(installed, adverse=True)
    entry = args['settings']['calibration_selection_policy']['variants']['customer-small']
    entry['evidence_ref'] = None  # No owner alias assertion: only canonical identity links the evidence.
    for model in args['models']:
        if model['name'] == 'customer-small':
            model['identity'] = identity('openai/gpt-5-4')  # the measured 'old' variant (gpt-5.4)
    result = service.resolve(request('Design it'), complete=matching, **args)
    assert result['config']['model_name'] == 'customer-large'
    exclusions = result['trace']['selection']['family_qualification']['exclusions']
    assert 'EXISTING_EXACT_EVIDENCE_HOLD' in exclusions['configured-customer-small']


def test_measured_trajectory_prices_the_bound_deployment_not_the_measured_name():
    args = economics_fixture()
    args[2].prices = PriceBook([dict(model_name='dep-'+v, input_cost_per_token='.000001', output_cost_per_token=p,
        cache_read_input_token_cost='.0000001') for v, p in [('a', '.000002'), ('b', '.000001')]], source_revision='test')
    args[2].deployments = {'a': 'dep-a', 'b': 'dep-b'}
    result = ranked(args)
    assert result['economics']['forecast_comparable'] and result['variant'] == 'b'
    assert Decimal(result['economics']['quotes']['b']['ranking_usd']) == Decimal('.0009')
