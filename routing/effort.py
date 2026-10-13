"""Exact measured reasoning envelope; no provider/model-name inference."""
import copy

FIELDS = {'reasoning_effort', 'reasoning', 'thinking', 'output_config'}


def validate_fields(config, body):
    expected = config['routing_reasoning_fields']
    effort, transport = config['reasoning_effort'], config['routing_transport']
    if effort is None:
        allowed = {}
    elif effort in {'low', 'medium', 'high'} and transport == 'chat_completions':
        allowed = {'reasoning': {'effort': effort}}
    elif effort in {'low', 'medium', 'high'} and transport == 'anthropic_messages':
        allowed = {'thinking': {'type': 'adaptive', 'display': 'summarized'},
                   'output_config': {'effort': effort}}
    else:
        raise ValueError('Unsupported measured reasoning contract')
    if expected != allowed or {k: copy.deepcopy(v) for k, v in body.items() if k in FIELDS} != expected:
        raise ValueError('Pinned measured reasoning envelope changed')



def check_output_limit(config, body):
    """Pinned output envelope of a relayed call; raises ValueError when it is not honoured.

    A pin with an explicit cap needs a body limit no larger than that cap (and, for a measured
    contract, not below its minimum). A pin with ``max_tokens: None`` is the provider default:
    the body may carry no limit at all, or any limit up to the model's ``max_output_tokens``.
    ``routing_min_output_cap`` applies only when a limit is present.
    """
    limit = body.get('max_completion_tokens', body.get('max_tokens', body.get('max_output_tokens')))
    pinned = config['max_tokens']
    if pinned is None:
        if limit is not None and (type(limit) is not int or limit > config['max_output_tokens']):
            raise ValueError('Pinned output allowance exceeded')
    elif type(limit) is not int or limit > pinned:
        raise ValueError('Pinned output allowance exceeded')
    if limit is not None and limit < config.get('routing_min_output_cap', 0):
        raise ValueError('Pinned measured output allowance reduced')
