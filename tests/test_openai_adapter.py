"""The OpenAI-compatible live adapter, against a local fake server (no network, no real key)."""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from research_harness.workflows.adapters import (
    ModelError, ModelOutputTruncated, ModelRefusal, OpenAICompatibleAdapter, ScriptedModelAdapter, adapter_identity)

KEY = "sk-test-secret-key-0123456789"


class Fake:
    def __init__(self, script):
        self.script, self.requests, self.calls = list(script), [], 0
        outer = self

        class H(BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                outer.requests.append({"path": self.path, "auth": self.headers.get("Authorization"), "body": body})
                status, payload = outer.script[min(outer.calls, len(outer.script) - 1)]
                outer.calls += 1
                raw = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def log_message(self, *a):
                pass
        self.server = HTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()


def ok(content='{"records": []}', finish="stop", **usage):
    return 200, {"model": "fake-model", "choices": [{"message": {"content": content, "reasoning_content": "hidden thoughts"}, "finish_reason": finish}],
                 "usage": {"prompt_tokens": 100, "completion_tokens": 20, "prompt_cache_hit_tokens": 60, **usage}}


@pytest.fixture
def serve():
    servers = []

    def make(*script):
        f = Fake(script)
        servers.append(f)
        return f
    yield make
    for s in servers:
        s.close()


def adapter(fake, **kw):
    return OpenAICompatibleAdapter("fake-model", base_url=fake.url, api_key=KEY, backoff_seconds=0.01, **kw)


def test_a_request_and_the_usage_the_provider_reports(serve):
    f = serve(ok('{"records": [1]}'))
    a = adapter(f)
    text, usage = a.generate("SYSTEM", "USER", temperature=0.0)
    req = f.requests[0]
    assert text == '{"records": [1]}' and req["path"] == "/chat/completions" and req["auth"] == f"Bearer {KEY}"
    assert req["body"]["messages"] == [{"role": "system", "content": "SYSTEM"}, {"role": "user", "content": "USER"}]
    assert req["body"]["response_format"] == {"type": "json_object"} and "temperature" not in req["body"] and req["body"]["stream"] is False
    assert (usage.input_tokens, usage.output_tokens, usage.cache_read_tokens, usage.llm_calls, usage.usage_source) == (100, 20, 60, 1, "provider")
    assert "hidden thoughts" not in text                                             # reasoning text is not used


def test_the_key_never_appears_in_identity_repr_or_errors(serve):
    f = serve((401, {"error": {"message": f"bad key {KEY}"}}))
    a = adapter(f)
    ident = adapter_identity(a)
    assert KEY not in json.dumps(ident) and KEY not in repr(a) and ident["provider"] == "openai-compatible" and ident["model_id"] == "fake-model"
    with pytest.raises(ModelError) as e:
        a.generate("s", "u")
    assert KEY not in str(e.value) and "HTTP 401" in str(e.value) and f.calls == 1             # a client error is not retried


def test_transient_failures_are_retried_then_reported(serve):
    f = serve((429, {"error": "slow down"}), (503, {"error": "busy"}), ok())
    assert adapter(f).generate("s", "u")[0] == '{"records": []}' and f.calls == 3
    g = serve((500, {"error": "down"}))
    with pytest.raises(ModelError, match="after 3 attempt"):
        adapter(g).generate("s", "u")
    assert g.calls == 3


def test_truncation_filter_and_empty_answers_are_distinct_errors(serve):
    with pytest.raises(ModelOutputTruncated) as e:
        adapter(serve(ok('{"records": [', finish="length"))).generate("s", "u")
    assert e.value.usage.output_tokens == 20 and e.value.partial_output == '{"records": ['
    with pytest.raises(ModelRefusal):
        adapter(serve(ok("", finish="content_filter"))).generate("s", "u")
    with pytest.raises(ModelError, match="empty answer"):
        adapter(serve(ok("   "))).generate("s", "u")
    with pytest.raises(ModelError, match="no choices"):
        adapter(serve((200, {"choices": [], "usage": {}}))).generate("s", "u")


def test_nothing_is_defaulted(monkeypatch):
    for v in ("RESEARCH_MODEL", "RESEARCH_BASE_URL", "RESEARCH_API_KEY"):
        monkeypatch.delenv(v, raising=False)
    with pytest.raises(ValueError, match="model id"):
        OpenAICompatibleAdapter(base_url="http://x", api_key="k")
    with pytest.raises(ValueError, match="base URL"):
        OpenAICompatibleAdapter("m", api_key="k")
    with pytest.raises(ValueError, match="API key"):
        OpenAICompatibleAdapter("m", base_url="http://x")
    monkeypatch.setenv("RESEARCH_MODEL", "m"), monkeypatch.setenv("RESEARCH_BASE_URL", "http://x/"), monkeypatch.setenv("RESEARCH_API_KEY", "k")
    a = OpenAICompatibleAdapter()
    assert a.model_id == "m" and a.base_url == "http://x"


def test_it_drives_a_reading_protocol_end_to_end(serve, tmp_path):
    """The same path a real model takes: the protocol prompt goes out, the JSON answer comes back and every quote is verified."""
    from research_harness.knowledge import corpus
    from research_harness.state.store import StateStore
    from research_harness.workflows import reading_protocols as rp
    store, token = StateStore.create(tmp_path / "home", "PRJ_OA", owner_id="researcher")
    corpus.import_source(store, token, citation_key="s2026", text="Intro\n\n3 Method\nWe drop firms with fewer than three consecutive annual observations.\n")
    answer = json.dumps({"records": [{"field": "sample_selection", "value": "short panels dropped", "quote": "We drop firms with fewer than three consecutive annual observations"},
                                     {"field": "label_construction", "value": "invented", "quote": "a quote that is not in the text at all"}], "gaps": []})
    f = serve(ok(answer))
    out = rp.run_protocol(store, token, "SRC_S2026", "RP_METHOD", adapter(f))
    assert out["status"] == "EXTRACTED" and [r["field"] for r in out["records"]] == ["sample_selection"] and out["rejected"][0]["reason"] == "quote_not_in_text_shown"
    assert out["model"]["provider"] == "openai-compatible" and KEY not in json.dumps(out, default=str)
    assert "RP_METHOD" in f.requests[0]["body"]["messages"][0]["content"] and "drop firms" in f.requests[0]["body"]["messages"][1]["content"]
