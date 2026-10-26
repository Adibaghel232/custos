from custos_bench.datasets import contradiction_set as cs
from custos_bench.datasets.longmemeval import DATA, judge_prompt, load_subset


def test_contradiction_set_shape_and_determinism():
    a, b = cs.build(), cs.build()
    assert [p.to_dict() for p in a] == [p.to_dict() for p in b]
    assert sum(p.split == "test" for p in a) == 300 and sum(p.split == "dev" for p in a) == 60
    assert {p.label for p in a if p.category == "non"} == {"compatible"}
    m = cs.f1(a, [p.label == "contradicts" for p in a])
    assert m["f1"] == 1.0


def test_longmemeval_subset():
    if not DATA.exists():
        return
    qs = load_subset()
    assert len(qs) == 178
    assert sum(q.question_type == "knowledge-update" for q in qs) == 72
    assert all(q.sessions for q in qs)
    assert load_subset()[5].question_id == qs[5].question_id
    p = judge_prompt("knowledge-update", "Q?", "A", "resp")
    assert "updated answer" in p and p.endswith("Answer yes or no only.")
