#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""export.py — 按标签分存导出（一个标签一个文件夹，方便按素材类型收集）。

用法：
  python export.py --lib taglib --by plot              # 按「类型」分文件夹
  python export.py --lib taglib --by scene --tag plot:恋爱
  python export.py --lib taglib --by all               # 每个维度各导一份
  python export.py --lib taglib --by none              # 不分组，全部平铺
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _common as C


def frag_md(r) -> str:
    tags = r.get("tags") or {}
    tag_line = "；".join(f"{k}={','.join(v)}" for k, v in tags.items())
    return (
        f"# {r.get('book')} 第{r.get('chapter')}章 片段{r.get('frag_no')}\n\n"
        f"- id: {r.get('id')} ｜ 位置: {r.get('start')}-{r.get('end')} ｜ "
        f"字数: {r.get('n_chars')} ｜ 对话占比: {r.get('dialogue_ratio')}\n"
        f"- 标签: {tag_line}\n\n---\n\n{r.get('text')}\n"
    )


def main():
    C.setup_console()
    ap = argparse.ArgumentParser(description="按标签分存导出")
    ap.add_argument("--lib", default="taglib")
    ap.add_argument("--tag", action="append", default=[])
    ap.add_argument("--book", default=None)
    ap.add_argument("--by", default="plot", help="按哪个维度分存；all=每个维度各一份；none=单文件夹")
    ap.add_argument("--out", default=None)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--skip-untagged", action="store_true", help="没打该维度标签的片段直接跳过（默认放入「未分类」）")
    args = ap.parse_args()

    lib = Path(args.lib)
    out = Path(args.out) if args.out else lib / "export"
    rows = C.select_rows(lib, tag_filters=C.parse_tag_args(args.tag), book=args.book, order="chapter")
    if args.limit:
        rows = rows[: args.limit]
    if not rows:
        print("没有匹配的片段")
        return

    if args.by == "all":
        seen = set()
        for r in rows:
            for d in (r.get("tags") or {}):
                seen.add(d)
        dims = ["scene", "chars", "plot", "style", "term"] + sorted(
            seen - {"scene", "chars", "plot", "style", "term"})
    elif args.by == "none":
        dims = [None]
    else:
        dims = [args.by]

    wrote = 0
    for dim in dims:
        sub = out if dim is None else out / dim
        sub.mkdir(parents=True, exist_ok=True)
        for r in rows:
            if dim is None:
                vals = [None]
            else:
                vals = (r.get("tags") or {}).get(dim) or []
                if not vals:
                    if args.skip_untagged:
                        continue
                    vals = ["未分类"]
            for v in vals:
                folder = sub if v is None else sub / C.sanitize_filename(v, "未分类")
                folder.mkdir(parents=True, exist_ok=True)
                fp = folder / (C.sanitize_filename(r.get("id"), "frag") + ".md")
                fp.write_text(frag_md(r), encoding="utf-8")
                wrote += 1
    print(f"已导出 {len(rows)} 个片段 × {len(dims)} 个分组 → {wrote} 个文件，目录：{out}")


if __name__ == "__main__":
    main()
