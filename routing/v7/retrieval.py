"""Scope-owned incremental preparation with query-dependent passage equivalence.

Never changes generation messages. All input must already be authorized for the
scope. The lock covers index publication; model requests run outside that lock.
"""
import copy,hashlib,json,math,threading
from collections import Counter,OrderedDict,defaultdict
from .lexical import tokens,preprocess as text_preprocess
from .protocol import content,excerpt,SYSTEM
from .routing import assemble,clip


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
    def update():
        out['allowed_reference_ids']=[x['id'] for lane in ['recent','earlier_index','instruction_context'] for x in out.get(lane,[])]
    update();original_size=len(json.dumps(out,ensure_ascii=False).encode())
    for cap in [700,450,250]:
        if len(json.dumps(out,ensure_ascii=False).encode())<=max_bytes:break
        for lane in ['recent','earlier_index']:
            for item in out[lane]:
                if len(item['text'])>cap:item['text']=clip(item['text'],cap);item['content_truncated']=True
    while len(json.dumps(out,ensure_ascii=False).encode())>max_bytes and out['earlier_index']:
        worst=min(range(len(out['earlier_index'])),key=lambda i:(out['earlier_index'][i].get('retrieval_score',0),i))
        out['earlier_index'].pop(worst);out['history_omitted']+=1;update()
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
    # Evidence breadth follows the configured byte budget, rather than an
    # invariant twelve-message ceiling. Final serialization remains authoritative.
    if older_cap is None:older_cap=max(4,min(64,(max_bytes-8000)//800))
    if type(older_cap)is not int or not 1<=older_cap<=64:raise ValueError('Invalid older-evidence allowance')
    if not has_tools:
        v=assemble(messages,hint);v,resolution=text_preprocess(messages,v)
    else:
        records,stats=index.snapshot(messages,epoch);q=content(messages[-1])
        ids=[i for i,m in enumerate(messages[:-1]) if m['role']!='system'];recent_ids=ids[-4:];older=ids[:-4]
        projected=[records[i].project(q) for i in older]
        scores=score_counts(q,[x[1] for x in projected])
        picked=sorted(sorted(zip(older,scores),key=lambda x:(-x[1],-x[0]))[:older_cap])
        def entry(i,cap=900):
            text=records[i].project(q)[0] if cap==900 else excerpt(records[i].text,q,cap)
            row={'id':f'm{i}','role':messages[i]['role'],'text':text,'content_truncated':len(records[i].text)>cap}
            if messages[i]['role']=='tool':row['tool_call_id']=messages[i].get('tool_call_id')
            return row
        recent=[entry(i) for i in recent_ids];earlier=[{**entry(i),'retrieval_score':round(score,6)} for i,score in picked]
        instructions=[entry(i,1000) for i,m in enumerate(messages) if m['role']=='system'][:2]
        v={'latest':{'id':f'm{len(messages)-1}','text':clip(q,5000)},'recent':recent,'earlier_index':earlier,
           'instruction_context':instructions,'allowed_reference_ids':[], 'history_omitted':len(ids)-len(recent)-len(earlier),
           'context_truncated':len(ids)>len(recent)+len(earlier) or len(q)>5000 or any(x['content_truncated'] for x in recent+earlier+instructions),
           'execution_hint':hint or {'kind':'chat_turn'},'evidence_coverage':{'tool_entries_total':sum(m['role']=='tool' for m in messages),
             'tool_entries_visible':0,'older_cap':older_cap,'full_history_chars':sum(len(r.text) for r in records),
             'ranking':'BM25 over bounded matching passages; full generation history unchanged'}}
        resolution={'status':'ranked','selected_ids':[],'candidates':[],'method':'Incremental matching-passages; classifier chooses references'}
    if force_sources:
        source_entries=[]
        for rid in force_sources:
            i=int(rid[1:])
            if i>=len(messages)-1:raise ValueError('Pending source outside current history')
            source_entries.append({'id':rid,'role':messages[i]['role'],'text':excerpt(content(messages[i]),content(messages[-1]),1800)})
        v['recent']=[];v['earlier_index']=source_entries
        resolution={'status':'ranked','selected_ids':[],'candidates':[],'method':'Validated pending-task sources'}
    return bounded(v,max_bytes),resolution
