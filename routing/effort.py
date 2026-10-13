"""Exact measured reasoning envelope; no provider/model-name inference."""
import copy

FIELDS = {'reasoning_effort', 'reasoning', 'thinking', 'output_config'}


def validate_fields(config, body):
    expected = config['routing_reasoning_fields']
    effort, transport = config['reasoning_effort'], config['routing_transport']
    representation = config.get('routing_reasoning_format', 'nested')
    if not isinstance(representation, str) or representation not in {'nested', 'top_level'} or (representation == 'top_level' and transport != 'chat_completions'):
        raise ValueError('Unsupported measured reasoning contract format')
    if effort is None:
        allowed = {}
    elif effort in {'low', 'medium', 'high'} and transport == 'chat_completions':
        allowed = {'reasoning_effort': effort} if representation == 'top_level' else {'reasoning': {'effort': effort}}
    elif effort in {'low', 'medium', 'high'} and transport == 'anthropic_messages':
        allowed = {'thinking': {'type': 'adaptive', 'display': 'summarized'},
                   'output_config': {'effort': effort}}
    else:
        raise ValueError('Unsupported measured reasoning contract')
    if expected != allowed or {k: copy.deepcopy(v) for k, v in body.items() if k in FIELDS} != expected:
        raise ValueError('Pinned measured reasoning envelope changed')
