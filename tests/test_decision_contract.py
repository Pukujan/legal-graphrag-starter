"""Root-authored deterministic decision contract, before provider implementation."""
import copy
import math
import pytest
from legal_graph.decisions import parse_decision_response, cache_key, filter_annotations

LABELS = ['asserted', 'negated', 'hypothetical', 'forward_looking', 'unclear']
QUESTIONS = {'support': {'type': 'noul'}, 'assertion': {'type': 'choice', 'choices': LABELS}}


def raw_response():
    return {'id': 'synthetic-request', 'model': 'fixture-model-v1', 'provider': 'fixture-provider',
            'usage': {'cost': 0.0}, 'answers': {
                'support': {'noul': 0.9},
                'assertion': {'choice': 'asserted', 'confidence': 0.8,
                              'probabilities': dict(zip(LABELS, [0.8, 0.05, 0.05, 0.05, 0.05]))}}}


def test_normalization_and_raw_provenance():
    raw = raw_response()
    original = copy.deepcopy(raw)
    result = parse_decision_response(raw, QUESTIONS)
    assert result['answers']['support'] == {'kind': 'noul', 'value': 0.9}
    assert result['answers']['assertion'] == {
        'kind': 'choice', 'value': 'asserted', 'confidence': 0.8,
        'probabilities': raw['answers']['assertion']['probabilities']}
    assert result['raw'] == original
    assert result['model'] == 'fixture-model-v1'
    assert result['provider'] == 'fixture-provider'
    assert result['request_id'] == 'synthetic-request'
    result['raw']['answers']['support']['noul'] = 0
    assert raw == original


@pytest.mark.parametrize('value', [-0.01, 1.01, math.nan, math.inf, True, '0.9', None])
def test_support_invalid(value):
    raw = raw_response()
    raw['answers']['support']['noul'] = value
    with pytest.raises(ValueError):
        parse_decision_response(raw, QUESTIONS)


@pytest.mark.parametrize('mutation', ['missing_question', 'extra_question', 'missing_label',
    'extra_label', 'wrong_label', 'bad_sum', 'negative_probability', 'nan_probability',
    'boolean_probability', 'bad_confidence', 'missing_confidence', 'refusal'])
def test_malformed_response_never_becomes_support(mutation):
    raw = raw_response()
    answer = raw['answers']['assertion']
    if mutation == 'missing_question': del raw['answers']['support']
    elif mutation == 'extra_question': raw['answers']['other'] = {'noul': 0.5}
    elif mutation == 'missing_label': del answer['probabilities']['unclear']
    elif mutation == 'extra_label': answer['probabilities']['other'] = 0.0
    elif mutation == 'wrong_label': answer['choice'] = 'other'
    elif mutation == 'bad_sum': answer['probabilities']['asserted'] = 0.7
    elif mutation == 'negative_probability': answer['probabilities']['negated'] = -0.05
    elif mutation == 'nan_probability': answer['probabilities']['negated'] = math.nan
    elif mutation == 'boolean_probability': answer['probabilities']['negated'] = False
    elif mutation == 'bad_confidence': answer['confidence'] = 1.1
    elif mutation == 'missing_confidence': del answer['confidence']
    elif mutation == 'refusal': raw = {'error': 'refused', 'answers': {}}
    with pytest.raises(ValueError): parse_decision_response(raw, QUESTIONS)


def test_cache_order_invariance_and_version_invalidation():
    record = {'source_hash': 'abc', 'start': 1, 'end': 10, 'text': 'candidate',
              'subject': 'case1', 'object': 'case2', 'predicate': 'cites',
              'prompt_version': 'v1', 'schema_version': 'v1', 'parser_version': 'v1',
              'provider': 'fixture', 'model': 'fixture-v1'}
    key = cache_key(record)
    assert len(key) == 64
    assert key == cache_key(dict(reversed(list(record.items()))))
    for field in record:
        changed = dict(record)
        changed[field] = str(record[field]) + '-changed'
        assert cache_key(changed) != key
    with pytest.raises(ValueError): cache_key({'bad': math.nan})


def annotation(identifier, support, assertion):
    response = raw_response()
    response['answers']['support']['noul'] = support
    response['answers']['assertion']['choice'] = assertion
    return {'id': identifier, 'source_hash': 'synthetic', 'synthetic': True,
            'decision': parse_decision_response(response, QUESTIONS)}


def test_policy_retains_contradictions_in_original_and_only_filters_view():
    annotations = [annotation('positive', 0.9, 'asserted'),
                   annotation('negative', 0.9, 'negated'),
                   annotation('hypothesis', 0.9, 'hypothetical'),
                   annotation('prospective', 0.9, 'forward_looking'),
                   annotation('ambiguous', 0.9, 'unclear'),
                   annotation('low-support', 0.79, 'asserted'),
                   {'id': 'unavailable', 'decision': None}]
    original = copy.deepcopy(annotations)
    selected = filter_annotations(annotations)
    assert [a['id'] for a in selected] == ['positive']
    assert annotations == original
    assert selected == [a for a in annotations if a.get('decision') and
                        a['decision']['answers']['support']['value'] >= 0.8 and
                        a['decision']['answers']['assertion']['value'] == 'asserted']
    assert [a['id'] for a in filter_annotations(annotations, allowed_assertions=('negated',))] == ['negative']


@pytest.mark.parametrize('threshold', [-0.01, 1.01, math.nan, True])
def test_policy_invalid_threshold(threshold):
    with pytest.raises(ValueError): filter_annotations([], minimum_support=threshold)
