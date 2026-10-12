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


def _joins(model, variant):
    """Canonical identity joins a measured contract; legacy items join by exact name."""
    identity = model.get('identity')
    if not isinstance(identity, dict):
        return model['name'] == variant['model']
    contract = identity.get('contract', 'default')
    # D6: provider-default variants take default deployments only; effort
    # variants may use either. A web-search tool contract never joins Auto.
    return (identity.get('canonical') is not None and identity['canonical'] == variant.get('canonical_model')
            and identity.get('kind') == 'chat'
            and contract in ({'default'} if variant.get('effort') is None else {'default', 'reasoning'}))


def select_deployment(candidates, variant, *, project_id=None):
    """Deterministic winner among deployments of one canonical model (#6826 3.4):
    explicit identity, project-owned, preferred contract, shortest, lexicographic."""
    preferred = 'default' if variant.get('effort') is None else 'reasoning'
    def key(model):
        identity = model.get('identity') if isinstance(model.get('identity'), dict) else {}
        return (identity.get('source') != 'explicit', model['project_id'] != project_id,
                identity.get('contract', 'default') != preferred, len(model['name']), model['name'])
    return min(candidates, key=key)


def qualified_inventory(models, catalog, *, project_id=None):
    """Join live inventory to evidence; unmeasured models stay visible/excluded.

    One deployment is bound per measured variant; same-canonical losers are
    listed, never merged. Discovering a model never invents its qualification.
    """
    joined, candidates, exclusions = {}, {}, []
    for name, model in models.items():
        entry = {'model': name, 'model_project_id': model['project_id']}
        if model.get('available') is False:
            exclusions.append({**entry, 'reason': model.get('exclusion_reason') or 'CONFIGURATION_UNAVAILABLE'})
            continue
        joined[name] = [vid for vid, variant in catalog['variants'].items() if _joins(model, variant)]
        if not joined[name]:
            identity = model.get('identity')
            if isinstance(identity, dict) and identity.get('canonical') is None:
                entry['identity_source'] = 'unresolved'
            exclusions.append({**entry, 'reason': 'NO_CALIBRATED_MODEL_CONTRACT'})
        for vid in joined[name]:
            candidates.setdefault(vid, []).append(model)
    winners = {vid: select_deployment(group, catalog['variants'][vid], project_id=project_id)
               for vid, group in candidates.items()}
    # Retain inventory order, then catalog order, for every downstream tie-break.
    available = {vid: models[name] for name in joined for vid in joined[name] if winners[vid] is models[name]}
    for name, variants in joined.items():
        if variants and not any(winners[vid] is models[name] for vid in variants):
            exclusions.append({'model': name, 'model_project_id': models[name]['project_id'],
                               'reason': 'DUPLICATE_CANONICAL_DEPLOYMENT', 'selected': winners[variants[0]]['name']})
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
