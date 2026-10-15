"""Root-authored HTTP-to-engine differential contracts, before server code."""
import pytest
from fastapi.testclient import TestClient
from legal_graph.server import create_app
from legal_graph.retrieval import CitationGraphIndex, graphrag_retrieve

NODES = [{'id':'A','title':'Case A'}, {'id':'B'}, {'id':'C'}, {'id':'D'},
         {'id':'Case § A / 1','source_url':'https://example.invalid/synthetic'}]
EDGES = [{'source':'A','target':'B','kind':'cites'},
         {'source':'C','target':'B','kind':'cites'},
         {'source':'B','target':'D','kind':'part_of'}]
REVISION = 'synthetic-http-v1'


@pytest.fixture
def setup():
    index = CitationGraphIndex(NODES,EDGES,dataset_revision=REVISION,source_dataset='synthetic-contract')
    with TestClient(create_app(index=index,synthetic=True,allowed_origins=('http://localhost:5173',))) as client:
        yield client,index


def test_health_and_schema(setup):
    client,index = setup
    response = client.get('/api/v1/health')
    assert response.status_code == 200
    assert response.json()['status'] == 'ready'
    assert response.json()['graph'] == {'revision':REVISION,'nodes':5,'edges':3,
                                        'source_dataset':'synthetic-contract','synthetic':True}
    schema = client.get('/openapi.json').json()
    assert '/api/v1/neighborhood' in schema['paths']


def test_authority_exact_query_id_and_missing(setup):
    client,_ = setup
    response = client.get('/api/v1/authority',params={'id':'Case § A / 1'})
    assert response.status_code == 200
    assert response.json() == {'node':NODES[-1],'graph_revision':REVISION}
    assert client.get('/api/v1/authority',params={'id':'unknown'}).status_code == 404


def test_metadata_search(setup):
    client,_ = setup
    response = client.get('/api/v1/search',params={'q':'CASE','limit':2})
    assert response.status_code == 200
    assert response.json() == {'results':[NODES[0],NODES[-1]],'graph_revision':REVISION}
    assert client.get('/api/v1/search',params={'q':'not present'}).json()['results'] == []


@pytest.mark.parametrize('direction',['incoming','outgoing','both'])
def test_neighborhood_python_http_parity(setup,direction):
    client,index = setup
    body = {'seeds':['B','unknown'],'depth':2,'direction':direction,'edge_kinds':['cites','part_of']}
    response = client.post('/api/v1/neighborhood',json=body)
    assert response.status_code == 200
    expected = index.retrieve_workflow(**body)
    assert response.json() == dict(expected,graph_revision=REVISION)


def test_rank_route(setup):
    client,index = setup
    response = client.post('/api/v1/retrieve',json={'seeds':['B'],'k':2})
    assert response.status_code == 200
    expected = graphrag_retrieve(['B'],2,index=index)
    assert response.json() == dict(expected,graph_revision=REVISION)


@pytest.mark.parametrize('body',[{'depth':True},{'depth':4},{'max_nodes':501},
    {'max_edges':1001},{'max_scanned_edges':10001},{'seeds':'B'},
    {'direction':'sideways'},{'unknown_field':'ignored?'}])
def test_invalid_body_returns_422(setup,body):
    client,_ = setup
    assert client.post('/api/v1/neighborhood',json=dict({'seeds':['B']},**body)).status_code == 422


def test_no_fabricated_experiments(setup):
    client,_ = setup
    result = client.get('/api/v1/experiments').json()
    assert result == {'experiments':[],'graph_revision':REVISION}


def test_explicit_cors(setup):
    client,_ = setup
    headers = {'Origin':'http://localhost:5173','Access-Control-Request-Method':'POST'}
    allowed = client.options('/api/v1/neighborhood',headers=headers)
    assert allowed.status_code == 200
    assert allowed.headers['access-control-allow-origin'] == 'http://localhost:5173'
    denied = client.options('/api/v1/neighborhood',headers=dict(headers,Origin='https://unrelated.invalid'))
    assert denied.status_code == 400
