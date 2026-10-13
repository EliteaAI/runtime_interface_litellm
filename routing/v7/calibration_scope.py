"""Trusted execution envelopes and prospective forecast certificates."""
import math
import re
from .retrieval import digest


def validate_scope(scope):
    keys = {'id', 'delivery', 'execution_contract', 'tools_sha256', 'history'}
    bounded = scope.get('history') == 'bounded_conversation'
    if bounded:
        keys.add('maximum_user_turns')
    if (set(scope) != keys
            or scope['delivery'] not in {'text', 'repository', 'standalone_code'}
            or not isinstance(scope['execution_contract'], str) or not scope['execution_contract']
            or scope['id'] != scope['delivery'] + ':' + scope['execution_contract']
            or scope['history'] not in {'isolated', 'bounded_conversation'}
            or not isinstance(scope['tools_sha256'], str)
            or not re.fullmatch('[0-9a-f]{64}', scope['tools_sha256'])):
        raise ValueError('Invalid calibrated request scope')
    if bounded and (scope['delivery'] != 'text' or scope['tools_sha256'] != digest([])
                    or type(scope['maximum_user_turns']) is not int
                    or not 1 <= scope['maximum_user_turns'] <= 64):
        raise ValueError('Invalid bounded conversation scope')


def check_request_scope(scope, task_contract, messages, tools):
    # task_contract belongs to the server/experiment owner. Neither a model
    # descriptor nor caller text can replace this runtime contract.
    validate_scope(scope)
    if task_contract != scope['id']:
        raise ValueError('Unmeasured execution envelope: configured task contract differs')
    if digest(tools or []) != scope['tools_sha256']:
        raise ValueError('Unmeasured execution envelope: native tool schema differs')
    turns = sum(m.get('role') == 'user' for m in messages)
    if scope['history'] == 'bounded_conversation':
        if not 1 <= turns <= scope['maximum_user_turns']:
            raise ValueError('Unmeasured execution envelope: conversation turn bound exceeded')
        if any(m.get('role') in {'tool', 'function'} for m in messages):
            raise ValueError('Unmeasured execution envelope: prior tool history is not calibrated')
    elif (turns != 1 or any(m.get('role') in {'assistant', 'tool', 'function'} for m in messages)):
        raise ValueError('Unmeasured execution envelope: isolated calibration does not cover conversation history')


def valid_forecast(cohort, request_contract_sha256):
    certificate = cohort.get('validation_certificate') or {}
    core = {k:v for k,v in cohort.items() if k != 'validation_certificate'}
    trajectories = cohort.get('trajectories', [])
    groups = {r['template_group'] for r in trajectories}
    metrics = [certificate.get('wape'), certificate.get('p90_symmetric_cost_factor')]
    return bool(
        certificate.get('method') == 'prospective-frozen-cohort-v1'
        and certificate.get('cohort_sha256') == digest(core)
        and isinstance(certificate.get('validation_source_sha256'), str)
        and re.fullmatch('[0-9a-f]{64}', certificate['validation_source_sha256'])
        and certificate.get('connected_training_groups_excluded') is True
        and certificate.get('gates_tuned_from_this_run') is False
        and len(groups) >= 8 and cohort.get('independent_templates') == len(groups)
        and type(certificate.get('validation_groups')) is int and certificate['validation_groups'] >= 8
        and all(type(v) in (int, float) and math.isfinite(v) and v >= 0 for v in metrics)
        and metrics[0] <= .5 and metrics[1] <= 3
        and isinstance(request_contract_sha256, str) and request_contract_sha256
        and all(r.get('request_contract_sha256') == request_contract_sha256 for r in trajectories))
