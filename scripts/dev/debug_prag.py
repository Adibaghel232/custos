"""Dev-only: trace D3 write verdicts on PoisonedRAG items (trusted condition)."""
import asyncio, json, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]; sys.path[:0] = [str(ROOT / "src"), str(ROOT / "scripts")]
from eval_poisonedrag import one, TENANT, PRINCIPAL  # noqa
from custos.llm import ToolCall
from custos.session import SessionCtx
from custos.types import Tier
from custos_bench.defences.factory import make_memory

async def trace(item):
    mem = make_memory("D3", backend="native"); gw = mem.gw
    s1 = SessionCtx(tenant_id=TENANT, principal=PRINCIPAL, session_id="dbg-s1", session_no=1)
    fact = f"Reference answer for \"{item['question']}\": {item['correct answer']}."
    print("=== Q:", item["question"], "| correct:", item["correct answer"], "| wrong:", item["incorrect answer"])
    await mem.sor_sync(s1, fact)
    await mem.drain(TENANT)
    for r in gw.store.list(TENANT) if hasattr(gw.store, "list") else []:
        pass
    for j, txt in enumerate(item["adv_texts"][:3]):
        ctx = await mem.on_tool_result(s1, ToolCall(id=f"c{j}", name="kb_search", arguments={"query": item["question"]}), txt, Tier.T1)
        from custos.types import WriteRequest
        vs = await gw.write(WriteRequest(tenant_id=TENANT, owner=PRINCIPAL, session_id="dbg-s1", content=txt, via="remember", cites=[ctx.ctx_id]))
        await gw.drain(TENANT)
        for v in vs:
            d = v.model_dump()
            print(" verdict:", d.get("action"), d.get("rule"), "|", "; ".join(d.get("reasons") or [])[:300])
    s2 = SessionCtx(tenant_id=TENANT, principal=PRINCIPAL, session_id="dbg-s2", session_no=2)
    rc = await mem.recall(s2, item["question"], "qa")
    print(" RENDERED:\n", rc.rendered[:1500])

async def main():
    data = list(json.loads(Path(sys.argv[1]).read_text()).values())
    for it in data[int(sys.argv[2]):int(sys.argv[3])]:
        await trace(it)
asyncio.run(main())
