"""#6722: the trace names which gate removed a variant and which reason the chooser rejected it with."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from admission_support import args_for, classifier, request
from routing.service import decision_log_line, resolve
from routing.v7.catalog import compile_catalog

LOCAL = {'sol': {'context_window': 200000, 'max_output_tokens': 32000, 'supports_reasoning': True},
         'terra': {'context_window': 200000, 'max_output_tokens': 32000, 'supports_reasoning': True},
         'luna': {'context_window': 272000, 'max_output_tokens': 64000}}
SOL_TERRA = ['sol-default', 'sol-high', 'sol-low', 'sol-medium', 'terra-default', 'terra-high', 'terra-low', 'terra-medium']


def local(generation_bytes, demand='deep', **kwargs):
    """The local stack: two priced 200k-context deployments, Luna at 272k, a baseline, no cap."""
    args = args_for('luna', 'terra', 'sol', 'gpt54', **{**LOCAL, **kwargs.pop('inventory', {})})
    req = request('Design the worker architecture.', generation_input_bytes=generation_bytes)
    return resolve(req, complete=classifier(demand=demand, **kwargs), **args)


# --- which gate fires in the local scenario ---------------------------------------------------------------------
def test_measured_contract_cap_equals_the_deployment_maximum_so_the_effective_cap_path_matches_the_old_one():
    variants = compile_catalog()['variants']
    for vid in ('sol-high', 'terra-default'):
        assert variants[vid]['calibration_contract']['output_allowance'] == LOCAL['sol']['max_output_tokens']


def test_at_100k_bytes_sol_and_terra_pass_every_gate():
    result = local(100_000, operation='design')
    inventory = result['trace']['inventory']
    assert inventory['gate_failures'] == {} or not set(SOL_TERRA) & set(inventory['gate_failures'])
    assert result['trace']['selection']['variant'] in {'terra-default', 'sol-high'}


def test_at_170k_bytes_the_context_window_gate_removes_sol_and_terra_but_not_luna():
    result = local(170_000, operation='design', effort_need='high', relation='return', needs_context=True,
                   input_status='ambiguous')
    inventory = result['trace']['inventory']
    assert inventory['input_bytes'] == 170_000
    for vid in SOL_TERRA:
        assert inventory['gate_failures'][vid] == ['CONTEXT_WINDOW'], vid
    assert not any(v.startswith('luna') for v in inventory['gate_failures'])  # 272k holds 170k + 64k
    selection = result['trace']['selection']
    # The symptom seen live: the baseline, with an admission status that alone does not say why.
    assert (selection['variant'], selection['reason']) == ('gpt54-high', 'UNCERTAIN_TASK_BASELINE')
    assert selection['admission']['status'] == 'no_variant_clears_floor'
    # The chooser only sees what the gates admitted; the gate failure above is the real reason.
    assert selection['admission']['rejections']['sol-high'] == ['outside configured PoC pool']


@pytest.mark.parametrize('size,passes', [(168_000, True), (168_001, False)])
def test_context_window_boundary_is_size_plus_the_32000_deployment_maximum(size, passes):
    inventory = local(size, operation='design')['trace']['inventory']
    assert ('terra-default' not in inventory['gate_failures']) is passes


# --- gate_failures: every qualified variant that was not admitted ---------------------------------------------------
def test_price_and_cache_write_price_failures_are_named():
    args = args_for('luna', 'terra', 'sol', 'gpt54')
    entries = args['price_snapshot']['entries']
    args['price_snapshot']['entries'] = [e for e in entries if e['model_name'] != 'global.openai.gpt-5.6-sol']
    for e in args['price_snapshot']['entries']:
        if e['model_name'] == 'global.openai.gpt-5.6-terra':
            del e['cache_creation_input_token_cost']
    inventory = resolve(request(), complete=classifier(demand='standard'), **args)['trace']['inventory']
    assert inventory['gate_failures']['sol-high'] == ['PRICE']
    assert inventory['gate_failures']['terra-default'] == ['CACHE_WRITE_PRICE']
    assert inventory['admitted_variants'] + len(inventory['gate_failures']) == inventory['qualified_variants']


def test_output_limit_reasoning_disabled_and_thinking_budget_are_named():
    args = args_for('luna', 'terra', 'opus55')
    req = request(); req['output_cap'] = 1000
    args['models'][2]['max_output_tokens'] = 500  # terra: below the explicit cap
    inventory = resolve(req, complete=classifier(demand='simple'), **args)['trace']['inventory']
    assert inventory['gate_failures']['terra-default'] == ['OUTPUT_LIMIT']
    assert inventory['gate_failures']['opus-5-5-high'] == ['THINKING_BUDGET']  # 1000 <= the 9092 thinking budget
    args = args_for('luna', 'terra', terra={'supports_reasoning': False})
    inventory = resolve(request(), complete=classifier(demand='standard'), **args)['trace']['inventory']
    assert inventory['gate_failures']['terra-high'] == ['CONFIGURATION_REASONING_DISABLED']


def test_only_names_and_numbers_appear_in_gate_failures():
    inventory = local(170_000)['trace']['inventory']
    assert all(isinstance(v, list) and all(isinstance(c, str) and c.isupper() for c in v)
               for v in inventory['gate_failures'].values())


# --- selection.admission.rejections -----------------------------------------------------------------------------------
def test_chooser_rejections_name_the_reason_per_candidate():
    result = local(100_000, operation='design', effort_need='high')
    rejections = result['trace']['selection']['admission']['rejections']
    assert rejections['luna-default'] == ['ADMISSION_ROW_OUT']
    assert rejections['gpt54-low'][0] == 'EFFORT_NEED_FILTER'  # then also ADMISSION_NO_ROW: it has no row
    assert rejections['mini-low'][0] == 'outside configured PoC pool'  # no deployment, see gate_failures/exclusions
    assert 'terra-default' not in rejections or rejections['terra-default'] != ['ADMISSION_ROW_OUT']
    codes = {c for reasons in rejections.values() for c in reasons}
    assert codes <= {'ADMISSION_ROW_OUT', 'ADMISSION_NO_ROW', 'EFFORT_NEED_FILTER', 'outside configured PoC pool',
                     'CALIBRATION_ALLOWANCE_BELOW_P90'}


def test_no_row_and_allowance_and_pool_rejections():
    args = args_for('luna', 'terra', 'gpt54', terra={'max_output_tokens': 300})
    req = request(); req['output_cap'] = 300
    result = resolve(req, complete=classifier(demand='standard'), **args)
    rejections = result['trace']['selection']['admission']['rejections']
    assert rejections['luna-default'] == ['CALIBRATION_ALLOWANCE_BELOW_P90']
    assert rejections['gpt54-medium'] == ['ADMISSION_NO_ROW']
    assert rejections['sol-high'] == ['outside configured PoC pool']  # no Sol deployment at all


def test_rejections_survive_the_economics_rerank():
    result = local(100_000, demand='standard')
    admission = result['trace']['selection']['admission']
    assert admission['rejections'], 'ranked selections keep the chooser rejections'


# --- decision log line -------------------------------------------------------------------------------------------------
SENTINEL = 'SENTINEL-do-not-log-7731 zero trust worker'


def test_log_line_has_the_documented_fields_and_no_request_text():
    args = args_for('luna', 'terra', 'sol', 'gpt54', **LOCAL)
    req = request(SENTINEL, generation_input_bytes=170_000)
    result = resolve(req, complete=classifier(demand='deep', operation='design', effort_need='high', relation='return',
                                              needs_context=True, input_status='ambiguous',
                                              reason=SENTINEL), **args)
    line = decision_log_line(7, req, result)
    assert 'SENTINEL' not in line and 'zero' not in line and 'worker' not in line
    assert '\n' not in line
    for field in ('project=7', 'scope=11111111', 'classifier=classifier-only', 'schema_valid=True', 'error=-',
                  'profile_error=-', 'descriptor=design/deep/return/quantitative/high', 'needs_context=True',
                  'input_status=ambiguous', 'selection=gpt54-high/UNCERTAIN_TASK_BASELINE/-',
                  'model=gpt-5.4', 'cap=default', 'gate_failures=', 'sol-high:CONTEXT_WINDOW',
                  'terra-default:CONTEXT_WINDOW', 'rejections='):
        assert field in line, field
    assert line.startswith('auto-routing decision ')
    assert f"admitted={result['trace']['inventory']['admitted_variants']}/{result['trace']['inventory']['qualified_variants']}" in line


def test_log_line_for_a_tiered_selection_and_an_explicit_cap():
    args = args_for('luna', 'terra')
    req = request(SENTINEL); req['output_cap'] = 9000
    result = resolve(req, complete=classifier(demand='deep'), **args)
    line = decision_log_line(7, req, result)
    assert 'selection=terra-default/V6_PRICE_AFTER_ELIGIBILITY/provisional' in line and 'cap=9000' in line
    assert SENTINEL.split()[0] not in line


def test_classifier_error_codes_appear_but_the_reply_text_does_not():
    args = args_for('luna', 'terra', 'gpt54')
    req = request(SENTINEL)
    result = resolve(req, complete=classifier(raw='{"operation": "' + SENTINEL + '"}'), **args)
    line = decision_log_line(7, req, result)
    assert 'schema_valid=False' in line and 'SENTINEL' not in line and 'zero' not in line


def test_refusal_line_keeps_the_prefix_and_names_the_reason():
    req = request(SENTINEL)
    line = decision_log_line(7, req, refused='NO_QUALIFIED_MODELS')
    assert line == 'auto-routing decision project=7 scope=11111111 refused=NO_QUALIFIED_MODELS'
    assert 'SENTINEL' not in decision_log_line(7, req, refused='ValueError: ' + 'x y\nz')
    assert '\n' not in decision_log_line(7, req, refused='a\nb')
