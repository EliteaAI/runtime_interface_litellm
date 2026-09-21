"""Live project-over-shared inventory, separate from measured qualification.

Only Configurations supplies deployment facts. The policy describes measured
model/effort contracts; discovering a model never invents its qualification.
"""
import copy
import hashlib
import json


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def effective_models(items, project_id):
    """Project definitions shadow shared definitions before admission checks."""
    if not isinstance(items, list) or len(items) > 4096:
        raise ValueError('Invalid routing inventory')
    shared, private = {}, {}
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get('name'), str) or not item['name']:
            raise ValueError('Invalid routing model identity')
        owner = item.get('project_id')
        if type(owner) is not int or owner <= 0:
            raise ValueError('Invalid routing model owner')
        if owner == project_id:
            target = private
        elif item.get('shared') is True:
            target = shared
        else:
            continue
        name = item['name']
        if name in target and target[name] != item:
            raise ValueError('Ambiguous routing model identity')
        target[name] = copy.deepcopy(item)
    return dict(sorted({**shared, **private}.items()))


def model_binding(model):
    # Production snapshots have a configuration fingerprint. The fallback keeps
    # pure policy tests independent of a database and never includes credentials.
    fields = {key: model.get(key) for key in (
        'name', 'project_id', 'configuration_id', 'configuration_uuid',
        'context_window', 'max_output_tokens', 'supports_reasoning',
        'supports_vision', 'openai_compatible', 'available', 'exclusion_reason')}
    revision = model.get('configuration_fingerprint') or fingerprint(fields)
    return {'name': model['name'], 'project_id': model['project_id'], 'fingerprint': revision}


def validate_binding(binding, models, project_id):
    effective = effective_models(models, project_id)
    current = effective.get(binding.get('name')) if isinstance(binding, dict) else None
    if current is None or current.get('available') is False or model_binding(current) != binding:
        raise ValueError('Pinned model configuration changed or is no longer available')
    return current


def qualified_inventory(models, catalog):
    """Join live inventory to evidence; unmeasured models stay visible/excluded."""
    cards = {}
    for variant, contract in catalog['variants'].items():
        cards.setdefault(contract['model'], []).append(variant)
    available, exclusions = {}, []
    for name, model in models.items():
        reason = model.get('exclusion_reason') if model.get('available') is False else None
        if model.get('available') is False:
            reason = reason or 'CONFIGURATION_UNAVAILABLE'
        elif name not in cards:
            reason = 'NO_CALIBRATED_MODEL_CONTRACT'
        if reason:
            exclusions.append({'model': name, 'model_project_id': model['project_id'], 'reason': reason})
        else:
            available.update({variant: model for variant in cards[name]})
    return available, exclusions


def map_bound_model(binding, *, project_id, public_project_id, lookup):
    """Auto dispatch never silently falls back to a different owner or raw name."""
    owner = binding['project_id']
    if owner not in {project_id, public_project_id}:
        raise ValueError('Auto model owner is not in the execution inventory')
    mapped = f"{owner}_{binding['name']}"
    if not lookup('model_group_info', mapped):
        raise ValueError('Pinned model deployment is unavailable')
    return mapped, owner != project_id
