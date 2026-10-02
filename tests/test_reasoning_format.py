import copy
import pytest
from routing.effort import validate_fields
from routing.v7.catalog import compile_catalog, effort_candidate


@pytest.mark.parametrize('effort',[None,'low','medium','high'])
def test_top_level_contract_rejects_nested_substitution(effort):
    fields={} if effort is None else {'reasoning_effort':effort}
    config={'reasoning_effort':effort,'routing_transport':'chat_completions',
            'routing_reasoning_format':'top_level','routing_reasoning_fields':fields}
    validate_fields(config,fields)
    with pytest.raises(ValueError):
        validate_fields(config,{'reasoning':{'effort':effort}})
    if effort is not None:
        with pytest.raises(ValueError):validate_fields({k:v for k,v in config.items() if k!='routing_reasoning_format'},fields)


@pytest.mark.parametrize('representation',['unknown',None,[],{}])
def test_unknown_representation_rejected(representation):
    with pytest.raises((ValueError,TypeError)):
        validate_fields({'reasoning_effort':None,'routing_transport':'chat_completions',
                         'routing_reasoning_format':representation,'routing_reasoning_fields':{}},{})


def test_format_is_explicit_evidence_metadata_not_model_name_inference():
    candidate=copy.deepcopy(effort_candidate())
    vid=next(k for k,v in candidate['variants'].items() if v['transport']=='chat_completions' and v['effort']=='medium')
    candidate['variants'][vid]['reasoning_format']='top_level'
    candidate['variants'][vid]['requested_reasoning_fields']={'reasoning_effort':'medium'}
    candidate['variants']={'top-level-test':candidate['variants'][vid]}
    candidate['records']=[{**row,'variant':'top-level-test'} for row in candidate['records'] if row['variant']==vid]
    catalog=compile_catalog(candidate=candidate)
    observed=catalog['variants']['top-level-test']['calibration_contract']
    assert observed['reasoning_format']=='top_level'
    assert observed['reasoning_fields']=={'reasoning_effort':'medium'}


def test_anthropic_rejects_top_level_format_even_without_effort():
    with pytest.raises(ValueError):
        validate_fields({'reasoning_effort':None,'routing_transport':'anthropic_messages',
                         'routing_reasoning_format':'top_level','routing_reasoning_fields':{}},{})


def test_service_keeps_explicit_format_in_signed_binding():
    import json
    from routing.service import GenerationRouter,ClassifierTransport,resolve,decode_pin
    from test_v12_efforts import inputs,request,descriptor,DATA
    preset='terra-medium'
    cell=next(r for r in DATA['records'] if r['variant']==preset and r['eligible_local_beta'])
    catalog=compile_catalog()
    measured=catalog['variants'][preset]['calibration_contract']
    measured.update(reasoning_format='top_level',reasoning_fields={'reasoning_effort':'medium'})
    router=GenerationRouter(ClassifierTransport(),catalog=catalog,output_policy='measured')
    req=request('Analyze supplied evidence');req['selection']['reasoning']={'mode':'explicit','preset':'medium'}
    value=descriptor(cell['family'],cell['demand_coverage'][0],'medium')
    args=inputs(preset);args['router']=router
    result=resolve(req,complete=lambda *a,**k:{'message':{'content':json.dumps(value)},'finish_reason':'stop'},**args)
    assert result['config']['routing_reasoning_format']=='top_level'
    assert result['config']['routing_reasoning_fields']=={'reasoning_effort':'medium'}
    validate_fields(result['config'],{'reasoning_effort':'medium'})
    pin=decode_pin(result['pin'],args['signing_key'],project_id=7,user_id=2,settings=args['settings'],now=101)
    assert pin['config']['routing_reasoning_format']=='top_level'
