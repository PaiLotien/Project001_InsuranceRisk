"""评测：python scripts/run_advisor_eval.py [--ids Q01,Q02 | --n 10] [--cache] [--label 名称]

→ eval/results_<时间>.jsonl + 汇总。默认关闭答案缓存以测真实推理时延；--cache 用于测缓存命中。
“正确”= 要点全覆盖 + JSON 合法 + 拒答判断正确 + 无无法溯源的数字 + 无无效引用。
"""

from __future__ import annotations
import argparse, json, re, sys, time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from src.advisor import rag as rc
from src.advisor.context import hypothesis_context
from src.advisor.service import ask_advisor


USE_CACHE = False


def ask(question, **kwargs):
    kwargs.pop("index", None)
    return ask_advisor(question, use_cache=USE_CACHE, **kwargs)


def pct(values, q):
    values = sorted(values)
    if not values:
        return None
    pos = (len(values) - 1) * q
    lo, hi = int(pos), min(int(pos) + 1, len(values) - 1)
    return round(values[lo] + (values[hi] - values[lo]) * (pos - lo), 2)


NUM = re.compile(r"\d+(?:\.\d+)?")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ids")
    ap.add_argument("--n", type=int)
    ap.add_argument("--cache", action="store_true")
    ap.add_argument("--label", default="")
    a = ap.parse_args()
    global USE_CACHE
    USE_CACHE = a.cache
    audit = rc.enforce_local_only()
    items = [json.loads(l) for l in (ROOT / "eval/advisor_eval_set.jsonl").read_text(encoding="utf-8").splitlines()]
    if a.ids:
        items = [i for i in items if i["id"] in a.ids.split(",")]
    if a.n:
        items = items[: a.n]
    idx = rc.KnowledgeIndex()
    out = ROOT / f"eval/results_{time.strftime('%Y%m%d_%H%M')}{('_' + a.label) if a.label else ''}.jsonl"
    rows = []
    for it in items:
        ctx = json.loads((ROOT / it["context"]).read_text(encoding="utf-8")) if it["context"] else None
        r = ask(it["question"], context=ctx, json_mode=it["json"], index=idx)
        ans = r["raw"]
        if it["json"] and "json" in r:
            j = r["json"]
            ans_text = (
                j.get("answer", "")
                + " "
                + " ".join(e.get("claim", "") for e in j.get("evidence", []))
                + " "
                + " ".join(j.get("suggestions", []) + j.get("uncertainty", []))
                + " "
                + " ".join(str(e.get("source", "")) for e in j.get("evidence", []))
            )
        else:
            ans_text = ans
        hit_text = " ".join(h["source"] for h in r["hits"])
        src_hit = (it["expect_source"] in hit_text) if it["expect_source"] else None
        kw = [k for k in it["keywords"] if k.lower() in ans_text.lower()]
        # 数字溯源：答案中的数字是否都出现在检索片段/上下文/问题里
        # 与线上一致：假设分析由 Python 算好后放进系统结果，可被引用
        sys_ctx = hypothesis_context(it["question"], ctx)
        evidence_text = json.dumps(sys_ctx, ensure_ascii=False) if sys_ctx else ""
        evidence_text += " " + " ".join(f["label"] + f["value"] for f in r.get("lookup", []))
        chunks = {c["source"] + c["section"] + c["text"] for c in idx.chunks}
        pool = (
            evidence_text
            + it["question"]
            + " ".join(
                c["text"]
                for c in idx.chunks
                if any(c["source"] == h["source"] and c["section"] == h["section"] for h in r["hits"])
            )
        )
        nums = [n for n in set(NUM.findall(ans_text)) if not re.fullmatch(r"[0-9]", n) and not re.fullmatch(r"K?\d", n)]
        untraced = [n for n in nums if n not in pool]
        refused = (r.get("json") or {}).get("refused") if it["json"] else None
        row = {
            "id": it["id"],
            "type": it["type"],
            "question": it["question"],
            "json_ok": ("json" in r) if it["json"] else None,
            "retrieval_hit": src_hit,
            "keywords_hit": f"{len(kw)}/{len(it['keywords'])}",
            "kw_full": len(kw) == len(it["keywords"]),
            "refused": refused,
            "refusal_ok": (refused == it["should_refuse"]) if it["json"] else None,
            "invalid_citations": r["invalid_citations"],
            "lookup": r.get("lookup", []),
            "lookup_mismatch": r.get("lookup_mismatch", []),
            "has_citation": bool(r["citations"]),
            "untraced_numbers": untraced,
            "correct": None,
            "cached": r.get("cached", False),
            "error": r.get("error"),
            "timing": r["timing"],
            "answer": ans,
            "hits": r["hits"],
        }
        row["correct"] = bool(
            row["kw_full"]
            and row["json_ok"] is not False
            and row["refusal_ok"] is not False
            and not untraced
            and not r["invalid_citations"]
            and not row["error"]
        )
        rows.append(row)
        with out.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
        print(
            f"{it['id']} ok={row['correct']} kw={row['keywords_hit']} ret={src_hit} json={row['json_ok']} refuse_ok={row['refusal_ok']} "
            f"cite={row['has_citation']} bad_cite={row['invalid_citations']} untraced={untraced} t={r['timing']}",
            flush=True,
        )

    def rate(key):
        v = [r[key] for r in rows if r[key] is not None]
        return f"{sum(bool(x) for x in v)}/{len(v)}"

    tt = sorted(r["timing"]["total_s"] for r in rows)
    ft = sorted(r["timing"].get("ttft_s", r["timing"]["total_s"]) for r in rows)
    old = [r for r in rows if int(r["id"][1:]) <= 24]
    new = [r for r in rows if int(r["id"][1:]) > 24]
    ext = [h for h in audit if h not in ("127.0.0.1", "::1", "localhost")]
    summ = {
        "n": len(rows),
        "retrieval_hit@5": rate("retrieval_hit"),
        "keywords_full": rate("kw_full"),
        "json_valid": rate("json_ok"),
        "refusal_correct": rate("refusal_ok"),
        "with_citation": rate("has_citation"),
        "invalid_citation_answers": sum(bool(r["invalid_citations"]) for r in rows),
        "answers_with_untraced_numbers": sum(bool(r["untraced_numbers"]) for r in rows),
        "correct": rate("correct"),
        "correct_old_Q01_Q24": f"{sum(r['correct'] for r in old)}/{len(old)}",
        "correct_new_Q25plus": f"{sum(r['correct'] for r in new)}/{len(new)}",
        "lookup_used": sum(bool(r["lookup"]) for r in rows),
        "lookup_mismatch_answers": sum(bool(r["lookup_mismatch"]) for r in rows),
        "cached_answers": sum(bool(r["cached"]) for r in rows),
        "ttft_median_s": pct(ft, 0.5),
        "total_median_s": pct(tt, 0.5),
        "total_p90_s": pct(tt, 0.9),
        "total_max_s": tt[-1],
        "output_tokens_median": pct([r["timing"].get("output_tokens", 0) for r in rows], 0.5),
        "prompt_tokens_median": pct([r["timing"].get("prompt_tokens", 0) for r in rows], 0.5),
        "external_connections": len(ext),
    }
    print(json.dumps(summ, ensure_ascii=False, indent=1))
    (out.with_suffix(".summary.json")).write_text(json.dumps(summ, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
