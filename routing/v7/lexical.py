"""Experimental lexical context ranking. No embedding/model/server dependency.

Ranking is a retrieval hint, never authorization, a model-quality score, or proof
that one artifact is the intended source. Core R5 remains separately frozen.
"""
import copy,math,re,unicodedata
from collections import Counter
from .context_resolution import STOP,artifacts,resolve_references,augment_view,can_preclarify
from .routing import unknown
from .routing import rules
from .mechanical import match_mechanical

COMMAND=re.compile(r'\b(?:and|then|also)\s+(?:please\s+)?(?:design|debug|implement|prove|analy[sz]e|calculate|explain|derive|generate|compare|solve|write|build|evaluate|review)\b',re.I)

def guarded_rules(view):
    result=rules(view)
    if result['operation']=='other' and not view.get('instruction_context') and not (view.get('active_instructions') or {}).get('text') and not view.get('pending_task'):
        mechanical=match_mechanical(view['latest']['text'])
        if mechanical:
            return {**{k:v for k,v in mechanical.items() if k!='rule'}, 'demand':'simple',
                    'relation':'independent','reference_ids':[],'needs_context':False,
                    'reason':'Complete bounded mechanical grammar: '+mechanical['rule']}
    if result['operation']=='creative' and COMMAND.search(view['latest']['text']):
        return unknown('Possible additional task inside joke request; classifier required')
    return result

def tokens(text):
    text=unicodedata.normalize('NFKC',text).casefold()
    return [w for w in re.findall(r'[\w]+',text) if w not in STOP]

def bm25(query,documents,k1=1.2,b=.75):
    if not documents:return []
    counts=[Counter(tokens(d)) for d in documents];n=len(counts)
    lengths=[sum(c.values()) for c in counts];avg=sum(lengths)/n or 1
    terms=set(tokens(query));dfs={t:sum(t in c for c in counts) for t in terms}
    return [sum(math.log1p((n-dfs[t]+.5)/(dfs[t]+.5))*
        (c[t]*(k1+1))/(c[t]+k1*(1-b+b*length/avg)) for t in terms if c[t])
        for c,length in zip(counts,lengths)]

def rank(messages,k1=1.2,b=.75,include_body=True):
    items=artifacts(messages)
    # Each source is a paired task/answer or an explicit refinement chain.
    # This experiment stays inside the prepared conversation; production must
    # supply already-authorized scope/branch/revision-filtered artifacts.
    docs=[]
    for item in items:
        title=messages[item['index']]['content'][:900]
        answer=messages[item['index']+1]['content'][:900] if include_body else ''
        docs.append(title+'\n'+answer)
    scores=bm25(messages[-1]['content'],docs,k1,b)
    result=[{**{k:item[k] for k in ['ids','kind','title']},'score':round(score,8)} for item,score in zip(items,scores) if score>0]
    # ID tie-break is for reproducible display only; it conveys no certainty.
    return sorted(result,key=lambda x:(-x['score'],x['ids'][0]))

def preprocess(messages,view,top_k=3):
    resolution=resolve_references(messages)
    if resolution['status']=='not_needed':return view,resolution
    if resolution['status'] in {'ambiguous','missing'} and can_preclarify(view['latest']['text'],resolution):
        return augment_view(view,messages,resolution),resolution
    ranked=rank(messages)[:top_k]
    if not ranked:return augment_view(view,messages,resolution),resolution
    out=copy.deepcopy(view);selected=[];seen={x['id'] for x in out['recent']+out.get('instruction_context',[])}
    for item in ranked:
        for rid in item['ids']:
            if rid in seen:continue
            seen.add(rid);m=messages[int(rid[1:])]
            selected.append({'id':rid,'role':m['role'],'text':m['content'][:900]})
    out['earlier_index']=(selected+[x for x in out['earlier_index'] if x['id'] not in seen])[:12]
    out['allowed_reference_ids']=[x['id'] for x in out['recent']+out['earlier_index']+out.get('instruction_context',[])]
    resolution={'status':'ranked','selected_ids':[],'candidates':ranked,'method':'BM25 retrieval hints; classifier chooses source references'}
    out['reference_resolution']=resolution
    return out,resolution
