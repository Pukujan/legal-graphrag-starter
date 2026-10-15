"""Root-authored transport tests: no real credential or external call."""
import json
import urllib.error
import pytest
from legal_graph.decisions import OpenRouterJevProvider, DecisionProviderError

PAYLOAD = {'model':'typesafe/jev-1.13','state':{'passage':'SYNTHETIC'},
           'questions':{'support':{'type':'noul','instructions':'Support?'}}}


class Response:
    def __init__(self, body, status=200): self.body, self.status = body, status
    def __enter__(self): return self
    def __exit__(self, *args): return False
    def read(self): return self.body


def test_endpoint_payload_and_auth():
    observed = []
    raw = {'answers':{'support':{'noul':0.5}}, 'id':'fixture'}
    def opener(request, timeout):
        observed.append((request,timeout))
        return Response(json.dumps(raw).encode())
    result = OpenRouterJevProvider(api_key='synthetic-test-key', timeout=7, opener=opener).decide(PAYLOAD)
    assert result == raw
    request, timeout = observed[0]
    assert request.full_url == 'https://openrouter.ai/api/alpha/decisions'
    assert request.get_method() == 'POST'
    assert json.loads(request.data) == PAYLOAD
    assert request.get_header('Authorization') == 'Bearer synthetic-test-key'
    assert request.get_header('Content-type') == 'application/json'
    assert timeout == 7


def test_missing_key_before_network(monkeypatch):
    monkeypatch.delenv('OPENROUTER_API_KEY', raising=False)
    calls = []
    with pytest.raises(ValueError):
        OpenRouterJevProvider(opener=lambda *a, **k: calls.append(a)).decide(PAYLOAD)
    assert not calls


@pytest.mark.parametrize('body,status', [(b'not json',200),(b'[]',200),
    (b'{}',200),(b'{"answers":null}',200),(b'{"answers":{}}',429)])
def test_transport_failure_is_explicit(body,status):
    provider = OpenRouterJevProvider(api_key='synthetic', opener=lambda *a, **k: Response(body,status))
    with pytest.raises(DecisionProviderError): provider.decide(PAYLOAD)


def test_http_error_is_explicit():
    def opener(request, timeout):
        raise urllib.error.HTTPError(request.full_url,429,'fixture rate limit',{},None)
    with pytest.raises(DecisionProviderError):
        OpenRouterJevProvider(api_key='synthetic',opener=opener).decide(PAYLOAD)
