"""Conservative source-artifact resolution before routing.

PoC artifact types are inferred cheaply from prior task text. Production should
use existing typed artifact/message metadata and revisions when available.
This component never grants model/provider access.
"""
import re

STOP=set('the a an to from that this it its our your earlier previous above before return revisit back resume continue again create generate turn into use as source for acceptance criteria checks tests testable given when then using with and of in on please let us what we discussed already'.split())
KINDS={
    'design':r'\b(architectur\w*|design\w*|specification\w*|contract\w*|protocol\w*|pipeline\w*|requirement\w*|user stor\w*)\b',
    'creative':r'\b(joke|poem|story|haiku|creative)\b',
    'weather':r'\b(weather|forecast|rain)\b',
    'sports':r'\b(chess|score|sport|match)\b'
}


def words(text):return set(re.findall(r'\w{3,}',text.lower()))-STOP


def artifacts(messages):
    result=[]
    for i,m in enumerate(messages[:-1]):
        if m.get('role')!='user' or not isinstance(m.get('content'),str):continue
        answer=messages[i+1] if i+1<len(messages)-1 else None
        if not answer or answer.get('role')!='assistant' or not isinstance(answer.get('content'),str):continue
        title=m['content'];kind='other'
        # Current task intent, not words incidentally appearing in its response.
        for k,pattern in KINDS.items():
            if re.search(pattern,title,re.I):kind=k;break
        item={'ids':[f'm{i}',f'm{i+1}'],'kind':kind,'title':title[:220],
              'terms':words(title),'index':i}
        if kind=='design' and re.search(r'\b(return|revisit|back|refine|earlier)\b',title,re.I):
            prior=[a for a in result if a['kind']=='design']
            scores=[len(item['terms']&a['terms']) for a in prior];best=max(scores,default=0)
            parents=[a for a,s in zip(prior,scores) if s==best and s>0]
            if len(parents)==1:
                parent=parents[0];item['ids']=(parent['ids']+item['ids'])[-8:]
                item['terms'] |= parent['terms'];result.remove(parent)
        result.append(item)
    return result


def resolve_references(messages):
    latest=messages[-1].get('content','') if messages else ''
    if not isinstance(latest,str):return {'status':'not_needed','selected_ids':[],'candidates':[]}
    referential=bool(re.search(r'\b(earlier|previous|above|revisit|resume|discussed|described|mentioned)\b|\b(return|back)\s+to\b|\bleft off\b|\b(turn|convert|use|generate|create|derive|write).{0,80}\b(that|it)\b',latest,re.I))
    wants_criteria=bool(re.search(r'acceptance|\bACs?\b|Given.?When.?Then|QA checklist',latest,re.I))
    if not referential:
        return {'status':'not_needed','selected_ids':[],'candidates':[]}
    all_artifacts=artifacts(messages)
    types=[k for k,p in KINDS.items() if re.search(p,latest,re.I)]
    if wants_criteria and not any(k in types for k in ['creative','weather','sports']):types=['design']
    candidates=[a for a in all_artifacts if not types or a['kind'] in types]
    # Generic topic-return requests prefer substantive artifacts when present,
    # but do not use recency to manufacture certainty between two designs.
    if not types and any(a['kind']=='design' for a in candidates):
        candidates=[a for a in candidates if a['kind']=='design']
    # Explicit refinement chains were grouped above; production should use
    # authoritative artifact lineage instead of deriving it from prose.
    query=words(latest)
    scores=[len(query&a['terms']) for a in candidates]
    best=max(scores,default=0)
    selected=[a for a,score in zip(candidates,scores) if score==best] if best else candidates
    public=[{k:a[k] for k in ['ids','kind','title']} for a in selected[:6]]
    status='missing' if not selected else 'unique' if len(selected)==1 else 'ambiguous'
    return {'status':status,'selected_ids':selected[0]['ids'] if status=='unique' else [],
            'candidates':public,'artifact_count':len(all_artifacts),
            'method':'typed source compatibility + explicit term overlap; no recency tie-break'}


def can_preclarify(latest,resolution):
    """Only narrow standalone source requests can stop before classification.

    An empty filtered set may mean our cheap artifact typing missed a source.
    Quoted/negated references require semantic interpretation by the classifier.
    """
    text=latest.strip()
    if re.search(r'["`\n]|\b(no|not|without|quoted|log)\b',text,re.I):return False
    generic=r'(?:go back to (?:it|that)|(?:let us )?return to (?:it|that)|resume the previous one|pick up where we left off before)[.!? ]*'
    if resolution['status']=='ambiguous':return bool(re.fullmatch(generic,text,re.I))
    if resolution['status']=='missing' and resolution.get('artifact_count')==0:
        return bool(re.match(r'^(create|derive|turn|generate|continue)\b',text,re.I))
    return False


def augment_view(view,messages,resolution):
    view={**view,'reference_resolution':resolution}
    if resolution['status']=='unique':
        present={x['id'] for x in view['recent']+view['earlier_index']}
        index=list(view['earlier_index'])
        for rid in resolution['selected_ids']:
            if rid in present:continue
            m=messages[int(rid[1:])]
            index.insert(0,{'id':rid,'role':m['role'],'text':m['content'][:900]})
        view['earlier_index']=index[:12]
        view['allowed_reference_ids']=[x['id'] for x in view['recent']+view['earlier_index']+view.get('instruction_context',[])]
    return view


def clarification(resolution):
    if resolution['status']=='missing':
        return 'I cannot identify the required source in this conversation. Please name or provide the artifact you want me to use.'
    titles=[x['title'][:110] for x in resolution.get('candidates',[])[:3]]
    return 'Which earlier item should I use? '+(' / '.join(titles) if titles else 'Please name the source task or artifact.')
