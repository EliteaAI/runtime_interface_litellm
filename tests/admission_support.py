"""Shared builders for the admission-table tests (#6722): inventories, prices and classifier replies."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from routing.service import PROFILE
from test_auto_routing_service import identity

# key -> (exact deployment name, canonical identity, (input, output) USD per token). Only the ordering of
# the prices matters: Luna < Terra < Sol < Sonnet < Opus, as in the platform catalog.
MODELS = {
    'luna': ('global.openai.gpt-5.6-luna', 'openai/gpt-5-6-luna', ('.00000025', '.000002')),
    'terra': ('global.openai.gpt-5.6-terra', 'openai/gpt-5-6-terra', ('.000001', '.000008')),
    'sol': ('global.openai.gpt-5.6-sol', 'openai/gpt-5-6-sol', ('.000002', '.000016')),
    'sonnet55': ('global.anthropic.claude-sonnet-5-5', 'anthropic/claude-sonnet-5-5', ('.000003', '.000015')),
    'opus55': ('global.anthropic.claude-opus-5-5', 'anthropic/claude-opus-5-5', ('.000005', '.000025')),
    'gpt54': ('gpt-5.4', 'openai/gpt-5-4', ('.0000025', '.00002')),
    'haiku': ('eu.anthropic.claude-haiku-4-5-20251001-v1:0', 'anthropic/claude-haiku-4-5', ('.000001', '.000005')),
}
CLASSIFIER = 'classifier-only'


def entry(name, prices):
    return dict(model_name=name, input_cost_per_token=prices[0], output_cost_per_token=prices[1],
                cache_read_input_token_cost='.0000001', cache_creation_input_token_cost='.00000125')


def args_for(*keys, prices=None, models=None, **per_model):
    """resolve() keyword arguments for an inventory of `keys` plus a classifier-only deployment.

    The classifier has no calibrated identity, so it never competes for generation. `per_model`
    maps a key to model fields (context_window, max_output_tokens, openai_compatible, ...).
    """
    prices = prices or {}
    rows = [{'name': CLASSIFIER, 'project_id': 7, 'context_window': 128000, 'max_output_tokens': 4096,
             'identity': identity(None)}]
    entries = [entry(CLASSIFIER, ('.0000001', '.0000004'))]
    for key in keys:
        name, canonical, default_prices = MODELS[key]
        rows.append({'name': name, 'project_id': 7, 'context_window': 1_000_000, 'max_output_tokens': 64000,
                     'identity': identity(canonical), **per_model.get(key, {})})
        entries.append(entry(name, prices.get(key, default_prices)))
    return dict(project_id=7, user_id=2, signing_key='unit-test-only', now=100,
                settings={'enabled': True, 'revision': 'g1',
                          'classifier': {'name': CLASSIFIER, 'project_id': 7, 'source': 'platform'}},
                models=models if models is not None else rows,
                price_snapshot={'revision': 'p1', 'entries': entries})


def request(prompt='Analyze the supplied figures.', **changes):
    value = {'selection': {'mode': 'auto', 'profile_ref': PROFILE, 'reasoning': {'mode': 'auto'}},
             'surface': 'chat', 'scope_id': '1' * 64, 'invocation_id': 'a' * 64,
             'messages': [{'role': 'user', 'content': prompt}]}
    value.update(changes)
    return value


def descriptor(demand='standard', **changes):
    value = {'operation': 'analysis', 'demand': demand, 'relation': 'independent', 'reference_ids': [],
             'needs_context': False, 'reason': 'Analyze supplied material', 'effort_need': 'low',
             'task_family': 'quantitative', 'input_status': 'provided', 'retrieval_source_ids': []}
    value.update(changes)
    return value


def classifier(**changes):
    """A `complete` callable returning one classifier reply."""
    body = descriptor(**changes) if 'raw' not in changes else changes['raw']
    text = body if isinstance(body, str) else json.dumps(body)
    return lambda *a, **k: {'message': {'content': text}, 'finish_reason': 'stop'}
