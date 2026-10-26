"""LongMemEval utility subset (SPEC §17.1): 178 questions = knowledge-update 72 (all non-abstention) + multi-session 66 +
temporal-reasoning 40 (seeded sample), from longmemeval_oracle.json (MIT, xiaowu0162/longmemeval-cleaned).
Judge = the official LongMemEval rubric (evaluate_qa.py), run on Nemotron 3 Super (role "evaluator") instead of GPT-4o.
"""
from __future__ import annotations

import json
import random
from dataclasses import dataclass, field
from pathlib import Path

from custos.llm import get_llm
from custos.settings import ROOT

DATA = ROOT / "data" / "raw" / "longmemeval_oracle.json"
QUOTAS = {"knowledge-update": 72, "multi-session": 66, "temporal-reasoning": 40}


@dataclass
class LMEQuestion:
    question_id: str
    question_type: str
    question: str
    answer: str
    question_date: str
    sessions: list[list[dict]] = field(default_factory=list)   # each turn: {"role","content"}
    session_dates: list[str] = field(default_factory=list)


def load_subset(seed: int = 0, path: Path = DATA) -> list[LMEQuestion]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    by_type: dict[str, list[dict]] = {}
    for q in data:
        if q["question_id"].endswith("_abs"):          # abstention questions are out of scope
            continue
        by_type.setdefault(q["question_type"], []).append(q)
    rng = random.Random(seed)
    out: list[LMEQuestion] = []
    for qtype, n in QUOTAS.items():
        pool = sorted(by_type.get(qtype, []), key=lambda q: q["question_id"])
        picked = pool if len(pool) <= n else rng.sample(pool, n)
        for q in sorted(picked, key=lambda q: q["question_id"]):
            order = sorted(range(len(q["haystack_sessions"])), key=lambda i: q["haystack_dates"][i])
            out.append(LMEQuestion(
                question_id=q["question_id"], question_type=qtype, question=q["question"], answer=str(q["answer"]),
                question_date=q["question_date"],
                sessions=[[{"role": t["role"], "content": t["content"]} for t in q["haystack_sessions"][i]]
                          for i in order],
                session_dates=[q["haystack_dates"][i] for i in order]))
    return out


_BASE = ("I will give you a question, a correct answer, and a response from a model. Please answer yes if the response "
         "contains the correct answer. Otherwise, answer no. If the response is equivalent to the correct answer or "
         "contains all the intermediate steps to get the correct answer, you should also answer yes. If the response "
         "only contains a subset of the information required by the answer, answer no. ")
_TEMPORAL = ("In addition, do not penalize off-by-one errors for the number of days. If the question asks for the number "
             "of days/weeks/months, etc., and the model makes off-by-one errors (e.g., predicting 19 days when the answer "
             "is 18), the model's response is still correct. ")
_KU = ("I will give you a question, a correct answer, and a response from a model. Please answer yes if the response "
       "contains the correct answer. Otherwise, answer no. If the response contains some previous information along "
       "with an updated answer, the response should be considered as correct as long as the updated answer is the "
       "required answer.")
_TAIL = "\n\nQuestion: {}\n\nCorrect Answer: {}\n\nModel Response: {}\n\nIs the model response correct? Answer yes or no only."


def judge_prompt(qtype: str, question: str, answer: str, response: str) -> str:
    """Verbatim LongMemEval rubric (evaluate_qa.get_anscheck_prompt, non-abstention)."""
    if qtype == "knowledge-update":
        head = _KU
    elif qtype == "temporal-reasoning":
        head = _BASE + _TEMPORAL
    else:
        head = _BASE.rstrip()
    return head.rstrip() + _TAIL.format(question, answer, response)


async def judge(q: LMEQuestion, response: str) -> bool:
    """Official protocol: temperature 0, max 10 tokens, correct iff 'yes' in the reply."""
    r = await get_llm().chat("evaluator", [{"role": "user", "content": judge_prompt(
        q.question_type, q.question, q.answer, response)}], temperature=0.0, max_tokens=10)
    return "yes" in r.text.lower()
