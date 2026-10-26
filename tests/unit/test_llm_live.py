"""Live checks against the configured endpoint (run: pytest -m live tests/unit/test_llm_live.py)."""
import pytest
from pydantic import BaseModel

from custos.embed import NvidiaEmbedder
from custos.llm import NemotronClient

pytestmark = pytest.mark.live


class NLI(BaseModel):
    label: str
    confidence: float


TOOLS = [{"type": "function", "function": {
    "name": "pay_invoice", "description": "Pay a vendor invoice",
    "parameters": {"type": "object", "properties": {
        "invoice_id": {"type": "string"}, "account": {"type": "string"}, "amount_inr": {"type": "number"}},
        "required": ["invoice_id", "account", "amount_inr"]}}}]


async def test_live_json_schema():
    c = NemotronClient()
    r = await c.chat("judge", [{"role": "user", "content":
        "Premise: Acme's bank account is ****4410 (ERP). Hypothesis: Acme's bank account is ****9921. "
        "Label as entailment, contradiction or neutral."}], schema=NLI, max_tokens=100, cache=False)
    assert r.parsed.label.lower().startswith("contra"), r.text


async def test_live_tool_call():
    c = NemotronClient()
    r = await c.chat("agent", [{"role": "system", "content": "You are an accounts-payable assistant."},
                               {"role": "user", "content": "Pay invoice INV-0317 for ₹40,00,000 to account ****4410."}],
                     tools=TOOLS, max_tokens=300, cache=False)
    assert r.tool_calls and r.tool_calls[0].name == "pay_invoice", r.text
    assert "4410" in str(r.tool_calls[0].arguments)


async def test_live_embed():
    e = NvidiaEmbedder()
    v = await e.embed(["Acme bank account 4410", "Acme's bank account number is 4410", "monsoon rainfall"], "passage")
    assert v.shape[0] == 3 and float(v[0] @ v[1]) > float(v[0] @ v[2])
