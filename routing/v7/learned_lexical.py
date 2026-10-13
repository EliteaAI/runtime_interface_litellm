"""Portable lexical proposals with explicit abstention; no provider labels.

Weights and thresholds are fitted offline. Runtime needs only the standard
library. A proposal is a task descriptor, never a model or capability override.
"""
from collections import Counter
import copy
import math
import re
import unicodedata
from .task_profile import apply_profile, normalize_operation, FAMILIES

REVISION = 'v14-lexical-features-1'


def features(text):
    text = unicodedata.normalize('NFKC', text).casefold()
    words = re.findall(r'\w+', text)
    counts = Counter('w:'+w for w in words)
    counts.update('b:'+a+' '+b for a,b in zip(words,words[1:]))
    for word in text.split():
        padded = ' '+word+' '
        for n in (3,4,5):
            counts.update('c:'+padded[i:i+n] for i in range(max(0,len(padded)-n+1)))
    return counts


def vector(text, vocabulary, idf):
    values = {vocabulary[k]:(1+math.log(n))*idf[vocabulary[k]] for k,n in features(text).items() if k in vocabulary}
    norm = math.sqrt(sum(v*v for v in values.values()))
    return {i:v/norm for i,v in values.items()} if norm else {}


def blocked(context):
    # Deliberately conservative until a context-aware scope is separately trained.
    return (context.get('has_history',False) or context.get('has_tools',False)
            or context.get('has_active_instructions',False) or context.get('pending_count',0)>0)


class Scorer:
    def __init__(self, artifact):
        self.artifact = copy.deepcopy(artifact)
        if artifact.get('feature_revision') != REVISION:
            raise ValueError('Unsupported lexical features')
        self.labels = artifact['labels']
        if len(self.labels)<2 or len(set(self.labels))!=len(self.labels):
            raise ValueError('Distinct lexical classes required')
        count = len(artifact['vocabulary'])
        if (set(artifact['vocabulary'].values()) != set(range(count)) or len(artifact['idf'])!=count
                or len(artifact['coefficients'])!=len(self.labels) or len(artifact['intercept'])!=len(self.labels)
                or any(len(row)!=count for row in artifact['coefficients'])):
            raise ValueError('Invalid portable weight dimensions')
        for values in (artifact['idf'], artifact['intercept'], *artifact['coefficients']):
            if any(type(v) not in (int,float) or not math.isfinite(v) for v in values):
                raise ValueError('Finite lexical weights required')
        for label, descriptor in artifact['descriptors'].items():
            from .routing import OPERATIONS, DEMAND
            if label not in self.labels or descriptor.get('task_family') not in FAMILIES:
                raise ValueError('Unknown task label')
            if (not isinstance(descriptor.get('work_profile'),dict) or descriptor.get('operation') not in OPERATIONS
                    or descriptor.get('demand') not in DEMAND or descriptor.get('effort_need') not in {'low','medium','high'}):
                raise ValueError('Complete task descriptor required')
            if descriptor.get('needs_context') or descriptor.get('relation')!='independent' or descriptor.get('reference_ids'):
                raise ValueError('Lexical labels cannot grant source continuity')
            allowed={'operation','demand','task_family','work_profile','effort_need','relation','reference_ids','needs_context','reason'}
            if set(descriptor)-allowed:
                raise ValueError('Lexical labels must not encode model or execution fields')
            checked=normalize_operation(apply_profile(descriptor,descriptor.get('work_profile')))
            if checked.get('demand')!=descriptor.get('demand'):
                raise ValueError('Lexical label violates product demand floor')

    def propose(self, text, context):
        a=self.artifact
        if len(text)>a.get('maximum_input_chars',4000) or blocked(context):
            return {'accepted':False,'reason':'CONTEXT_OR_SIZE_GUARD','descriptor':None}
        values=vector(text,a['vocabulary'],a['idf'])
        scores=[bias+sum(weights[i]*value for i,value in values.items()) for bias,weights in zip(a['intercept'],a['coefficients'])]
        # Binary logistic regression is exported as symmetric logits, preserving
        # sklearn probabilities under this same softmax implementation.
        largest=max(scores);exp=[math.exp(s-largest) for s in scores];total=sum(exp)
        probabilities=[s/total for s in exp]
        order=sorted(range(len(scores)),key=lambda i:probabilities[i],reverse=True)
        label=self.labels[order[0]];confidence=probabilities[order[0]];margin=confidence-probabilities[order[1]]
        thresholds=a.get('thresholds') or {}
        accepted=bool(values and label in a['descriptors'] and thresholds.get('supported') is True
                      and confidence>=thresholds['probability'] and margin>=thresholds['margin'])
        return {'accepted':accepted,'label':label,'confidence':confidence,'margin':margin,
                'reason':'LEARNED_DESCRIPTOR_PROPOSAL' if accepted else 'ABSTAIN',
                'descriptor':copy.deepcopy(a['descriptors'][label]) if accepted else None}
