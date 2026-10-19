#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""list_tags.py — 列出已拆出的关键字/标签列表（某本书 或 整个库目录下所有小说）。

用法：
  python list_tags.py --lib taglib                    # 所有书目汇总 + 分书统计
  python list_tags.py --lib taglib --book 某书        # 单本
  python list_tags.py --lib taglib --dim plot         # 只看「类型」维度
  python list_tags.py --lib taglib --books            # 只列书目
  python list_tags.py --lib taglib --library          # 附：标准库+用户库的词库定义
  python list_tags.py --lib taglib --json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _common as C
import qlib

DEFAULT_CFG = Path(__file__).resolve().parent.parent / "config"


def main():
    C.setup_console()
    ap = argparse.ArgumentParser(description="列已拆出的标签/关键字")
    ap.add_argument("--lib", default="taglib")
    ap.add_argument("--book", default=None)
    ap.add_argument("--dim", action="append", default=None)
    ap.add_argument("--books", action="store_true", help="只列书目信息")
    ap.add_argument("--library", action="store_true", help="附：合并后的词库定义（标准库+用户库）")
    ap.add_argument("--config-dir", default=None)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--show-terms", action="store_true",
                    help="显示自动词条（term）词面（默认遮挡、只给数量：避免书内敏感词进入对话上下文）")
    args = ap.parse_args()

    lib = Path(args.lib)
    books = qlib.list_books(lib)

    if args.books:
        if args.json:
            print(json.dumps(books, ensure_ascii=False, indent=2))
        else:
            print(f"库目录 {lib} ｜ 书目 {len(books)} 本")
            for b in books:
                print(f"  《{b['book']}》 片段 {b['fragments']} ｜ 字数 {b['chars']:,} ｜ 待LLM {b['llm_pending']}")
        return

    dims = set(args.dim or [])
    out = {
        "lib": str(lib),
        "books": books,
        "all": qlib.list_tags(lib),
    }
    if args.book:
        out["filtered"] = qlib.list_tags(lib, book=args.book)
    if args.library:
        cfg_dir = Path(args.config_dir) if args.config_dir else DEFAULT_CFG
        _, keywords, _, info = C.load_libraries(cfg_dir, lib)
        out["library"] = keywords
        out["library_info"] = info

    if args.json:
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return

    print(f"库目录 {lib} ｜ 书目 {len(books)} 本")
    for b in books:
        print(f"  《{b['book']}》 片段 {b['fragments']} ｜ 字数 {b['chars']:,} ｜ 待LLM {b['llm_pending']}")
    print()

    def fmt_dim(dim, items):
        if dim == "term" and not args.show_terms:
            hits = sum(it["count"] for it in items)
            return f"{len(items)} 个词条 / 命中 {hits} 次（词面默认不回显；--show-terms 显示，或直接看 terms.json）"
        return " ｜ ".join(f"{it['tag']} {it['count']}" for it in items)

    def show(title, tagmap):
        print(f"== {title} ==")
        for dim in sorted(tagmap.keys()):
            if dims and dim not in dims:
                continue
            print(f"[{dim}] {fmt_dim(dim, tagmap[dim])}")
        print()

    show("全库标签/关键字（维度 · 标签 计数）", out["all"])

    if args.book:
        show(f"《{args.book}》标签/关键字", out["filtered"])
    elif len(books) > 1:
        print("== 分书标签 ==")
        for b in books:
            t = qlib.list_tags(lib, book=b["book"])
            flat = []
            for dim in sorted(t.keys()):
                if dim == "term" and not args.show_terms:
                    flat.append(f"[term] {len(t[dim])} 个词条（词面默认不回显）")
                else:
                    flat.append(f"[{dim}] " + " ".join(it["tag"] for it in t[dim]))
            print(f"《{b['book']}》：" + " ｜ ".join(flat))
        print()

    if args.library:
        info = out["library_info"]
        print("== 词库定义（标准库 + 用户库） ==")
        for dim in sorted(out["library"].keys()):
            print(f"[{dim}]")
            for tag, spec in out["library"][dim].items():
                kws = (spec or {}).get("keywords", []) if isinstance(spec, dict) else (spec or [])
                thr = (spec or {}).get("threshold", 2) if isinstance(spec, dict) else 2
                shown = "、".join(kws[:30]) + ("…" if len(kws) > 30 else "")
                print(f"  {tag}（阈值{thr}）：{shown}")
        print(f"\n标准库: {info['standard_dir']}")
        print(f"用户库: {'；'.join(info['user_files']) or '（无，示例见 config/keywords.user.example.yaml）'}")


if __name__ == "__main__":
    main()
