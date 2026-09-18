"""Complete-input mechanical grammars, never a general factual shortcut."""
import re

REVISION = 'bounded-mechanical-v1'
CASE = re.compile(r'(?:convert|change) "([A-Za-z0-9 ,.!?;:\-]{1,160})" to (uppercase|lowercase)[.!]?', re.I)
SORT = re.compile(r'sort (?:these )?integers (ascending|descending):\s*(-?\d{1,6}(?:\s*,\s*-?\d{1,6}){1,31})[.!]?', re.I)


def match_mechanical(text):
    if not isinstance(text, str) or len(text) > 300:
        return None
    if CASE.fullmatch(text.strip()):
        return {'operation': 'transform', 'task_family': 'transformation', 'rule': 'literal_ascii_case'}
    if SORT.fullmatch(text.strip()):
        return {'operation': 'transform', 'task_family': 'quantitative', 'rule': 'bounded_integer_sort'}
    return None
