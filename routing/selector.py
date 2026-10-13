"""V5 selector artifact loader and pure-Python scorer (#6722 V5 PLAN section 3.1).

INTERIM: until WP-1 lands, the feature hashing and head maths below are this
package's own reading of the contract. WP-1's `scorer.py` and
`test_vectors.json` replace them verbatim; the loader/hash check stays.

Hashing scheme (deterministic, no process hash seed):
- text: request text, lowercased when `features.lowercase`, whitespace collapsed;
- word n-grams over `\\w+` tokens, key `w<n>:<tokens joined by space>`;
- char n-grams over the collapsed text, key `c<n>:<substring>`;
- index = first 8 bytes (big endian) of blake2b(key, digest_size=8) mod hashing_dim;
- counts are L2-normalised; numeric feature i is stored at index hashing_dim + i.
Heads: p_pass = sigmoid(a * (bias + w.x) + b); expected_output_tokens =
max(floor, round(exp(intercept + coef.x))) with the exponent clamped.
"""
import hashlib
import json
import math
import re
from functools import lru_cache
from pathlib import Path

SCHEMA = 'elitea-auto-selector/1'
ARTIFACT_DIR = Path(__file__).parent / 'selector'
MAX_ARTIFACT_BYTES = 2_000_000
MAX_HASHING_DIM = 16384
MAX_FEATURE_CHARS = 20000
NUMERIC = ('log_request_chars', 'history_turns', 'tools_count', 'has_output_schema', 'log_input_bytes')
HEAD_KEY = re.compile(r'^[a-z]+/[a-z0-9][a-z0-9-]*\|[a-z]+$')
ARTIFACT_ID = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$')  # Configurations grammar; case-sensitive
WORD = re.compile(r'\w+')
MAX_EXPONENT = math.log(1_000_000)


def canonical_sha256(artifact):
    """sha256 of the canonical JSON without the `sha256` field."""
    body = {k: v for k, v in artifact.items() if k != 'sha256'}
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(',', ':'),
                                     allow_nan=False).encode()).hexdigest()


def _finite(value, field):
    if type(value) not in (int, float) or not math.isfinite(value) or abs(value) > 1e6:
        raise ValueError(f'Selector {field} must be a bounded finite number')
    return float(value)


def _sparse(weights, size, field):
    if not isinstance(weights, dict):
        raise ValueError(f'Selector {field} must be an object')
    result = {}
    for key, value in weights.items():
        if not isinstance(key, str) or not key.isdigit() or not 0 <= int(key) < size:
            raise ValueError(f'Selector {field} index out of range')
        result[int(key)] = _finite(value, field)
    return result


def _index(key, dim):
    return int.from_bytes(hashlib.blake2b(key.encode('utf-8'), digest_size=8).digest(), 'big') % dim


class Selector:
    def __init__(self, artifact):
        if not isinstance(artifact, dict) or artifact.get('schema') != SCHEMA:
            raise ValueError('Unknown selector schema')
        if not isinstance(artifact.get('id'), str) or not ARTIFACT_ID.fullmatch(artifact['id']):
            raise ValueError('Invalid selector id')
        if artifact.get('sha256') != canonical_sha256(artifact):
            raise ValueError('Selector hash mismatch')
        spec = artifact.get('features')
        if not isinstance(spec, dict):
            raise ValueError('Missing selector feature spec')
        dim = spec.get('hashing_dim')
        if type(dim) is not int or not 1 <= dim <= MAX_HASHING_DIM:
            raise ValueError('Selector hashing_dim out of bounds')
        grams = {}
        for name in ('word_ngrams', 'char_ngrams'):
            pair = spec.get(name)
            if (not isinstance(pair, list) or len(pair) != 2 or any(type(x) is not int for x in pair)
                    or not 1 <= pair[0] <= pair[1] <= 8):
                raise ValueError(f'Selector {name} out of bounds')
            grams[name] = tuple(pair)
        if type(spec.get('lowercase')) is not bool:
            raise ValueError('Selector lowercase must be boolean')
        numeric = spec.get('numeric')
        if not isinstance(numeric, list) or any(x not in NUMERIC for x in numeric) or len(set(numeric)) != len(numeric):
            raise ValueError('Unknown selector numeric feature')
        heads = artifact.get('heads')
        if not isinstance(heads, dict) or not heads:
            raise ValueError('Selector has no heads')
        size = dim + len(numeric)
        self.heads = {}
        for key, head in heads.items():
            if not isinstance(key, str) or not HEAD_KEY.fullmatch(key) or not isinstance(head, dict):
                raise ValueError('Invalid selector head key')
            calibration, output = head.get('calibration'), head.get('output_tokens')
            if not isinstance(calibration, dict) or not isinstance(output, dict):
                raise ValueError('Selector head lacks calibration or output head')
            floor = output.get('floor')
            if type(floor) is not int or not 1 <= floor <= 1_000_000:
                raise ValueError('Selector output floor out of bounds')
            self.heads[key] = {
                'weights': _sparse(head.get('weights'), size, 'weights'), 'bias': _finite(head.get('bias'), 'bias'),
                'a': _finite(calibration.get('a'), 'calibration'), 'b': _finite(calibration.get('b'), 'calibration'),
                'intercept': _finite(output.get('intercept'), 'output intercept'),
                'coef': _sparse(output.get('coef'), size, 'output coef'), 'floor': floor}
        metrics = artifact.get('metrics', {})
        band = metrics.get('noise_band', 0.0) if isinstance(metrics, dict) else None
        if type(band) not in (int, float) or not 0.0 <= band <= 1.0:
            raise ValueError('Selector metrics.noise_band must be between 0 and 1')
        # p_pass spread among qualified heads that the fit cannot distinguish.
        self.noise_band = float(band)
        self.id, self.sha256 = artifact['id'], artifact['sha256']
        self.dim, self.numeric, self.lowercase = dim, tuple(numeric), spec['lowercase']
        self.word_ngrams, self.char_ngrams = grams['word_ngrams'], grams['char_ngrams']

    def features(self, request_text, history_turns, tools_count, has_output_schema, input_bytes):
        if not isinstance(request_text, str):
            raise ValueError('Selector request text must be a string')
        text = request_text[:MAX_FEATURE_CHARS]
        if self.lowercase:
            text = text.lower()
        text = ' '.join(text.split())
        counts = {}
        tokens = WORD.findall(text)
        for n in range(self.word_ngrams[0], self.word_ngrams[1]+1):
            for i in range(len(tokens)-n+1):
                key = f'w{n}:' + ' '.join(tokens[i:i+n])
                counts[key] = counts.get(key, 0) + 1
        for n in range(self.char_ngrams[0], self.char_ngrams[1]+1):
            for i in range(len(text)-n+1):
                key = f'c{n}:' + text[i:i+n]
                counts[key] = counts.get(key, 0) + 1
        vector = {}
        for key, count in counts.items():
            index = _index(key, self.dim)
            vector[index] = vector.get(index, 0.0) + count
        norm = math.sqrt(sum(v*v for v in vector.values()))
        if norm:
            vector = {k: v/norm for k, v in vector.items()}
        values = {'log_request_chars': math.log1p(len(request_text)), 'history_turns': float(history_turns),
                  'tools_count': float(tools_count), 'has_output_schema': 1.0 if has_output_schema else 0.0,
                  'log_input_bytes': math.log1p(max(0, input_bytes))}
        for i, name in enumerate(self.numeric):
            vector[self.dim+i] = values[name]
        return vector

    @staticmethod
    def _dot(weights, vector):
        small, large = (weights, vector) if len(weights) <= len(vector) else (vector, weights)
        return sum(value*large.get(index, 0.0) for index, value in small.items())

    def score(self, features):
        result = {}
        for key, head in self.heads.items():
            z = head['a']*(head['bias']+self._dot(head['weights'], features))+head['b']
            p = 1.0/(1.0+math.exp(-z)) if z >= 0 else math.exp(z)/(1.0+math.exp(z))
            tokens = math.exp(min(MAX_EXPONENT, head['intercept']+self._dot(head['coef'], features)))
            result[key] = {'p_pass': p, 'expected_output_tokens': max(head['floor'], int(round(tokens)))}
        return result


def load(path):
    """Verify size, schema, bounds and sha256; raise ValueError otherwise."""
    path = Path(path)
    if path.stat().st_size > MAX_ARTIFACT_BYTES:
        raise ValueError('Selector artifact exceeds its size bound')
    try:
        artifact = json.loads(path.read_bytes())
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError('Unreadable selector artifact') from exc
    return Selector(artifact)


def manifest(directory=None):
    value = json.loads(((directory or ARTIFACT_DIR) / 'MANIFEST.json').read_text())
    if not isinstance(value, dict) or not isinstance(value.get('artifacts'), dict):
        raise ValueError('Invalid selector manifest')
    return value


@lru_cache(maxsize=4)
def _installed(artifact_id, directory):
    entry = manifest(directory)['artifacts'].get(artifact_id)
    if not isinstance(entry, dict) or not isinstance(entry.get('file'), str) or '/' in entry['file']:
        raise ValueError('Selector artifact is not installed')
    selector = load(directory / entry['file'])
    if selector.id != artifact_id or selector.sha256 != entry.get('sha256'):
        raise ValueError('Installed selector does not match its manifest')
    selector.stub = entry.get('stub') is True
    return selector


def installed(artifact_id):
    """The installed artifact named by the policy; its manifest hash must match."""
    if not isinstance(artifact_id, str) or not ARTIFACT_ID.fullmatch(artifact_id):
        raise ValueError('No selector artifact configured')
    try:
        return _installed(artifact_id, ARTIFACT_DIR)
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError('Selector artifact is not installed') from exc
