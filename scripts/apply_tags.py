#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""apply_tags.py — 把 LLM 批次打标结果合并回 fragments.jsonl。

结果文件格式（batch_XX.result.json）：
  { "片段ID": {"scene": ["对话"], "plot": ["恋爱"], "confidence": 0.8, "note": "..."} }
  - 标签值前缀 "-" 表示删除该标签（例如 "-悬疑"）
  - 兼容 {"results": {...}} 包装
合并后片段标记 llm_done=true，重跑 todo_llm 会自动跳过。

用法：
  python apply_tags.py --lib taglib taglib/llm_inbox/batch_01.result.json
  python apply_tags.py --lib taglib taglib/llm_inbox/*.result.json --dry-run
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _common as C


def main():
    C.setup_console()
    ap = argparse.ArgumentParser(description="合并 LLM 打标结果")
    ap.add_argument("--lib", required=True)
    ap.add_argument("results", nargs="+")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--mark-batch", default=None,
                    help="同时把该批次文件(todo_llm 产出的 batch_XX.json)里的所有片段标记为已处理")
    args = ap.parse_args()

    lib = Path(args.lib)
    jl = lib / "fragments.jsonl"
    rows = list(C.iter_jsonl(jl))
    index = {r["id"]: r for r in rows}

    touched = added = removed = missed = 0
    for rf in args.results:
        data = C.load_config(rf)
        if isinstance(data, dict) and isinstance(data.get("results"), dict):
            data = data["results"]
        if not isinstance(data, dict):
            print(f"[跳过] 无法识别结果文件格式：{rf}")
            continue
        for fid, dims in data.items():
            r = index.get(fid)
            if r is None:
                missed += 1
                print(f"[警告] 未知片段ID：{fid}")
                continue
            if not isinstance(dims, dict):
                continue
            touched += 1
            tags = r.setdefault("tags", {})
            src = r.setdefault("source", {})
            for dim, vals in dims.items():
                if dim in ("confidence", "note"):
                    if dim == "confidence":
                        try:
                            r["llm_confidence"] = float(vals)
                        except (TypeError, ValueError):
                            pass
                    continue
                if isinstance(vals, str):
                    vals = [vals]
                if not isinstance(vals, list):
                    continue
                cur = tags.setdefault(dim, [])
                for v in vals:
                    v = str(v).strip()
                    if not v:
                        continue
                    if v.startswith("-"):
                        t = v[1:].strip()
                        if t in cur:
                            cur.remove(t)
                            removed += 1
                    elif v not in cur:
                        cur.append(v)
                        added += 1
                if cur:
                    prev = str(src.get(dim, ""))
                    src[dim] = "rule+llm" if "rule" in prev else "llm"
                else:
                    tags.pop(dim, None)
                    src.pop(dim, None)
            r["llm_done"] = True

    confirmed = 0
    if args.mark_batch:
        bd = C.load_config(args.mark_batch)
        for it in (bd.get("items") or []):
            r = index.get(it.get("id"))
            if r is not None and not r.get("llm_done"):
                r["llm_done"] = True
                confirmed += 1

    if args.dry_run:
        print(f"[dry-run] 将更新 {touched} 个片段（新增标签 {added}、删除 {removed}），"
              f"整批标记 {confirmed} 个；未知ID {missed} 个；未写盘")
        return
    C.write_jsonl(jl, rows)
    C.build_db(lib)
    print(f"已合并：更新 {touched} 个片段（新增标签 {added}、删除 {removed}），"
          f"整批标记 {confirmed} 个；未知ID {missed} 个")


if __name__ == "__main__":
    main()
