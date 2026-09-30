"""Trusted deployment fallback contracts, separate from measured quality cells."""
import copy
import re

from .effort import validate_fields

VARIANT = 'deployment-configured-fallback'


def validate(value):
    keys = {'model_binding', 'effort', 'transport', 'reasoning_fields',
            'reasoning_format', 'output_allowance', 'cache_write_mode'}
    if not isinstance(value, dict) or set(value) != keys:
        raise ValueError('Invalid configured fallback contract')
    binding = value['model_binding']
    if (not isinstance(binding, dict) or set(binding) != {'name', 'project_id', 'fingerprint'}
            or not isinstance(binding['name'], str) or not 1 <= len(binding['name']) <= 256
            or type(binding['project_id']) is not int or binding['project_id'] <= 0
            or not isinstance(binding['fingerprint'], str)
            or not re.fullmatch('[0-9a-f]{64}', binding['fingerprint'])
            or not isinstance(value['transport'], str)
            or value['transport'] not in {'chat_completions', 'anthropic_messages'}
            or (value['effort'] is not None and not isinstance(value['effort'], str))
            or type(value['output_allowance']) is not int
            or not 1 <= value['output_allowance'] <= 1_000_000
            or not isinstance(value['cache_write_mode'], str)
            or value['cache_write_mode'] not in {'ordinary_input', 'separate'}):
        raise ValueError('Invalid configured fallback binding or native limits')
    validate_fields({'routing_reasoning_fields': value['reasoning_fields'],
                     'reasoning_effort': value['effort'],
                     'routing_transport': value['transport'],
                     'routing_reasoning_format': value['reasoning_format']},
                    value['reasoning_fields'])
    return copy.deepcopy(value)


def install(catalog, value):
    value = validate(value)
    catalog = copy.deepcopy(catalog)
    if VARIANT in catalog['variants']:
        raise ValueError('Configured fallback identity collides with measured catalog')
    # These execution permissions belong to the deployment owner. They are not
    # capability ceilings, evidence, or candidates for economic optimization.
    catalog['variants'][VARIANT] = {
        'model': value['model_binding']['name'], 'effort': value['effort'],
        'enabled': True, 'tasks': ['greeting', 'creative', 'transform', 'analysis', 'design', 'other'],
        'max_demand': 'deep', 'cost_band': 2, 'cache_write_mode': value['cache_write_mode'],
        'configured_fallback_contract': value,
    }
    return catalog
