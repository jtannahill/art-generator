"""Tests for the art-trigger Lambda gate."""
import json

import lambdas.trigger.handler as h


def _event(body=None, qs=None):
    return {"body": json.dumps(body) if body else None, "queryStringParameters": qs,
            "requestContext": {"http": {"sourceIp": "1.2.3.4"}}}


def test_rejects_without_token(monkeypatch):
    monkeypatch.setattr(h, "TURNSTILE_SECRET_KEY", "s")
    started = []
    monkeypatch.setattr(h.boto3, "client", lambda *a, **k: started.append(1))
    resp = h.handler(_event(qs={"artist": "sam_francis"}), None)
    assert resp["statusCode"] == 403 and started == []


def test_rejects_when_secret_unset(monkeypatch):
    monkeypatch.setattr(h, "TURNSTILE_SECRET_KEY", "")
    resp = h.handler(_event(body={"token": "x"}), None)
    assert resp["statusCode"] == 403


def test_rejects_bad_artist(monkeypatch):
    monkeypatch.setattr(h, "TURNSTILE_SECRET_KEY", "s")
    monkeypatch.setattr(h, "verify_turnstile", lambda t, ip: True)
    resp = h.handler(_event(body={"token": "x", "artist": "../x; drop"}), None)
    assert resp["statusCode"] == 400


def test_starts_with_valid_token(monkeypatch):
    monkeypatch.setattr(h, "TURNSTILE_SECRET_KEY", "s")
    monkeypatch.setattr(h, "verify_turnstile", lambda t, ip: t == "good")

    class FakeSfn:
        def list_executions(self, **k):
            return {"executions": []}

        def start_execution(self, **k):
            assert json.loads(k["input"]) == {"artist": "mark_rothko"}
            return {"executionArn": "arn:x"}

    monkeypatch.setattr(h.boto3, "client", lambda *a, **k: FakeSfn())
    resp = h.handler(_event(body={"token": "good", "artist": "mark_rothko"}), None)
    assert resp["statusCode"] == 200 and json.loads(resp["body"])["status"] == "started"


class _Resp:
    def __init__(self, payload):
        self._b = json.dumps(payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return self._b


def _siteverify(monkeypatch, payload):
    monkeypatch.setattr(h, "TURNSTILE_SECRET_KEY", "s")
    monkeypatch.setattr(h.urllib.request, "urlopen", lambda req, timeout=10: _Resp(payload))


def test_verify_accepts_expected_action_and_hostname(monkeypatch):
    _siteverify(monkeypatch, {"success": True, "action": "generate", "hostname": "art.jamestannahill.com"})
    assert h.verify_turnstile("tok", "1.2.3.4") is True


def test_verify_rejects_wrong_action(monkeypatch):
    _siteverify(monkeypatch, {"success": True, "action": "contact", "hostname": "art.jamestannahill.com"})
    assert h.verify_turnstile("tok", "1.2.3.4") is False


def test_verify_rejects_unapproved_hostname(monkeypatch):
    _siteverify(monkeypatch, {"success": True, "action": "generate", "hostname": "localhost"})
    assert h.verify_turnstile("tok", "1.2.3.4") is False


def test_verify_rejects_failure_and_oversized_token(monkeypatch):
    _siteverify(monkeypatch, {"success": False, "action": "generate", "hostname": "art.jamestannahill.com"})
    assert h.verify_turnstile("tok", "1.2.3.4") is False
    assert h.verify_turnstile("x" * 2049, "1.2.3.4") is False
