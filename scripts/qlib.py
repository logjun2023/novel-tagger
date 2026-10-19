#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""qlib.py — novel-tagger 查询函数库（可被其它脚本/技能 import 复用）。

主要函数：
    list_books(lib)                         列出库中所有书目（片段数/字数/待标数）
    list_tags(lib, book=None, dim=None)     已拆出的标签/关键字列表（含计数）
    keyword_index(lib, book=None)           倒排索引：标签/命中词 → 片段ID 列表
    search(lib, tags/book/chapter/kw/...)   组合查询（标签 & 原文关键字）
    get_fragment(lib, frag_id)              取单个片段全文
    neighbor_fragments(lib, frag_id, n)     取相邻片段（读上下文）

所有函数直接从 <lib>/index.sqlite 读取（自动重建）。
"""
from __future__ import annotations

import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _common as C


def connect(lib):
    return C.connect(lib)


def list_books(lib) -> list[dict]:
    """列出库中所有书目。"""
    con = connect(lib)
    try:
        rows = con.execute(
            "SELECT book, COUNT(*) AS frags, SUM(n_chars) AS chars, SUM(llm_done) AS done "
            "FROM frags GROUP BY book ORDER BY book").fetchall()
        out = []
        for r in rows:
            out.append({
                "book": r["book"],
                "fragments": r["frags"],
                "chars": r["chars"] or 0,
                "llm_done": r["done"] or 0,
                "llm_pending": r["frags"] - (r["done"] or 0),
            })
        return out
    finally:
        con.close()


def list_tags(lib, book=None, dim=None) -> dict:
    """已拆出的关键字/标签列表 → {维度: [{'tag': 标签, 'count': 片段数}, ...]}。"""
    con = connect(lib)
    try:
        if book:
            sql = ("SELECT t.dim, t.tag, COUNT(*) c FROM tags t "
                   "JOIN frags f ON f.id = t.frag_id WHERE f.book LIKE ?")
            params = [f"%{book}%"]
            has_where = True
        else:
            sql = "SELECT t.dim, t.tag, COUNT(*) c FROM tags t"
            params = []
            has_where = False
        if dim:
            conds = "t.dim = ?"
            sql += (" AND " if has_where else " WHERE ") + conds
            params.append(dim)
        sql += " GROUP BY t.dim, t.tag ORDER BY t.dim, c DESC, t.tag"
        result: dict[str, list] = {}
        for r in con.execute(sql, params):
            result.setdefault(r["dim"], []).append({"tag": r["tag"], "count": r["c"]})
        return result
    finally:
        con.close()


def keyword_index(lib, book=None):
    """倒排索引：返回 (tag_map, kw_map)，均为 {(维度, 名称): set(片段ID)}。"""
    rows = C.select_rows(lib, book=book, order="chapter")
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
    return dict(tag_map), dict(kw_map)


def search(lib, tags=None, book=None, chapter=None, kw=None, mode="any",
           text=None, limit=None, offset=0, order="id"):
    """组合查询。返回 (rows, total)。

    tags:  [(dim, [values])]（parse_tag_args 的产物）
    kw:    关键字；mode=any 时命中标签或原文都算，tag=只比标签，text=只比原文
    """
    rows = C.select_rows(lib, tag_filters=tags, book=book, chapter=chapter,
                         text=text, limit=None, offset=0, order=order)
    if kw:
        k = str(kw).strip()

        def hit(r):
            if mode in ("any", "tag"):
                for vals in (r.get("tags") or {}).values():
                    if any(k in str(v) for v in vals):
                        return True
            if mode in ("any", "text"):
                if k in (r.get("text") or ""):
                    return True
            return False

        rows = [r for r in rows if hit(r)]
    total = len(rows)
    start = offset or 0
    rows = rows[start:]
    if limit:
        rows = rows[:limit]
    return rows, total


def get_fragment(lib, frag_id):
    """按片段ID取全文。"""
    con = connect(lib)
    try:
        r = con.execute("SELECT * FROM frags WHERE id=?", (frag_id,)).fetchone()
        return C.row_to_dict(r) if r else None
    finally:
        con.close()


def neighbor_fragments(lib, frag_id, n=1):
    """取某个片段前后 n 个片段（同章内），便于读上下文。"""
    con = connect(lib)
    try:
        cur = con.execute("SELECT book, chapter, frag_no FROM frags WHERE id=?", (frag_id,)).fetchone()
        if not cur:
            return []
        rows = con.execute(
            "SELECT * FROM frags WHERE book=? AND chapter=? AND frag_no BETWEEN ? AND ? ORDER BY frag_no",
            (cur["book"], cur["chapter"], cur["frag_no"] - n, cur["frag_no"] + n)).fetchall()
        return [C.row_to_dict(r) for r in rows]
    finally:
        con.close()
