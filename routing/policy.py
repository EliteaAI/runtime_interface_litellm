"""Auto routing policy values (#6722 V5 PLAN section 3.2), validated strictly.

Configurations serves the effective policy as `settings['policy']`. Older
Configurations omit the key; then every value equals the literal the Gateway
used before the policy existed, and the V5 path does not run at all.
"""
import copy
import hashlib
import json
import re

DEFAULT_POLICY = {
    'version': 1,
    'shadow_mode': True,
    'selector': {'artifact': None, 'tau': 0.85, 'abstain': True},
    'pool': None,
    'fallback': None,
    'output': {'default_allowance': 8000, 'min_cap': 256, 'max_cap': 32000},
    'thinking_budget_tokens': {'low': 2048, 'medium': 4096, 'high': 9092},
    'greeting_rule': True,
    'classifier': {'enabled': False},
}
# Platform bounds no configuration can exceed (contract, not policy).
PLATFORM_MIN_CAP = 256
PLATFORM_MAX_CAP = 128000
MAX_THINKING_BUDGET = 128000
MAX_POOL_ENTRIES = 256
ARTIFACT_ID = re.compile(r'^[a-z0-9][a-z0-9.-]{0,127}$')
NESTED = {'selector': {'artifact', 'tau', 'abstain'}, 'output': {'default_allowance', 'min_cap', 'max_cap'},
          'thinking_budget_tokens': {'low', 'medium', 'high'}, 'classifier': {'enabled'}}


class PolicyError(ValueError):
    pass


def _int(value, low, high, field):
    if type(value) is not int or not low <= value <= high:
        raise PolicyError(f'{field} must be an integer between {low} and {high}')
    return value


def _ref(value, field):
    if value is None:
        return None
    if (not isinstance(value, dict) or set(value) != {'name', 'project_id'}
            or not isinstance(value['name'], str) or not 1 <= len(value['name']) <= 512
            or type(value['project_id']) is not int or value['project_id'] <= 0):
        raise PolicyError(f'{field} must be {{name, project_id}} or null')
    return value


def validate(raw):
    """Return the complete effective policy. Missing keys take defaults (a partial
    project override is merged per key); unknown keys, bad types and values
    outside the platform bounds refuse. What a valid merge can still produce
    (an allowance outside a lowered cap range) is clamped and listed in
    `clamped` for the trace."""
    if not isinstance(raw, dict):
        raise PolicyError('policy must be an object')
    unknown = set(raw) - set(DEFAULT_POLICY)
    if unknown:
        raise PolicyError('Unknown policy keys: ' + ', '.join(sorted(map(str, unknown))))
    value = copy.deepcopy(DEFAULT_POLICY)
    for key, item in raw.items():
        if key in NESTED:
            if not isinstance(item, dict) or set(item) - NESTED[key]:
                raise PolicyError(f'{key} must be an object with keys {sorted(NESTED[key])}')
            value[key].update(copy.deepcopy(item))
        else:
            value[key] = copy.deepcopy(item)
    if value['version'] != 1 or type(value['version']) is not int:
        raise PolicyError('Unsupported policy version')
    for key in ('shadow_mode', 'greeting_rule'):
        if type(value[key]) is not bool:
            raise PolicyError(f'{key} must be boolean')
    if type(value['classifier']['enabled']) is not bool:
        raise PolicyError('classifier.enabled must be boolean')
    artifact, tau = value['selector']['artifact'], value['selector']['tau']
    if artifact is not None and (not isinstance(artifact, str) or not ARTIFACT_ID.fullmatch(artifact)):
        raise PolicyError('selector.artifact must be an installed artifact id or null')
    if type(tau) not in (int, float) or not 0.5 <= tau <= 0.99:
        raise PolicyError('selector.tau must be between 0.5 and 0.99')
    value['selector']['tau'] = float(tau)
    if type(value['selector']['abstain']) is not bool:
        raise PolicyError('selector.abstain must be boolean')
    pool = value['pool']
    if pool is not None:
        if (not isinstance(pool, list) or not 1 <= len(pool) <= MAX_POOL_ENTRIES
                or any(not isinstance(x, str) or not 1 <= len(x) <= 512 for x in pool)):
            raise PolicyError(f'pool must be null or 1..{MAX_POOL_ENTRIES} model names or canonical ids')
    _ref(value['fallback'], 'fallback')
    out = value['output']
    _int(out['min_cap'], PLATFORM_MIN_CAP, PLATFORM_MAX_CAP, 'output.min_cap')
    _int(out['max_cap'], out['min_cap'], PLATFORM_MAX_CAP, 'output.max_cap')
    _int(out['default_allowance'], PLATFORM_MIN_CAP, PLATFORM_MAX_CAP, 'output.default_allowance')
    value['clamped'] = []
    if not out['min_cap'] <= out['default_allowance'] <= out['max_cap']:
        # e.g. platform allowance 8000 with a project max_cap of 4000.
        out['default_allowance'] = min(max(out['default_allowance'], out['min_cap']), out['max_cap'])
        value['clamped'].append('output.default_allowance')
    budgets = value['thinking_budget_tokens']
    for effort in ('low', 'medium', 'high'):
        _int(budgets[effort], 0, MAX_THINKING_BUDGET, f'thinking_budget_tokens.{effort}')
    if not budgets['low'] < budgets['medium'] < budgets['high']:
        # The relay maps a budget back to its effort; it must be unambiguous.
        raise PolicyError('thinking_budget_tokens must increase from low to high')
    return value


def effective_policy(settings):
    """Validated policy, or today's literals when Configurations sends none.

    Raises `RoutingUnavailable(..., 'POLICY_INVALID')` for an invalid policy."""
    raw = settings.get('policy') if isinstance(settings, dict) else None
    if raw is None:
        return copy.deepcopy(DEFAULT_POLICY)
    try:
        return validate(raw)
    except PolicyError as exc:
        from .service import RoutingUnavailable
        raise RoutingUnavailable('Invalid Auto routing policy', 'POLICY_INVALID') from exc


def policy_revision(policy, selector_id=None):
    """sha256 of the effective policy plus the selector identity it binds."""
    body = json.dumps({'policy': policy, 'selector': selector_id}, sort_keys=True,
                      separators=(',', ':'), allow_nan=False)
    return hashlib.sha256(body.encode()).hexdigest()


def runtime_policy(settings):
    """(policy, mode, error) for resolve and relay alike.

    mode: None when Configurations sent no policy (legacy only, identical to
    main); 'shadow' or 'active' otherwise. An invalid policy never refuses
    Auto: it runs today's literals in legacy mode and reports POLICY_INVALID.
    Activation needs an installed, trained artifact; otherwise the (valid)
    policy runs in shadow and reports ACTIVATION_WITHOUT_ARTIFACT.
    """
    if not isinstance(settings, dict) or settings.get('policy') is None:
        return copy.deepcopy(DEFAULT_POLICY), None, None
    try:
        policy = effective_policy(settings)
    except ValueError as exc:
        return copy.deepcopy(DEFAULT_POLICY), 'shadow', getattr(exc, 'reason', None) or 'POLICY_INVALID'
    if policy['shadow_mode']:
        return policy, 'shadow', None
    from .selector import installed
    try:
        trained = not installed(policy['selector']['artifact']).stub
    except ValueError:
        trained = False
    return (policy, 'active', None) if trained else (policy, 'shadow', 'ACTIVATION_WITHOUT_ARTIFACT')
