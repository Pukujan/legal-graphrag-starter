"""Root-authored ranking contract. Citation popularity is not relevance."""
import pytest
from legal_graph.retrieval import graphrag_retrieve

NODES = [{'id': x} for x in ['seed', 'A', 'B', 'C', 'outside']]
EDGES = [
    {'source': 'A', 'target': 'seed', 'kind': 'cites'},
    {'source': 'B', 'target': 'seed', 'kind': 'cites'},
    {'source': 'C', 'target': 'seed', 'kind': 'cites'},
    {'source': 'outside', 'target': 'B', 'kind': 'cites'},
    {'source': 'seed', 'target': 'C', 'kind': 'part_of'},
]


def run(k, **kwargs):
    return graphrag_retrieve(['seed'], k, nodes=NODES, edges=EDGES,
                            depth=1, direction='incoming', **kwargs)


def test_global_prior_and_exact_provenance():
    output = run(3)
    assert [r['id'] for r in output['results']] == ['B', 'A', 'C']
    assert [r['score'] for r in output['results']] == [1, 0, 0]
    for row in output['results']:
        assert row['method'] == 'global_citation_in_degree'
        assert row['node'] == {'id': row['id']}
        assert row['provenance']['seed_id'] == 'seed'
        assert row['provenance']['dataset_revision'] == 'supplied'
        assert row['path'] == output['retrieval']['paths'][row['id']]


def test_fixed_candidates_k_prefix_and_duplicates():
    assert run(1)['results'] == run(3)['results'][:1]
    changed = graphrag_retrieve(['seed', 'seed'], 3, nodes=list(reversed(NODES)),
                               edges=list(reversed(EDGES)) + EDGES, direction='incoming')
    assert changed == run(3)


@pytest.mark.parametrize('k', [0, -1, 501, True, 1.5])
def test_invalid_k(k):
    with pytest.raises(ValueError): run(k)


def test_input_pairs_and_unsupported_mode():
    with pytest.raises(ValueError): graphrag_retrieve(['seed'], 1, nodes=NODES)
    with pytest.raises(ValueError): run(1, mode='imaginary')
