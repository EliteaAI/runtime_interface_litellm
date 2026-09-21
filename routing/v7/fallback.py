"""Post-V6 diagnostic candidate: preserve baseline family across effort filtering.

Not part of the frozen main campaign and not promoted. Production should publish
explicit fallback variant order; this bounded candidate uses the catalog's
configured baseline model identity and the already-filtered eligible set.
"""
import copy
from .coordinator import Router as FrozenRouter


def resolve_baseline_variant(selection, catalog):
    if selection['reason'] != 'UNCERTAIN_TASK_BASELINE':
        return selection
    baseline = catalog['variants'][catalog['baseline']]
    eligible = {row['variant'] for row in selection['candidates'] if row['eligible']}
    family = [vid for vid in eligible
              if catalog['variants'][vid]['model'] == baseline['model']]
    if not family:
        return selection
    effort_order = {None: 0, 'low': 1, 'medium': 2, 'high': 3}
    chosen = (catalog['baseline'] if catalog['baseline'] in family else
              min(family, key=lambda vid: (effort_order[catalog['variants'][vid]['effort']], vid)))
    if chosen == selection['variant']:
        return selection
    result = {**copy.deepcopy(selection), **copy.deepcopy(catalog['variants'][chosen]),
              'variant': chosen}
    # Keep the uncertainty reason so the frozen economic layer cannot replace
    # the baseline with a cheaper unqualified interpretation of the task.
    result['baseline_resolution'] = {
        'reason': 'ELIGIBLE_BASELINE_FAMILY_EFFORT_VARIANT',
        'configured_baseline': catalog['baseline'],
        'previous_fallback': selection['variant'], 'selected': chosen}
    return result


class Router(FrozenRouter):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.revision += '-baseline-family-diagnostic-1'

    def select(self, *args, **kwargs):
        return resolve_baseline_variant(super().select(*args, **kwargs), self.catalog)
