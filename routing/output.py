"""Provider output defaults and explicit limits, independent of calibration budgets."""


def native_anthropic(model):
    return not model.get('openai_compatible', False) and any(
        name in model['name'].lower() for name in ('anthropic', 'claude'))


def validate_output(config, body):
    """Validate the signed allowance without forcing optional provider fields."""
    fields = [body[k] for k in ('max_tokens', 'max_completion_tokens', 'max_output_tokens') if k in body]
    optional = config.get('routing_output_mode') == 'provider_default' and not config.get('routing_output_required', False)
    if not fields:
        if optional:
            return
        raise ValueError('Pinned output allowance is required')
    if len(fields) != 1 or type(fields[0]) is not int or not 1 <= fields[0] <= config['max_tokens']:
        raise ValueError('Pinned output allowance exceeded or invalid')
    if fields[0] < config.get('routing_min_output_cap', 0):
        raise ValueError('Pinned measured output allowance reduced')
