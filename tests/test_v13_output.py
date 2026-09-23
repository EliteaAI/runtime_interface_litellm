"""Provider defaults are separate from frozen experimental output allowances."""
import json
import pytest
from routing.output import validate_output
from routing.service import ClassifierTransport, GenerationRouter, RoutingUnavailable, resolve
from test_auto_routing_service import fixture, request
from test_v12_efforts import inputs, descriptor


def live_args():
    args = fixture()
    args.pop('router')
    return args


def test_default_uses_model_capacity_and_renews_without_reclassifying():
    args = live_args()
    for m in args['models']:
        m['max_output_tokens'] = 65536
    req = request()
    result = resolve(req, complete=lambda *a, **k: pytest.fail('Greeting classified'), **args)
    assert result['config']['max_tokens'] == 65536
    assert result['config']['routing_output_mode'] == 'provider_default'
    validate_output(result['config'], {'model': result['config']['model_name']})
    req['prior_pin'] = result['pin']
    renewed = resolve(req, complete=lambda *a, **k: pytest.fail('Renewal classified'), **args)
    assert renewed['config'] == result['config']


@pytest.mark.parametrize('cap', [1, 32768, 65536])
def test_explicit_limit_respects_model_not_universal_32k(cap):
    args = live_args()
    for m in args['models']:
        m['max_output_tokens'] = 65536
    req = {**request(), 'output_cap': cap}
    result = resolve(req, complete=lambda *a, **k: None, **args)
    assert result['config']['max_tokens'] == cap
    assert result['config']['routing_output_mode'] == 'explicit'
    validate_output(result['config'], {'max_completion_tokens': cap})
    with pytest.raises(ValueError):
        validate_output(result['config'], {})
    req['prior_pin'] = result['pin']
    renewed = resolve(req, complete=lambda *a, **k: pytest.fail('Renewal classified'), **args)
    assert renewed['config'] == result['config']
    req['output_cap'] = cap+1
    with pytest.raises(RoutingUnavailable):
        resolve(req, complete=lambda *a, **k: pytest.fail('Enlargement classified'), **args)


@pytest.mark.parametrize('cap', [True, 0, -1, 1.5, '65536', 999999999])
def test_invalid_or_over_capacity_request_fails_before_inference(cap):
    with pytest.raises(RoutingUnavailable):
        resolve({**request(), 'output_cap': cap}, complete=lambda *a, **k: pytest.fail('Called'), **live_args())


def test_native_default_uses_configured_maximum_and_marks_measurement_transfer():
    args = inputs('opus5-high')
    args.pop('router')
    args['models'][-1]['max_output_tokens'] = 65536
    req = request('Analyze supplied evidence')
    req['selection']['reasoning'] = {'mode': 'explicit', 'preset': 'high'}
    from routing.v7.catalog import effort_candidate
    cell = next(r for r in effort_candidate()['records'] if r['variant'] == 'opus5-high' and r['eligible_local_beta'])
    desc = descriptor(cell['family'], cell['demand_coverage'][0], 'high')
    result = resolve(req, complete=lambda *a, **k: {'message': {'content': json.dumps(desc)}, 'finish_reason': 'stop'}, **args)
    assert result['config']['max_tokens'] == 65536
    assert result['config']['routing_output_required'] is True
    assert 'routing_min_output_cap' not in result['config']
    assert result['trace']['calibration_contract']['output_allowance']['transfer_requires_validation']
    validate_output(result['config'], {'max_tokens': 65536})
    with pytest.raises(ValueError):
        validate_output(result['config'], {})


@pytest.mark.parametrize('body', [{'max_tokens': None}, {'max_tokens': True}, {'max_tokens': 0},
    {'max_tokens': 70000}, {'max_tokens': 1, 'max_completion_tokens': 1}])
def test_optional_does_not_mean_invalid_wire_limits_allowed(body):
    with pytest.raises(ValueError):
        validate_output({'max_tokens': 65536, 'routing_output_mode': 'provider_default'}, body)


def test_request_cannot_select_measured_policy():
    result = resolve({**request(), 'output_policy': 'measured', 'router': 'untrusted'},
                     complete=lambda *a, **k: None, **live_args())
    assert result['config']['routing_output_mode'] == 'provider_default'
    assert GenerationRouter(ClassifierTransport()).revision != GenerationRouter(ClassifierTransport(), output_policy='measured').revision
