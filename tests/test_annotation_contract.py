"""Root-authored span/link/overlay tests written before enrichment implementation."""
import copy
import networkx as nx
import pytest
from legal_graph.extraction import validate_span, link_authority
from legal_graph.annotations import build_annotation_overlay, retrieve_annotated
from legal_graph.decisions import filter_annotations


def test_exact_span_offsets():
    validate_span('§ A cites B.', {'start':0,'end':3,'text':'§ A'})
    for bad in [{'start':True,'end':3,'text':'§ A'}, {'start':0,'end':30,'text':'§ A'},
                {'start':0,'end':3,'text':'A'}, {'start':2,'end':2,'text':''},
                {'start':-1,'end':3,'text':'§ A'}]:
        with pytest.raises(ValueError): validate_span('§ A cites B.', bad)


def test_linker_unambiguous_only_and_preserves_ids():
    nodes = [{'id':'Case § A','title':'Authority Alpha'}, {'id':'B','title':'Authority Beta'}]
    assert link_authority(' authority   ALPHA ',nodes) == 'Case § A'
    assert link_authority('Case § A',nodes) == 'Case § A'
    assert link_authority('Authority',nodes) is None
    assert link_authority('invented',nodes) is None
    assert link_authority('case a',[{'id':'Case A'},{'id':'Case  A'}]) is None
    assert link_authority('Authority Alpha',nodes + [{'id':'C','title':'Authority Alpha'}]) is None


def records():
    def make(identifier, support, assertion, subject='A', obj='C'):
        return {'id':identifier,'source_id':'synthetic-passage','subject_id':subject,'object_id':obj,
                'decision':{'answers':{'support':{'kind':'noul','value':support},
                                       'assertion':{'kind':'choice','value':assertion}}},
                'synthetic':True}
    return [make('supported',0.9,'asserted'),make('denied',0.9,'negated',obj='D'),
            make('hypothesis',0.9,'hypothetical',obj='D'),make('low',0.2,'asserted',obj='D'),
            make('unlinked',0.9,'asserted',obj='unknown')]


def graph():
    g = nx.MultiDiGraph()
    g.add_nodes_from(['A','B','C','D'])
    g.nodes['A']['metadata'] = {'original':True}
    g.add_edge('A','B',key='citation',kind='cites',source_locator='original')
    g.add_edge('A','B',key='hierarchy',kind='part_of')
    return g


def test_overlay_preserves_original_and_all_judgments():
    g, annotations = graph(), records()
    before = copy.deepcopy((g,annotations))
    overlay = build_annotation_overlay(g,annotations)
    assert dict(g.nodes(data=True)) == dict(before[0].nodes(data=True))
    assert list(g.edges(keys=True,data=True)) == list(before[0].edges(keys=True,data=True))
    assert annotations == before[1]
    for s,t,key,attrs in g.edges(keys=True,data=True):
        assert overlay[s][t][key] == attrs
    for a in annotations:
        assert overlay.nodes['annotation:'+a['id']]['annotation'] == a
    assert 'unknown' not in overlay
    assert overlay.nodes['source:synthetic-passage']['kind'] == 'source'


@pytest.mark.parametrize('direction,seeds,expected', [
    ('outgoing',['A'],{'A','B','C'}), ('incoming',['C'],{'A','C'}),
    ('both',['B'],{'A','B'})])
def test_overlay_filter_differential(direction,seeds,expected):
    all_rows = records()
    full = build_annotation_overlay(graph(),all_rows)
    gated = build_annotation_overlay(graph(),filter_annotations(all_rows))
    assert retrieve_annotated(full,seeds,hops=1,direction=direction) == expected
    assert retrieve_annotated(full,seeds,hops=1,direction=direction) == retrieve_annotated(gated,seeds,hops=1,direction=direction)
    assert len([n for n in full if str(n).startswith('annotation:')]) == len(all_rows)


def test_namespace_collision_fails_explicitly():
    g = graph()
    g.add_node('annotation:supported')
    with pytest.raises(ValueError): build_annotation_overlay(g,records())


@pytest.mark.parametrize('relationship,eligible', [
    ('none',False), ('insufficient',False), ('unclear',False),
    ('overrules',False), ('follows',True)])
def test_optional_relationship_gate(relationship,eligible):
    row = records()[0]
    row['predicate'] = 'follows'
    row['decision']['answers']['relationship'] = {'kind':'choice','value':relationship}
    original = copy.deepcopy(row)
    selected = filter_annotations([row])
    assert bool(selected) is eligible
    overlay = build_annotation_overlay(graph(),[row])
    reached = retrieve_annotated(overlay,['A'],hops=1,direction='outgoing')
    assert reached == ({'A','B','C'} if eligible else {'A','B'})
    assert overlay.nodes['annotation:supported']['annotation'] == original
    assert row == original
