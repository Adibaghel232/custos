"""Utility on LoCoMo (Maharana et al., ACL 2024; data/raw/locomo10.json): Custos on vs off, the second utility set promised
in round 1 next to LongMemEval. Gateway and agent code are unchanged.

Protocol (same for D0 and D3), mirroring src/custos_bench/harness/utility.py:
  ingest   each of the 10 conversations once per defence: every session in order; speaker A turns enter through
           memory.on_user_turn (T2, the agent's user), speaker B turns as assistant turns (T1); each turn is prefixed with
           the speaker name and the session's date; the agent's summariser + end_session write memory; snapshot cached.
  ask      every sampled question in a fresh memory restored from that snapshot, as a new session of the real agent.
  judge    LongMemEval rubric (temporal variant for category 2) on the evaluator role, as for LongMemEval.
Sample: categories 1-4 (adversarial category 5 skipped), 15 per conversation, seed 0 -> 150 questions.
Usage: python scripts/eval_locomo.py --out runs/external_locomo [--per-conv 15] [--limit N] [--concurrency 48]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import math
import random
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src")]

from custos.llm import get_llm  # noqa: E402
from custos.session import SessionCtx  # noqa: E402
from custos.types import ContextItem, Tier  # noqa: E402
from custos_bench import _c3  # noqa: E402
from custos_bench.datasets.longmemeval import judge_prompt  # noqa: E402
from custos_bench.defences.factory import make_memory  # noqa: E402
from custos_bench.pools import PRINCIPAL_A  # noqa: E402

DATA = ROOT / "data" / "raw" / "locomo10.json"
CATS = {1: "multi-hop", 2: "temporal", 3: "open-domain", 4: "single-hop"}
MAX_TURN_CHARS = 1500


def wilson(k: int, n: int, z: float = 1.96) -> list[float]:
    if n == 0:
        return [0.0, 0.0]
    p, d = k / n, 1 + z * z / n
    c, h = (p + z * z / (2 * n)) / d, z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return [max(0.0, c - h), min(1.0, c + h)]


def sessions_of(conv: dict) -> list[tuple[str, list[dict]]]:
    keys = sorted((k for k in conv if k.startswith("session_") and not k.endswith("date_time") and isinstance(conv[k], list)),
                  key=lambda k: int(k.split("_")[1]))
    return [(conv.get(f"{k}_date_time", ""), conv[k]) for k in keys]


def sample(data: list[dict], per_conv: int, seed: int = 0) -> list[dict]:
    out = []
    for x in data:
        qs = [q for q in x["qa"] if q.get("category") in CATS and q.get("answer") not in (None, "")]
        rng = random.Random(f"{seed}-{x['sample_id']}")
        for i, q in enumerate(sorted(rng.sample(qs, min(per_conv, len(qs))), key=lambda q: q["question"])):
            out.append({"qid": f"{x['sample_id']}-{i:02d}", "conv": x["sample_id"], "question": q["question"],
                        "answer": str(q["answer"]), "category": q["category"]})
    return out


async def ingest(memory, tenant: str, conv: dict, tag: str) -> int:
    from agent.summariser import summarise
    a, b = conv["speaker_a"], conv["speaker_b"]
    n = 0
    for si, (date, turns) in enumerate(sessions_of(conv), 1):
        s = SessionCtx(tenant_id=tenant, principal=PRINCIPAL_A, session_id=f"{tag}-i{si}", world_day=3 * (si - 1), session_no=si)
        items: list[ContextItem] = []
        for ti, t in enumerate(turns, 1):
            text = f"[{date}] {t['speaker']}: {t.get('text', '')}"[:MAX_TURN_CHARS]
            if t["speaker"] == a:
                items.append(await memory.on_user_turn(s, text))
            else:
                items.append(ContextItem(ctx_id=f"c{si}_a{ti}", session_id=s.session_id, kind="assistant_turn", tier=Tier.T1, text=text))
        for i, c in enumerate(items):
            if c.kind == "assistant_turn":
                c.ctx_id = f"c{si}_a{i}"
        summ = await summarise(items)
        await memory.end_session(s, items, summ)
        n += 1
    await memory.drain(tenant)
    return n


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="runs/external_locomo")
    ap.add_argument("--per-conv", type=int, default=15)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--defences", default="D0,D3")
    ap.add_argument("--concurrency", type=int, default=48)
    a = ap.parse_args()
    if _c3.run_episode is None:
        _c3.refresh()
    data = json.loads(DATA.read_text(encoding="utf-8"))
    convs = {x["sample_id"]: x["conversation"] for x in data}
    qs = sample(data, a.per_conv)
    if a.limit:
        qs = qs[: a.limit]
    defs = a.defences.split(",")
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    fp = out / "results.jsonl"
    done = set()
    if fp.exists():
        for line in fp.read_text(encoding="utf-8").splitlines():
            r = json.loads(line)
            if not r.get("error"):
                done.add((r["qid"], r["defence"]))
    jobs = [(q, d) for q in qs for d in defs if (q["qid"], d) not in done]
    need = sorted({(q["conv"], d) for q, d in jobs})
    print(f"{len(jobs)} questions to ask, {len(need)} conversation ingestions needed", flush=True)
    sem = asyncio.Semaphore(a.concurrency)
    snaps: dict[tuple[str, str], bytes] = {}

    async def build(cid: str, d: str) -> None:
        p = out / f"snap_{cid}_{d}.bin"
        if p.exists():
            snaps[(cid, d)] = p.read_bytes()
            return
        t0 = time.perf_counter()
        async with sem:
            mem = make_memory(d, backend="primary")
            n = await ingest(mem, f"loc-{cid}", convs[cid], f"loc-{cid}-{d}")
            blob = await mem.snapshot()
        p.write_bytes(blob)
        snaps[(cid, d)] = blob
        print(f"ingested {cid} {d}: {n} sessions in {time.perf_counter() - t0:.0f}s", flush=True)

    await asyncio.gather(*(build(c, d) for c, d in need))
    cnt = [0]

    async def ask(q: dict, d: str) -> None:
        t0 = time.perf_counter()
        rec = {"qid": q["qid"], "conv": q["conv"], "defence": d, "category": q["category"], "question": q["question"], "answer": q["answer"]}
        async with sem:
            try:
                mem = make_memory(d, backend="primary")
                await mem.restore(snaps[(q["conv"], d)])
                tenant = f"loc-{q['conv']}"
                world = _c3.FinanceWorld(tenant, seed=0)
                spec = _c3.SessionSpec(label="question", user_turns=[q["question"]])
                run = await _c3.run_episode(world, mem, [spec], tenant_id=tenant, principal=PRINCIPAL_A,
                                            episode_id=f"locq-{q['qid']}-{d}", user_policy="cautious", temperature=0.0)
                resp = run.sessions[-1].final_text if run.sessions else ""
                qtype = "temporal-reasoning" if q["category"] == 2 else "multi-session"
                r = await get_llm().chat("evaluator", [{"role": "user", "content": judge_prompt(qtype, q["question"], q["answer"], resp)}],
                                         temperature=0.0, max_tokens=10)
                rec.update(response=resp[:500], correct="yes" in (r.text or "").lower())
            except Exception as e:  # noqa: BLE001 - recorded and retried on resume
                rec["error"] = f"{type(e).__name__}: {e}"[:300]
        rec["latency_s"] = round(time.perf_counter() - t0, 1)
        with fp.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec) + "\n")
        cnt[0] += 1
        if cnt[0] % 25 == 0:
            print(f"{cnt[0]}/{len(jobs)} asked", flush=True)

    await asyncio.gather(*(ask(q, d) for q, d in jobs))
    summarise_results(fp, out / "summary.json")


def summarise_results(fp: Path, dest: Path) -> None:
    rows = {}
    for line in fp.read_text(encoding="utf-8").splitlines():
        r = json.loads(line)
        if not r.get("error"):
            rows[(r["qid"], r["defence"])] = r
    summ: dict = {"dataset": "LoCoMo (locomo10), categories 1-4, 15 questions per conversation, seed 0",
                  "judge": "LongMemEval rubric on the evaluator role", "defences": {}}
    for d in sorted({k[1] for k in rows}):
        rs = [r for k, r in rows.items() if k[1] == d]
        k = sum(r["correct"] for r in rs)
        cats = {}
        for c, name in CATS.items():
            cr = [r for r in rs if r["category"] == c]
            if cr:
                ck = sum(r["correct"] for r in cr)
                cats[name] = {"n": len(cr), "k": ck, "acc": ck / len(cr)}
        summ["defences"][d] = {"n": len(rs), "k": k, "acc": k / max(len(rs), 1), "ci95": wilson(k, len(rs)), "by_category": cats}
    both = [q for (q, d) in rows if d == "D0" and (q, "D3") in rows]
    if both:
        delta = (sum(rows[(q, "D3")]["correct"] for q in both) - sum(rows[(q, "D0")]["correct"] for q in both)) / len(both)
        summ["paired"] = {"n": len(both), "delta_d3_minus_d0": delta}
    dest.write_text(json.dumps(summ, indent=1), encoding="utf-8")
    for d, v in summ["defences"].items():
        print(f"{d}: {v['k']}/{v['n']} = {v['acc']:.1%} [{v['ci95'][0]:.1%}, {v['ci95'][1]:.1%}]  "
              + "  ".join(f"{c} {x['k']}/{x['n']}" for c, x in v["by_category"].items()))
    if "paired" in summ:
        print(f"paired delta D3-D0: {100 * summ['paired']['delta_d3_minus_d0']:+.1f} pts (n={summ['paired']['n']})")


if __name__ == "__main__":
    asyncio.run(main())
