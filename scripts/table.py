#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""table.py — 导出「多关键字表格」：一行=片段位置，多列=标签/关键字/命中词。

产物（默认 <lib>/tables/）：
  fragments.csv       片段×标签总表（utf-8-sig，Excel 可直接打开）
  fragments.md        同内容的 Markdown 表格
  keyword_index.csv   倒排索引：标签/命中词 → 片段ID 列表
  <书名>_fragments.csv（--per-book 时按书拆分）

用法：
  python table.py --lib taglib
  python table.py --lib taglib --book 某书 --tag plot:恋爱
  python table.py --lib taglib --per-book --out D:/导出
"""
from __future__ import annotations

import argparse
import csv
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _common as C


def main():
    C.setup_console()
    ap = argparse.ArgumentParser(description="导出多关键字表格")
    ap.add_argument("--lib", default="taglib")
    ap.add_argument("--book", default=None)
    ap.add_argument("--tag", action="append", default=[])
    ap.add_argument("--chapter", type=int, default=None)
    ap.add_argument("--out", default=None)
    ap.add_argument("--per-book", action="store_true")
    ap.add_argument("--preview", type=int, default=40)
    ap.add_argument("--no-hits", action="store_true", help="不输出命中关键词列")
    args = ap.parse_args()

    lib = Path(args.lib)
    out = Path(args.out) if args.out else lib / "tables"
    out.mkdir(parents=True, exist_ok=True)

    rows = C.select_rows(lib, tag_filters=C.parse_tag_args(args.tag), book=args.book,
                         chapter=args.chapter, order="chapter")
    if not rows:
        print("没有匹配的片段（先运行 segment.py 拆解小说，或检查筛选条件）")
        return

    # 维度列顺序：常用维度在前，用户新增维度在后
    preferred = ["scene", "chars", "plot", "style", "term"]
    seen = set()
    for r in rows:
        for d in (r.get("tags") or {}):
            seen.add(d)
    dims = [d for d in preferred if d in seen] + [d for d in sorted(seen) if d not in preferred]

    header = ["片段ID", "书名", "章号", "章节标题", "起始", "结束", "字数", "对话占比"] + dims
    if not args.no_hits:
        header += ["标签计数", "命中关键词"]
    header += ["预览"]

    def make_row(r):
        tags = r.get("tags") or {}
        hits = r.get("hits") or {}
        pos = [r.get("id"), r.get("book"), r.get("chapter"), r.get("chapter_title", ""),
               r.get("start"), r.get("end"), r.get("n_chars"), r.get("dialogue_ratio")]
        dimvals = [",".join(tags.get(d, [])) for d in dims]
        tail = []
        if not args.no_hits:
            cnt_line = " ".join(f"{k}({v.get('n')})" for k, v in hits.items())
            kw_flat = []
            for v in hits.values():
                for w in (v.get("kws") or []):
                    if w not in kw_flat:
                        kw_flat.append(w)
            tail += [cnt_line, "、".join(kw_flat)]
        preview = (r.get("text") or "").replace("\n", " ")[: args.preview]
        tail.append(preview)
        return pos + dimvals + tail

    def write_csv(path, head, data_rows):
        with path.open("w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f)
            w.writerow(head)
            w.writerows(data_rows)

    def write_md(path, head, data_rows):
        def esc(x):
            return str(x).replace("|", "\\|").replace("\n", " ")
        with path.open("w", encoding="utf-8") as f:
            f.write("| " + " | ".join(head) + " |\n")
            f.write("|" + "|".join(["---"] * len(head)) + "|\n")
            for row in data_rows:
                f.write("| " + " | ".join(esc(x) for x in row) + " |\n")

    data_rows = [make_row(r) for r in rows]
    write_csv(out / "fragments.csv", header, data_rows)
    write_md(out / "fragments.md", header, data_rows)

    if args.per_book:
        by_book = defaultdict(list)
        for r in rows:
            by_book[r.get("book")].append(r)
        for book, brs in by_book.items():
            d = [make_row(r) for r in brs]
            fn = C.sanitize_filename(book) + "_fragments.csv"
            write_csv(out / fn, header, d)

    # 倒排索引
    tag_map = defaultdict(set)
    kw_map = defaultdict(set)
    for r in rows:
        for d, vals in (r.get("tags") or {}).items():
            for v in vals:
                tag_map[(d, v)].add(r["id"])
        for key, h in (r.get("hits") or {}).items():
            d = key.split(":", 1)[0]
            for w in (h.get("kws") or []):
                kw_map[(d, w)].add(r["id"])
    idx_rows = []
    for (d, name), ids in sorted(tag_map.items()):
        idx_rows.append([name, d, "标签", len(ids), ";".join(sorted(ids))])
    for (d, name), ids in sorted(kw_map.items()):
        idx_rows.append([name, d, "命中词", len(ids), ";".join(sorted(ids))])
    write_csv(out / "keyword_index.csv", ["关键字", "维度", "类型", "片段数", "片段ID列表"], idx_rows)

    print(f"已导出 {len(rows)} 个片段 × {len(dims)} 个维度 → {out}")
    print(f"  fragments.csv / fragments.md（总表，Excel 可直接打开）")
    print(f"  keyword_index.csv（倒排：标签/命中词 → 片段ID）")
    if args.per_book:
        print(f"  各书单独表格：<书名>_fragments.csv")


if __name__ == "__main__":
    main()
