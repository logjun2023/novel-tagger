#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""todo_llm.py — 生成「待语义补标」批次文件（省 token：只发未标片段、批量、可截断）。

产物：<lib>/llm_inbox/batch_XX.json（片段ID + 本地标签 + 文本）+ PROMPT.md（打标协议）
流程：LLM（或会话中的你）读取批次 → 输出 batch_XX.result.json → apply_tags.py 合并回库。

用法：
  python todo_llm.py --lib taglib                       # 全部未标片段
  python todo_llm.py --lib taglib --book 某书 --chapter 3
  python todo_llm.py --lib taglib --dim plot            # 只挑缺 plot 标签的
  python todo_llm.py --lib taglib --max-batches 2       # 本次只生成 2 批（省着来）
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _common as C

DEFAULT_CFG = Path(__file__).resolve().parent.parent / "config"


def build_prompt(taxonomy: dict) -> str:
    dims = (taxonomy or {}).get("dimensions", {}) or {}
    lines = []
    lines.append("# LLM 语义补标协议（省 token 版）\n")
    lines.append("对每个片段，只输出「需要新增/删除」的标签，不要复述正文，不要输出解释文字。\n")
    lines.append("输出格式（每个 batch_XX.json 对应一个 batch_XX.result.json）：\n")
    lines.append("```json")
    lines.append("{")
    lines.append('  "片段ID": {"scene": ["对话"], "plot": ["恋爱", "吵架"], "confidence": 0.8, "note": "可选"}')
    lines.append("}")
    lines.append("```\n")
    lines.append("各维度可用值（尽量只用这些值；chars 维度用人物名/角色名；值前缀 - 表示删除该标签）：\n")
    for dim, spec in dims.items():
        methods = (spec or {}).get("method") or []
        if "llm" not in methods:
            continue  # 纯规则/词库/提炼的维度不需要 LLM 补
        vals = spec.get("values") or []
        vtxt = "、".join(vals) if vals else "（自由：人物名/角色标签）"
        lines.append(f"- {dim}（{spec.get('label', dim)}）：{vtxt}")
    lines.append("")
    lines.append("规则：")
    lines.append("1. local_tags 里已有的标签不要重复输出；")
    lines.append("2. 不确定就宁可不打；")
    lines.append("3. 一次处理一个批次文件，逐条给结果，不要漏条目；")
    lines.append("4. 输出仅 JSON（UTF-8），不要包 markdown 代码块之外的任何说明。")
    return "\n".join(lines)


def main():
    C.setup_console()
    ap = argparse.ArgumentParser(description="生成待语义补标的批次（省 token）")
    ap.add_argument("--lib", required=True)
    ap.add_argument("--book", default=None)
    ap.add_argument("--chapter", type=int, default=None)
    ap.add_argument("--batch-size", type=int, default=12)
    ap.add_argument("--max-chars", type=int, default=1200, help="单片段入批最大字符数（超出头尾截断）")
    ap.add_argument("--max-batches", type=int, default=0, help="本次最多生成几批（0=不限）")
    ap.add_argument("--dim", action="append", default=None, help="只挑缺少该维度的片段（可重复）")
    ap.add_argument("--redo", action="store_true", help="连同已标过的片段一起挑")
    ap.add_argument("--config-dir", default=None)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    lib = Path(args.lib)
    out = Path(args.out) if args.out else lib / "llm_inbox"
    out.mkdir(parents=True, exist_ok=True)
    cfg_dir = Path(args.config_dir) if args.config_dir else DEFAULT_CFG
    _, _, taxonomy, _ = C.load_libraries(cfg_dir, lib)

    dims = (taxonomy or {}).get("dimensions", {}) or {}
    llm_dims = [d for d, spec in dims.items() if "llm" in ((spec or {}).get("method") or [])]
    if not llm_dims:
        print("当前维度库没有需要 LLM 补标的维度（taxonomy 里所有维度的 method 都不含 llm）→ 无需生成批次")
        return

    rows = list(C.iter_jsonl(lib / "fragments.jsonl"))
    todo = []
    for r in rows:
        if args.book and args.book not in (r.get("book") or ""):
            continue
        if args.chapter is not None and r.get("chapter") != args.chapter:
            continue
        tags = r.get("tags") or {}
        if r.get("llm_done") and not args.redo:
            continue
        if args.dim and not any(d not in tags for d in args.dim):
            continue
        todo.append(r)

    if not todo:
        print("没有需要补标的片段（可加 --redo 强制重挑）")
        return

    batches = [todo[i:i + args.batch_size] for i in range(0, len(todo), args.batch_size)]
    if args.max_batches > 0:
        batches = batches[:args.max_batches]

    for k, chunk in enumerate(batches, 1):
        items = []
        for r in chunk:
            t = r.get("text") or ""
            if args.max_chars and len(t) > args.max_chars:
                head = int(args.max_chars * 0.7)
                tail = args.max_chars - head
                t = t[:head] + "\n……（中间省略）……\n" + t[-tail:]
            items.append({
                "id": r.get("id"),
                "book": r.get("book"),
                "chapter": r.get("chapter"),
                "chapter_title": r.get("chapter_title", ""),
                "local_tags": r.get("tags") or {},
                "text": t,
            })
        C.dump_json(out / f"batch_{k:02d}.json", {"batch": k, "count": len(items), "items": items})

    (out / "PROMPT.md").write_text(build_prompt(taxonomy), encoding="utf-8")
    print(f"共 {len(todo)} 个片段 → {len(batches)} 批，已写入 {out}")
    print("下一步（省 token 流程）：")
    print("  1) 逐个读取 batch_XX.json（只含未标片段；含正文——敏感内容被安全过滤拦截时跳过本批即可）")
    print("  2) 按 PROMPT.md 协议产出 batch_XX.result.json（只有标签，没有正文）")
    print(f"  3) python apply_tags.py --lib {args.lib} {out}/batch_01.result.json")


if __name__ == "__main__":
    main()
