"""Scope-owned incremental preparation with query-dependent passage equivalence.

Never changes generation messages. All input must already be authorized for the
scope. The lock covers index publication; model requests run outside that lock.
"""
import copy,hashlib,json,math,threading
from collections import Counter,OrderedDict,defaultdict
from .lexical import tokens
from .context_resolution import resolve_references
from .protocol import content,excerpt,SYSTEM
from .routing import clip


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()


class Passage:
    def __init__(self,text):
        self.text=text;self.lines=text.splitlines();self.postings=defaultdict(list)
        if len(text)>900:
            for i,line in enumerate(self.lines):
                for term in set(tokens(line)):self.postings[term].append(i)
        self.cache=OrderedDict();self.lock=threading.Lock()

    def project(self,query):
        scores=Counter()
        if len(self.text)>900:
            for term in set(tokens(query)):
                scores.update(self.postings.get(term,()))
        best=min(scores,key=lambda i:(-scores[i],i)) if scores else -1
        with self.lock:
            if best in self.cache:
                self.cache.move_to_end(best);return self.cache[best]
            if len(self.text)<=900:result=self.text
            elif best>=0 and len(self.lines)>1:
                center=self.lines[best]
                if len(center)>900:result=clip(center,900)
                else:
                    remaining=900-len(center)
                    result=clip(self.text[:max(0,remaining//2)],remaining//2)+'\n[matching source line]\n'+center+'\n[other text omitted]'
            else:result=clip(self.text,900)
            value=(result,Counter(tokens(result)))
            self.cache[best]=value
            while len(self.cache)>8:self.cache.popitem(last=False)
            return value


class ContextIndex:
    def __init__(self,max_bytes=8_000_000,max_entries=4096):
        self.lock=threading.RLock();self.hashes=[];self.records=[];self.epoch=None
        self.max_bytes=max_bytes;self.max_entries=max_entries;self.builds=0

    def snapshot(self,messages,epoch=0):
        if len(messages)>self.max_entries:raise ValueError('Authorized history exceeds configured index ceiling')
        texts=[content(m) for m in messages]
        if sum(len(t.encode()) for t in texts)>self.max_bytes:raise ValueError('Authorized history exceeds configured byte ceiling')
        hashes=[digest(m) for m in messages]
        with self.lock:
            append=self.epoch==epoch and hashes[:len(self.hashes)]==self.hashes
            if not append:self.hashes=[];self.records=[]
            for t in texts[len(self.hashes):]:self.records.append(Passage(t));self.builds+=1
            self.hashes=hashes;self.epoch=epoch
            return tuple(self.records),{'appended':append,'record_count':len(self.records),'total_record_builds':self.builds}


def score_counts(query,counts):
    if not counts:return []
    terms=set(tokens(query));n=len(counts);lengths=[sum(c.values()) for c in counts];avg=sum(lengths)/n or 1
    dfs={t:sum(t in c for c in counts) for t in terms}
    return [sum(math.log1p((n-dfs[t]+.5)/(dfs[t]+.5))*c[t]*2.2/(c[t]+1.2*(.25+.75*length/avg)) for t in terms if c[t]) for c,length in zip(counts,lengths)]


def bounded(view,max_bytes):
    """Keep latest/instructions; shrink selected evidence with explicit omission.

    Validation sees exactly the emitted IDs. The complete evidence manifest is
    retained server-side by Session, not expanded into a model's reference list.
    """
    out=copy.deepcopy(view)
    if 'evidence_coverage' in out:
        out['evidence_coverage']['tool_entries_visible']=sum(x.get('role')=='tool' for x in out['recent']+out['earlier_index'])
    required=set((out.get('reference_resolution') or {}).get('selected_ids',[]))
    required.update((out.get('pending_task') or {}).get('source_ids',[]))
    required.update((out.get('required_source_coverage') or {}).get('omitted_ids',[]))
    def update():
        out['allowed_reference_ids']=[x['id'] for lane in ['recent','earlier_index','instruction_context'] for x in out.get(lane,[])]
        visible=set(out['allowed_reference_ids'])
        missing=required-visible
        if missing:
            out['required_source_coverage']={'status':'unavailable','omitted_ids':sorted(missing)}
            resolution=out.setdefault('reference_resolution',{})
            resolution.update(status='missing',selected_ids=[rid for rid in resolution.get('selected_ids',[]) if rid in visible],
                              candidates=[],method='Required source group omitted by classifier view budget')
            if 'pending_task' in out:
                out['pending_task']['source_ids']=[rid for rid in out['pending_task'].get('source_ids',[]) if rid in visible]
    update();original_size=len(json.dumps(out,ensure_ascii=False).encode())
    protected_groups={x['source_group'] for lane in ['recent','earlier_index'] for x in out[lane]
                      if x['id'] in required and x.get('source_group') is not None}
    for cap in [700,450,250]:
        if len(json.dumps(out,ensure_ascii=False).encode())<=max_bytes:break
        for lane in ['recent','earlier_index']:
            for item in out[lane]:
                if len(item['text'])>cap:item['text']=clip(item['text'],cap);item['content_truncated']=True
    while len(json.dumps(out,ensure_ascii=False).encode())>max_bytes and out['earlier_index']:
        removable=[i for i,x in enumerate(out['earlier_index'])
                   if x['id'] not in required and x.get('source_group') not in protected_groups]
        # Required groups win over unrelated matches. If they alone cannot fit,
        # remove a whole group and expose unresolved coverage, never stale IDs.
        if not removable:removable=list(range(len(out['earlier_index'])))
        worst=min(removable,key=lambda i:(out['earlier_index'][i].get('retrieval_score',0),i))
        group = out['earlier_index'][worst].get('source_group')
        removed = [out['earlier_index'][worst]] if group is None else [x for x in out['earlier_index'] if x.get('source_group') == group]
        out['earlier_index'] = [x for x in out['earlier_index'] if x not in removed]
        out['history_omitted'] += len(removed)
        update()
    if original_size>max_bytes:out['context_truncated']=True
    if 'evidence_coverage' in out:
        out['evidence_coverage']['tool_entries_visible']=sum(x.get('role')=='tool' for x in out['recent']+out['earlier_index'])
    if len(json.dumps(out,ensure_ascii=False).encode())>max_bytes:
        raise ValueError('Latest task and active instructions exceed classifier view budget')
    # Add bounded telemetry only outside the classifier payload.
    return out


def build(messages,hint,index,*,epoch=0,older_cap=None,max_bytes=24000,force_sources=()):
    if not messages or messages[-1].get('role')!='user':raise ValueError('New scope needs a user task')
    if any(m.get('role')not in {'user','assistant','tool','system'} for m in messages):raise ValueError('Unsupported message role')
    has_tools=any(m['role']=='tool' or m.get('tool_calls') for m in messages)
    # One budget for plain and tool histories. A group is an actual external
    # user turn and its assistant/tool trajectory, not a similarity cluster.
    if older_cap is None:older_cap=max(4,min(64,(max_bytes-8000)//800))
    if type(older_cap)is not int or not 1<=older_cap<=64:raise ValueError('Invalid older-evidence allowance')
    records,stats=index.snapshot(messages,epoch);q=content(messages[-1])
    groups=[]
    for i,m in enumerate(messages[:-1]):
        if m['role']=='system':continue
        if m['role']=='user' or not groups:groups.append([])
        groups[-1].append(i)
    counts=[]
    for group in groups:
        counter=Counter()
        for i in group:counter.update(records[i].project(q)[1])
        counts.append(counter)
    scores=score_counts(q,counts)
    resolution=(resolve_references(messages) if not has_tools else
        {'status':'ranked','selected_ids':[],'candidates':[],
         'method':'Incremental task-group matching; classifier chooses references'})
    anchors={int(rid[1:]) for rid in resolution.get('selected_ids',[])}
    ranked=sorted(range(len(groups)),key=lambda g:(bool(anchors.intersection(groups[g])),scores[g],g),reverse=True)
    picked=[g for g in ranked if scores[g]>0 or anchors.intersection(groups[g])][:older_cap]
    # A refinement chain may span more groups than the optional-history cap.
    picked += [g for g in ranked if anchors.intersection(groups[g]) and g not in picked]
    # An unanchored short follow-up needs the latest complete turn. An explicit
    # old-topic return does not need the intervening joke/weather trajectory.
    recent_group=len(groups)-1 if groups and not anchors else None
    if recent_group is not None and recent_group not in picked:picked.append(recent_group)
    def entry(i,cap=900):
        text=records[i].project(q)[0] if cap==900 else excerpt(records[i].text,q,cap)
        row={'id':f'm{i}','role':messages[i]['role'],'text':text,'content_truncated':len(records[i].text)>cap}
        if messages[i]['role']=='tool':row['tool_call_id']=messages[i].get('tool_call_id')
        return row
    recent=[];earlier=[]
    for g in sorted(picked):
        rows=[{**entry(i),'source_group':f'turn-{groups[g][0]}','retrieval_score':round(scores[g],6)} for i in groups[g]]
        # Keep all evidence in the reducible lane; recent is a small complete
        # turn only. Large tool turns must also fit the same byte budget.
        (recent if g==recent_group and len(rows)<=4 else earlier).extend(rows)
    instructions=[entry(i,1000) for i,m in enumerate(messages) if m['role']=='system'][:2]
    total=sum(map(len,groups));visible=len(recent)+len(earlier)
    v={'latest':{'id':f'm{len(messages)-1}','text':clip(q,5000)},'recent':recent,'earlier_index':earlier,
       'instruction_context':instructions,'allowed_reference_ids':[], 'history_omitted':total-visible,
       'context_truncated':total>visible or len(q)>5000 or any(x['content_truncated'] for x in recent+earlier+instructions),
       'execution_hint':hint or {'kind':'chat_turn'},'reference_resolution':resolution}
    if has_tools:
        v['evidence_coverage']={'tool_entries_total':sum(m['role']=='tool' for m in messages),
            'tool_entries_visible':0,'older_cap':older_cap,'full_history_chars':sum(len(r.text) for r in records),
            'ranking':'BM25 over complete task groups and matching passages; full generation history unchanged'}
    if force_sources:
        source_entries=[]
        for rid in force_sources:
            i=int(rid[1:])
            if i>=len(messages)-1:raise ValueError('Pending source outside current history')
            source_entries.append({'id':rid,'role':messages[i]['role'],'text':excerpt(content(messages[i]),content(messages[-1]),1800),
                                   'source_group':next((f'turn-{g[0]}' for g in groups if i in g),None),
                                   'content_truncated':len(content(messages[i]))>1800})
        v['recent']=[];v['earlier_index']=source_entries
        v['history_omitted']=total-sum(x['role']!='system' for x in source_entries)
        v['context_truncated']=v['history_omitted']>0 or len(q)>5000 or any(x['content_truncated'] for x in source_entries+instructions)
        resolution={'status':'ranked','selected_ids':list(force_sources),'candidates':[],'method':'Validated pending-task sources'}
        v['reference_resolution']=resolution
    view=bounded(v,max_bytes)
    return view,view['reference_resolution']
