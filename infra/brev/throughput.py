"""Throughput probe for the self-hosted Lightning servers: N concurrent ~1.5k-token prompts, 150-token outputs,
plus one tool call and one JSON-schema call. Usage: CUSTOS_ROUTE_JUDGE=vllm CUSTOS_ROUTE_AGENT=vllm python infra/brev/throughput.py 512"""
import asyncio, sys, time
from pydantic import BaseModel
from custos.llm import NemotronClient

class NLI(BaseModel):
    label: str
    confidence: float

async def main(n: int):
    c = NemotronClient()
    filler = ("Vendor ledger note: invoices are reconciled weekly against the ERP vendor master. " * 60)[:6000]
    t = time.time()
    rs = await asyncio.gather(*[c.chat("judge", [{"role": "user", "content": f"{filler}\nRequest {i}: summarise the note in two sentences."}],
                                       max_tokens=150, cache=False) for i in range(n)], return_exceptions=True)
    dt = time.time() - t
    ok = [r for r in rs if not isinstance(r, Exception)]
    toks = sum(r.usage.get("completion_tokens", 0) for r in ok)
    print(f"{len(ok)}/{n} ok in {dt:.1f}s -> {len(ok)/dt:.1f} req/s, {toks/dt:.0f} out tok/s; errors: {[str(e)[:80] for e in rs if isinstance(e, Exception)][:2]}")
    r = await c.chat("judge", [{"role": "user", "content": "Premise: Acme's account is 4410. Hypothesis: Acme's account is 9921. entailment, contradiction or neutral?"}], schema=NLI, cache=False)
    print("json:", r.parsed)
    tools = [{"type": "function", "function": {"name": "pay_invoice", "parameters": {"type": "object", "properties": {"invoice_id": {"type": "string"}, "account": {"type": "string"}}, "required": ["invoice_id", "account"]}}}]
    r = await c.chat("agent", [{"role": "user", "content": "Pay invoice INV-0317 to account 4410."}], tools=tools, cache=False)
    print("tool:", [(t.name, t.arguments) for t in r.tool_calls])

asyncio.run(main(int(sys.argv[1]) if len(sys.argv) > 1 else 256))
