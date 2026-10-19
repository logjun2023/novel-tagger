#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""query.py — 按标签/关键字查询片段，返回对应段落文字。

用法：
  python query.py --lib taglib --tag plot:恋爱 --tag scene:对话 --limit 10
  python query.py --lib taglib --kw 亲吻 --full            # 关键字 → 段落原文全文
  python query.py --lib taglib --kw 苏晚 --full            # 人物名/角色标签也能查
  python query.py --lib taglib --book 某书 --chapter 3 --full
  python query.py --lib taglib --stats
说明：默认只显示摘要（省 token/省屏），--full 输出完整段落；--json 出结构化数据。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _common as C
import qlib


def print_row(i, r, args):
    tags = r.get("tags") or {}
    pieces = []
    for k, v in tags.items():
        if k == "term" and not args.show_terms and v:
            pieces.append(f"term:{len(v)}词")
        else:
            pieces.append(f"{k}:{','.join(v)}")
    tag_line = " ｜ ".join(pieces)
    pos = f"{r.get('book')} 第{r.get('chapter')}章 片段{r.get('frag_no')}  [{r.get('start')}-{r.get('end')}]"
    print(f"[{i}] {pos}")
    print(f"    {tag_line}")
    text = r.get("text") or ""
    if args.full:
        print("    " + text.replace("\n", "\n    "))
    else:
        prev = text[:args.preview].replace("\n", " ")
        if len(text) > args.preview:
            prev += "……"
        print(f"    {prev}")
    print()


def print_stats(lib, book=None, show_terms=False):
    con = qlib.connect(lib)
    try:
        total = con.execute("SELECT COUNT(*) FROM frags").fetchone()[0]
        chars = con.execute("SELECT SUM(n_chars) FROM frags").fetchone()[0] or 0
        pend = con.execute("SELECT COUNT(*) FROM frags WHERE llm_done=0").fetchone()[0]
        print(f"片段总数 {total} ｜ 总字数 {chars:,} ｜ 待LLM {pend}")
        tags = qlib.list_tags(lib, book=book)
        for dim in sorted(tags.keys()):
            if dim == "term" and not show_terms:
                hits = sum(it["count"] for it in tags[dim])
                print(f"[term] {len(tags[dim])} 个词条 / 命中 {hits} 次（词面默认不回显；--show-terms 显示）")
                continue
            s = " ｜ ".join(f"{it['tag']}:{it['count']}" for it in tags[dim])
            print(f"[{dim}] {s}")
    finally:
        con.close()


def main():
    C.setup_console()
    ap = argparse.ArgumentParser(description="查询已拆解的小说片段")
    ap.add_argument("--lib", default="taglib")
    ap.add_argument("--tag", action="append", default=[],
                    help="维度:标签，可重复；同维度=或，不同维度=且；如 plot:恋爱")
    ap.add_argument("--kw", default=None, help="关键字/标签词：命中标签或原文都算")
    ap.add_argument("--mode", choices=["any", "tag", "text"], default="any")
    ap.add_argument("--book", default=None)
    ap.add_argument("--chapter", type=int, default=None)
    ap.add_argument("--text", default=None, help="原文包含该词（纯文本过滤）")
    ap.add_argument("--limit", type=int, default=10)
    ap.add_argument("--offset", type=int, default=0)
    ap.add_argument("--preview", type=int, default=120)
    ap.add_argument("--full", action="store_true", help="输出完整段落文字")
    ap.add_argument("--order", choices=["id", "chapter", "chars"], default="id")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--ids-only", action="store_true")
    ap.add_argument("--stats", action="store_true")
    ap.add_argument("--show-terms", action="store_true",
                    help="片段标签/--stats 里显示自动词条（term）词面（默认遮挡：避免书内敏感词进入对话上下文）")
    args = ap.parse_args()

    lib = Path(args.lib)
    if args.stats:
        print_stats(lib, book=args.book, show_terms=args.show_terms)
        return

    rows, total = qlib.search(
        lib,
        tags=C.parse_tag_args(args.tag),
        book=args.book,
        chapter=args.chapter,
        kw=args.kw,
        mode=args.mode,
        text=args.text,
        limit=args.limit,
        offset=args.offset,
        order=args.order,
    )
    if args.json:
        print(json.dumps({"total": total, "rows": rows}, ensure_ascii=False, indent=2))
        return
    if args.ids_only:
        print("\n".join(r["id"] for r in rows))
        return
    print(f"共命中 {total} 个片段（显示 {len(rows)} 个）\n")
    for i, r in enumerate(rows, 1 + args.offset):
        print_row(i, r, args)


if __name__ == "__main__":
    main()
