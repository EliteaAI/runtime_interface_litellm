"""Caller-owned conversation state with explicit checkpoint serialization.

Only user messages or trusted application events create/cancel pending actions.
Provider cache observations survive restoration without refreshing their age.
"""
import copy,json,math,re,threading,time,uuid
from .retrieval import ContextIndex,digest

CONTINUE=re.compile(r'(?:go|proceed|continue|start(?: coding)?)[.! ]*',re.I)
READY=re.compile(r'\b(?:ready to (?:code|implement)|wait for my (?:go|confirmation).*?(?:code|implement)|(?:code|implement).*?wait for my (?:go|confirmation))\b',re.I|re.S)
CANCEL=re.compile(r'(?:please\s+)?(?:cancel|forget|drop)\s+(?:all(?:\s+pending)?\s+tasks|(?:the |my )?(?:pending )?(?:task|implementation|plan))[.! ]*',re.I)
CACHE_STATE_REVISION='bounded-cache-checkpoint-1'
MAX_CACHE_BYTES=64_000


def valid_time(value):
    return type(value) in (int,float) and math.isfinite(value) and value>=0


def bounded_cache(rows):
    """Optional advisory state may be dropped; task/intent state must survive."""
    if not isinstance(rows,list):return []
    kept=[];seen=set();size=2
    for row in reversed(rows[-64:]):
        if not isinstance(row,dict):continue
        try:
            if not valid_time(row['observed_at']):continue
            if type(row['epoch']) is not int or row['epoch']<0:continue
            if any(type(row[k]) is not int or row[k]<0 for k in ('read_tokens','observed_write_tokens')):continue
            if row['observed_read_tokens'] is not None and (type(row['observed_read_tokens']) is not int or row['observed_read_tokens']<0):continue
            if any(not isinstance(row[k],str) or len(row[k])>256 for k in ('variant','identity')):continue
            if not isinstance(row['message_hashes'],list) or not row['message_hashes']:continue
            if any(not isinstance(h,str) or not re.fullmatch('[0-9a-f]{64}',h) for h in row['message_hashes']):continue
            if row['returned_model'] is not None and (not isinstance(row['returned_model'],str) or len(row['returned_model'])>256):continue
            key=digest([row[k] for k in ('identity','epoch','message_hashes','observed_at')])
            encoded=json.dumps(row,allow_nan=False).encode()
            if key in seen or size+len(encoded)+2>MAX_CACHE_BYTES:continue
        except (KeyError,TypeError,ValueError):continue
        seen.add(key);size+=len(encoded)+2;kept.append(copy.deepcopy(row))
    return list(reversed(kept))

def message_digest(message):
    return digest({k:message.get(k)for k in ['role','content','tool_calls','tool_call_id']})


class Session:
    def __init__(self,session_id=None):
        self.id=session_id or uuid.uuid4().hex;self.lock=threading.RLock()
        self.index=ContextIndex();self.epoch=0;self.history_hashes=[];self.intents={};self.cache=[]
        self.evidence_sets={};self.events=[]

    def prepare(self,messages,access_revision='local-authorized-v1'):
        hashes=[message_digest(m) for m in messages]
        with self.lock:
            previous_access=getattr(self,'access_revision',access_revision)
            # Append is valid; edits, deletion, compaction or access changes
            # invalidate derived state. Caller must submit authorized history.
            changed=hashes[:len(self.history_hashes)]!=self.history_hashes or previous_access!=access_revision
            if changed:
                self.epoch+=1;self.cache=[];self.evidence_sets={}
                for intent in self.intents.values():
                    if intent['status']=='awaiting_user':intent['status']='stale'
            self.history_hashes=hashes;self.access_revision=access_revision
            latest=messages[-1].get('content','') if messages else ''
            if messages and messages[-1]['role']=='user' and isinstance(latest,str) and CANCEL.fullmatch(latest.strip()):
                active=[i for i in self.intents.values() if i['status']=='awaiting_user']
                if len(active)==1 or re.search(r'\ball\b',latest,re.I):
                    for intent in active:intent['status']='cancelled'
            return self.epoch

    def record_pending(self,messages,decision,result,policy_revision,answer_index=None):
        prompt=messages[-1].get('content','')
        if messages[-1]['role']!='user' or not isinstance(prompt,str) or not READY.search(prompt) or result.get('finish_reason')!='stop':return
        answer=result.get('message',{}).get('content')
        if not answer or decision.get('action')=='clarify':return
        rid=f'm{len(messages)-1}';aid=f'm{len(messages) if answer_index is None else answer_index}'
        with self.lock:
            # One intent per explicit source request; retries do not duplicate it.
            key=message_digest(messages[-1])
            if any(i['request_digest']==key for i in self.intents.values()):return
            self.register_pending({'id':uuid.uuid4().hex,'request_digest':key,'status':'awaiting_user',
                'source_ids':[rid,aid], 'source_digests':{rid:key,aid:message_digest({'role':'assistant','content':answer})},
                'next_task':'Implement the design in the explicitly requested prior task', 'next_demand':'deep',
                'decision':copy.deepcopy(decision),'policy_revision':policy_revision,'epoch':self.epoch})

    def register_pending(self,intent):
        """Trusted application event adapter. Never expose directly to tool text."""
        with self.lock:
            self.intents[intent['id']]=copy.deepcopy(intent)
            while len(self.intents)>32:self.intents.pop(next(iter(self.intents)))

    def pending(self,messages,policy_revision):
        if not CONTINUE.fullmatch((messages[-1].get('content')or'').strip()):return None
        with self.lock:
            active=[i for i in self.intents.values() if i['status']=='awaiting_user']
            if not self.intents:return None
            if len(active)!=1:return {'action':'clarify','reason':'No unique pending task','pending_count':len(active)}
            intent=copy.deepcopy(active[0])
        valid=intent['epoch']==self.epoch and intent['policy_revision']==policy_revision
        for rid,expected in intent['source_digests'].items():
            idx=int(rid[1:]);valid=valid and idx<len(messages)-1 and message_digest(messages[idx])==expected
        return {'action':'resume' if valid else 'classify','intent':intent,'reason':'Verified pending task' if valid else 'Pending task revision changed'}

    def claim_pending(self,intent_id):
        with self.lock:
            item=self.intents.get(intent_id)
            if not item or item['status']!='awaiting_user':return False
            item['status']='running';return True

    def finish_pending(self,intent_id,result):
        if not intent_id:return
        with self.lock:
            if intent_id in self.intents:
                reason=result.get('finish_reason')
                if reason=='stop':self.intents[intent_id]['status']='completed'
                elif reason!='tool_calls':self.intents[intent_id]['status']='interrupted'

    def authorize_retry(self,intent_id):
        """Trusted resume event after the caller reconciles possible effects."""
        with self.lock:
            intent=self.intents.get(intent_id)
            if not intent or intent['status']!='interrupted':raise ValueError('No interrupted intent to resume')
            intent['status']='awaiting_user'

    def evidence_manifest(self,messages):
        refs=[f'm{i}' for i,m in enumerate(messages[:-1]) if m['role']=='tool']
        if not refs:return None
        value={'id':'set-'+digest([(r,message_digest(messages[int(r[1:])])) for r in refs])[:16],
            'scope_id':self.id,'epoch':self.epoch,'reference_ids':refs,'complete_for_scope':True}
        with self.lock:
            self.evidence_sets[value['id']]=value
            while len(self.evidence_sets)>8:self.evidence_sets.pop(next(iter(self.evidence_sets)))
        return value

    def checkpoint(self):
        with self.lock:
            return json.loads(json.dumps({'version':2,'id':self.id,'epoch':self.epoch,'history_hashes':self.history_hashes,
                'access_revision':getattr(self,'access_revision','local-authorized-v1'),'intents':self.intents,
                'evidence_sets':self.evidence_sets,'cache':bounded_cache(self.cache)}))

    @classmethod
    def restore(cls,value):
        if value.get('version') not in (1,2):raise ValueError('Unsupported routing checkpoint')
        s=cls(value['id'])
        for k in ['epoch','history_hashes','access_revision','intents','evidence_sets']:setattr(s,k,copy.deepcopy(value[k]))
        if value['version']==2:s.cache=bounded_cache(value.get('cache',[]))
        return s


def cache_identity(gateway,variant,tools,cap):
    return digest({'gateway':getattr(gateway,'base',type(gateway).__name__),'project':getattr(gateway,'project',None),
        'model':variant['model'],'effort':variant['effort'],'tools':tools or [],'completion_cap':cap,
        'cache_policy':getattr(gateway,'cache_policy_revision','provider-default')})


def observe_cache(session,gateway,variant_id,variant,messages,tools,cap,result,now=None):
    now=time.time() if now is None else now
    if not valid_time(now):return
    usage=result.get('usage')or{};detail=usage.get('prompt_tokens_details')or{}
    read=detail.get('cached_tokens',usage.get('cache_read_input_tokens'))
    write=detail.get('cache_creation_tokens',usage.get('cache_creation_input_tokens',0))
    if read is not None and (type(read)is not int or read<0):return
    if type(write)is not int or write<0:return
    if read is None and not write:return  # Unknown is not an observed miss.
    if type(usage.get('prompt_tokens'))is not int or (read or 0)+write>usage['prompt_tokens']:return
    item={'variant':variant_id,'identity':cache_identity(gateway,variant,tools,cap),
        'message_hashes':[digest(m) for m in messages],'read_tokens':(read or 0)+write,'observed_read_tokens':read,'observed_write_tokens':write,'returned_model':result.get('returned_model'),
        'observed_at':now,'epoch':session.epoch}
    with session.lock:
        session.cache=bounded_cache([*session.cache,item])


def cache_quote(session,gateway,variant_id,variant,messages,tools,cap,ttl=180,now=None):
    identity=cache_identity(gateway,variant,tools,cap);hashes=[digest(m) for m in messages];now=time.time() if now is None else now
    with session.lock:
        matches=[x for x in session.cache if x['variant']==variant_id and x['identity']==identity and x['epoch']==session.epoch
                 and 0<=now-x['observed_at']<=ttl and hashes[:len(x['message_hashes'])]==x['message_hashes']]
    if not matches:return None
    best=max(matches,key=lambda x:x['read_tokens'])
    observed=[x for x in matches if type(x.get('observed_read_tokens'))is int]
    hits=sum(x['observed_read_tokens']>0 for x in observed)
    return {'read_token_upper_bound':best['read_tokens'],'hit_probability_range':[0,1],
        'compatible_prefix':True,'route_identity_matches':True,'effort_matches':True,'within_ttl':True,
        'variant':variant_id,'returned_model_last_observed':best['returned_model'],
        'historical_read_hits':hits,'historical_observations':len(observed),
        'observed_read_tokens':best.get('observed_read_tokens',best['read_tokens']),'observed_write_tokens':best.get('observed_write_tokens',0),
        'basis':'Observed cache read/write on identical prior request prefix; hidden gateway/provider changes remain uncertain'}
