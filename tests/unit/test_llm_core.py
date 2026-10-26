import pytest
from pydantic import BaseModel

from custos.compliance import ComplianceError, check
from custos.embed import HashEmbedder
from custos.llm import FakeLLM, ToolCall, parse_json_loose, usage_scope
from custos.settings import get_settings


class Out(BaseModel):
    label: str
    conf: float


async def test_fake_llm_schema_and_usage():
    llm = FakeLLM({"judge": lambda m, t, s: {"label": "contradicts", "conf": 0.9}})
    with usage_scope() as u:
        r = await llm.chat("judge", [{"role": "user", "content": "x"}], schema=Out)
    assert isinstance(r.parsed, Out) and r.parsed.label == "contradicts"
    assert u.calls["judge"] == 1


async def test_fake_llm_tool_calls():
    llm = FakeLLM({"agent": lambda m, t, s: [ToolCall("c1", "pay_invoice", {"vendor": "acme"})]})
    r = await llm.chat("agent", [{"role": "user", "content": "pay"}], tools=[{"type": "function"}])
    assert r.tool_calls[0].name == "pay_invoice" and r.tool_calls[0].arguments["vendor"] == "acme"


def test_parse_json_loose():
    assert parse_json_loose('<think>hmm</think>```json\n{"a": 1}\n```') == {"a": 1}
    assert parse_json_loose('Sure! Here it is: {"a": [1, 2]} hope that helps') == {"a": [1, 2]}


async def test_hash_embedder_similarity():
    e = HashEmbedder()
    v = await e.embed(["Acme bank account is 1234", "Acme's bank account number is 1234", "the weather is sunny"])
    assert v.shape == (3, 384)
    assert float(v[0] @ v[1]) > float(v[0] @ v[2])


def test_compliance_rejects_non_nvidia():
    s = get_settings()
    check(s)  # repo config must pass
    bad = type(s).__new__(type(s))
    bad.__dict__.update(s.__dict__)
    bad.models = {**s.models, "agent": "openai/gpt-4o"}
    with pytest.raises(ComplianceError):
        check(bad)
