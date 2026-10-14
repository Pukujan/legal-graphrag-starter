"""Root-authored existing-Octo consumer contracts; no DB provisioning."""
import json
import pytest
from legal_graph.storage import GraphSnapshotStore, StorageError

SNAPSHOT = '11111111-1111-4111-8111-111111111111'


class Executor:
    def __init__(self, rows, **overrides):
        self.rows, self.overrides, self.calls = rows, overrides, []
    def __call__(self, sql, params, row_limit):
        self.calls.append((sql,list(params),row_limit))
        return dict({'columns':list(self.rows[0]) if self.rows else [], 'rows':self.rows,
                     'rowCount':len(self.rows),'statementCount':1,'truncated':False}, **self.overrides)


def test_snapshot_metadata_and_empty():
    row = {'snapshot_id':SNAPSHOT,'source_digest':'synthetic-digest'}
    assert GraphSnapshotStore(Executor([row])).get_active_snapshot() == row
    assert GraphSnapshotStore(Executor([])).get_active_snapshot() is None
    with pytest.raises(StorageError): GraphSnapshotStore(Executor([row,row])).get_active_snapshot()


def test_authority_page_pins_snapshot_and_keeps_exact_payload():
    rows = [{'authority_id':'A','payload':{'id':'A','source':'synthetic'}},
            {'authority_id':'Case § B','payload':json.dumps({'id':'Case § B'})},
            {'authority_id':'Z','payload':{'id':'Z'}}]
    execute = Executor(rows)
    page = GraphSnapshotStore(execute).get_authorities(SNAPSHOT, limit=2)
    assert page == {'items':[{'id':'A','source':'synthetic'},{'id':'Case § B'}], 'next_cursor':'Case § B'}
    assert SNAPSHOT in execute.calls[0][1]
    assert execute.calls[0][2] >= 3


def test_edge_page_preserves_original_attributes():
    edge = {'source':'§ A','target':'B','kind':'cites','locator':'synthetic:17'}
    execute = Executor([{'edge_key':'001','payload':edge}])
    assert GraphSnapshotStore(execute).get_citation_edges(SNAPSHOT,limit=2) == {'items':[edge],'next_cursor':None}
    assert SNAPSHOT in execute.calls[0][1]


def test_identifiers_are_bound_and_not_sql_interpolated():
    identifier = "case'; DROP TABLE legal_graph.authorities; --"
    execute = Executor([{'payload':{'id':identifier}}])
    assert GraphSnapshotStore(execute).get_authority(identifier,SNAPSHOT) == {'id':identifier}
    sql, params, _ = execute.calls[0]
    assert identifier not in sql
    assert identifier in params and SNAPSHOT in params
    cursor = "cursor' OR true --"
    execute = Executor([])
    GraphSnapshotStore(execute).get_authorities(SNAPSHOT,cursor=cursor,limit=2)
    assert cursor not in execute.calls[0][0] and cursor in execute.calls[0][1]


@pytest.mark.parametrize('overrides', [{'statementCount':2},{'truncated':True},{'error':'SQL_ERROR'}])
def test_incomplete_or_multiple_statement_results_fail(overrides):
    with pytest.raises(StorageError): GraphSnapshotStore(Executor([],**overrides)).get_authorities(SNAPSHOT)


@pytest.mark.parametrize('limit', [0,-1,1001,True,1.5])
def test_invalid_limits_before_transport(limit):
    execute = Executor([])
    with pytest.raises(ValueError): GraphSnapshotStore(execute).get_authorities(SNAPSHOT,limit=limit)
    assert not execute.calls


def test_invalid_snapshot_before_transport():
    execute = Executor([])
    with pytest.raises(ValueError): GraphSnapshotStore(execute).get_authorities('not-a-uuid')
    assert not execute.calls


def test_malformed_payload_fails():
    with pytest.raises(StorageError):
        GraphSnapshotStore(Executor([{'authority_id':'A','payload':'not-json'}])).get_authorities(SNAPSHOT)
