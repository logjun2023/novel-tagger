#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""audit_scene.py — 场景覆盖审计：定位只落到「其他」的片段，按 term 共现建议补词。

背景：scene 打标使用 keywords.user.yaml 里的词表；term 是拆书时自动提炼的书内词条。
两条通道不互通时，自定义 scene 标签可能因词表覆盖不足而静默落入「其他」。

用法：
  python audit_scene.py --lib taglib
  python audit_scene.py --lib taglib --scene-tag 战斗
  python audit_scene.py --lib taglib --scene-tag 战斗 --apply
  python audit_scene.py --lib taglib --scene-tag 战斗 --show-terms
  python audit_scene.py --lib taglib --scene-tag 战斗 --strict   # 覆盖不足时退出码 1

安全输出：默认只给统计与片段 ID，不回显原文、词条词面或命中词；--show-terms 才显示词面。
全程本地零 token。
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _common as C

SCRIPT_DIR = Path(__file__).resolve().parent
OTHER_LABELS = {"其他", "其它"}

# 候选补词的虚词/量词黑名单：避免把「一道」「已经」这类高频通用词并入 scene 词表
CANDIDATE_STOPWORDS = {
    "一道", "一起", "一直", "一样", "一些", "一声", "一阵", "一切", "一边", "一定",
    "什么", "怎么", "这个", "那个", "这些", "那些", "这样", "那样", "这种", "那种",
    "自己", "他们", "她们", "我们", "你们", "大家", "别人", "对方",
    "时候", "现在", "已经", "还是", "只是", "但是", "然后", "于是", "因为", "所以",
    "如果", "虽然", "可以", "没有", "不是", "就是", "出来", "起来", "下来",
    "过去", "过来", "向前", "远处", "似乎", "仿佛", "忽然", "突然",
}


# ------------------------------------------------------------------ 数据读取

def scene_tags(row) -> set:
    return set((row.get("tags") or {}).get("scene") or [])


def is_unclassified(row) -> bool:
    """scene 只有「其他」或没有 scene 标签 → 未分类。"""
    return not (scene_tags(row) - OTHER_LABELS)


def term_tags(row) -> set:
    return set((row.get("tags") or {}).get("term") or [])


def load_rows(lib, book=None):
    rows = list(C.iter_jsonl(Path(lib) / "fragments.jsonl"))
    if book:
        rows = [r for r in rows if r.get("book") == book]
    return rows


def scene_distribution(rows):
    c = Counter()
    for r in rows:
        for t in scene_tags(r):
            c[t] += 1
    return c


def unclassified_stats(rows):
    bad = [r for r in rows if is_unclassified(r)]
    return len(rows), bad


# ------------------------------------------------------------------ 共现分析

def analyze(rows, target, min_target=2, co_occur=2.0):
    """按 term 文档频次找目标标签的高共现候选词。

    候选条件：目标片段中出现 >= min_target 次，且 >= co_occur × 非目标片段中出现次数。
    """
    t_rows = [r for r in rows if target in scene_tags(r)]
    n_rows = [r for r in rows if target not in scene_tags(r)]
    tf, nf = Counter(), Counter()
    for r in t_rows:
        for t in term_tags(r):
            tf[t] += 1
    for r in n_rows:
        for t in term_tags(r):
            nf[t] += 1
    cands = [t for t, tc in tf.items()
             if t not in CANDIDATE_STOPWORDS
             and tc >= min_target and tc >= co_occur * nf.get(t, 0)]
    cands.sort(key=lambda t: (-(tf[t] - nf.get(t, 0)), -tf[t], t))
    return t_rows, n_rows, tf, nf, cands


def fragment_coverage(rows, cands):
    """每个未分类片段能被多少候选词覆盖（优先按正文统计，缺失时退回 term 标签）。"""
    out = []
    for r in rows:
        text = r.get("text") or ""
        if text:
            matched = [t for t in cands if t in text]
            occ = sum(text.count(t) for t in matched)
        else:
            matched = [t for t in cands if t in term_tags(r)]
            occ = len(matched)
        out.append((r, matched, occ))
    out.sort(key=lambda x: (-len(x[1]), -x[2], x[0].get("id") or ""))
    return out


# ------------------------------------------------------------------ 补词写回

def _load_user_keywords(lib):
    """优先读取已存在的 keywords.user.yaml，其次 .json。"""
    for name in ("keywords.user.yaml", "keywords.user.json"):
        p = Path(lib) / name
        if p.exists():
            data = C.load_config(p, default={})
            return p, data if isinstance(data, dict) else {}
    return Path(lib) / "keywords.user.yaml", {}


def apply_terms(lib, target, terms):
    """把候选词并入 <lib>/keywords.user.yaml 的 scene.<target>.keywords。"""
    p, data = _load_user_keywords(lib)
    scene = data.setdefault("scene", {})
    spec = scene.get(target)
    if isinstance(spec, dict):
        kws = list(spec.get("keywords") or [])
    elif isinstance(spec, list):
        kws = list(spec)
        spec = {"threshold": 2, "keywords": kws}
    else:
        kws = []
        spec = {"threshold": 2, "keywords": kws}
    added = [t for t in terms if t not in kws]
    if not added:
        return (p if p.exists() else None), []
    kws.extend(added)
    spec["keywords"] = kws
    spec.setdefault("threshold", 2)
    scene[target] = spec
    data["scene"] = scene
    if p.exists():  # 写前备份
        bak = p.with_name(p.name + ".bak")
        bak.write_text(p.read_text(encoding="utf-8"), encoding="utf-8")
    actual = C.save_config(p, data)
    return actual, added


def run_retag(lib):
    cmd = [sys.executable, str(SCRIPT_DIR / "segment.py"),
           "--lib", str(lib), "--retag", "--only-dims", "scene"]
    print("[audit] 重打 scene 标签：" + " ".join(cmd))
    return subprocess.run(cmd).returncode


# ------------------------------------------------------------------ 主流程

def main():
    C.setup_console()
    ap = argparse.ArgumentParser(
        description="场景覆盖审计：定位落入「其他」的片段，按 term 共现建议补词（本地零 token）")
    ap.add_argument("--lib", default="taglib", help="素材库目录（默认 ./taglib）")
    ap.add_argument("--book", default=None, help="只审计某本书")
    ap.add_argument("--scene-tag", default=None,
                    help="目标 scene 标签；省略则只输出覆盖率统计")
    ap.add_argument("--apply", action="store_true",
                    help="把候选词并入 <lib>/keywords.user.yaml（自动备份 .bak）")
    ap.add_argument("--no-retag", action="store_true",
                    help="--apply 后不自动重打 scene 标签")
    ap.add_argument("--show-terms", action="store_true",
                    help="显示候选词面（默认只给数量）")
    ap.add_argument("--threshold", type=float, default=0.10,
                    help="未分类占比告警阈值（默认 0.10）")
    ap.add_argument("--min-frags", type=int, default=5,
                    help="片段数低于该值不做覆盖告警（默认 5）")
    ap.add_argument("--min-target", type=int, default=2,
                    help="候选词至少命中的目标片段数（默认 2）")
    ap.add_argument("--co-occur", type=float, default=2.0,
                    help="候选词共现比：目标片段数 >= N × 非目标片段数（默认 2.0）")
    ap.add_argument("--max-terms", type=int, default=50,
                    help="候选补词上限（默认 50）")
    ap.add_argument("--strict", action="store_true",
                    help="未分类占比超阈值时退出码 1（便于脚本感知）")
    args = ap.parse_args()

    if args.apply and not args.scene_tag:
        C.die("--apply 需要同时指定 --scene-tag")

    lib = Path(args.lib)
    rows = load_rows(lib, args.book)
    if not rows:
        C.die(f"{lib / 'fragments.jsonl'} 为空，先运行：python scripts/segment.py <小说文件> --lib {lib}")

    total, bad = unclassified_stats(rows)
    ratio = (len(bad) / total) if total else 0.0
    dist = scene_distribution(rows)

    print(f"[audit] 素材库：{lib} ｜ 片段：{total}")
    if dist:
        print("场景分布：" + " ｜ ".join(f"scene:{k} {v}" for k, v in dist.most_common()))
    else:
        print("场景分布：（无 scene 标签）")
    print(f"scene 未分类（其他/无标签）：{len(bad)}/{total}"
          f"（{round(ratio * 100)}%，阈值 {round(args.threshold * 100)}%）")
    if bad:
        ids = [r.get("id") or "?" for r in bad]
        shown = ids[:20]
        suffix = f" … 共 {len(ids)} 个" if len(ids) > len(shown) else ""
        print("未分类片段：" + "、".join(shown) + suffix)
    else:
        print("[audit] 覆盖率正常：所有片段都有有效 scene 标签")

    target = args.scene_tag
    if not target:
        ranked = [(k, v) for k, v in dist.most_common() if k not in OTHER_LABELS]
        if ranked:
            target = ranked[0][0]
            print(f"未指定 --scene-tag → 自动分析最高频标签 scene:{target}"
                  "（只读；--apply 需显式指定）")

    if target:
        t_rows, n_rows, tf, nf, cands = analyze(rows, target, args.min_target, args.co_occur)
        cands = cands[:args.max_terms]
        if not t_rows:
            print(f"[audit] 目标标签 scene:{target} 无命中：请确认标签名，或先看上面的场景分布")
        else:
            print(f"目标标签：scene:{target} ｜ 命中片段：{len(t_rows)} ｜ 非目标：{len(n_rows)}")
            print(f"候选补词：{len(cands)} 个"
                  f"（目标片段 ≥{args.min_target} 且 ≥{args.co_occur:g}×非目标）")
            if not cands:
                print("提示：没有高共现候选词；确认拆书时已开启词条提炼（默认开启），或补充种子片段")
            cov = fragment_coverage(bad, cands)
            covered = [(r, m, o) for r, m, o in cov if m]
            print(f"未分类片段可覆盖：{len(covered)}/{len(cov)}")
            for r, matched, occ in covered[:20]:
                extra = f"  [{'、'.join(matched)}]" if args.show_terms else ""
                print(f"  - {r.get('id')}  覆盖 {len(matched)} 词 / {occ} 次{extra}")
            if cands:
                if args.show_terms:
                    print("候选词面：" + "、".join(cands))
                else:
                    print("候选词面默认遮挡（加 --show-terms 查看）")
        if args.apply:
            if not cands:
                print("[audit] 无可补候选词，未修改配置")
            else:
                actual, added = apply_terms(lib, target, cands)
                if not added:
                    print("[audit] 候选词已全部存在，未修改配置")
                else:
                    print(f"[audit] 已补词 {len(added)} 个 → {actual}（旧文件备份为 .bak）")
                    if args.no_retag:
                        print(f"[audit] 请手动重打：python scripts/segment.py --lib {lib} --retag --only-dims scene")
                    else:
                        rc = run_retag(lib)
                        if rc != 0:
                            print(f"[audit] 重打失败（exit {rc}），请手动运行："
                                  f"python scripts/segment.py --lib {lib} --retag --only-dims scene")
                        else:
                            rows = load_rows(lib, args.book)
                            total, bad = unclassified_stats(rows)
                            ratio = (len(bad) / total) if total else 0.0
                            print(f"[audit] 重打后 scene 未分类：{len(bad)}/{total}（{round(ratio * 100)}%）")
    else:
        print("提示：当前没有可分析的非「其他」scene 标签；可用 --scene-tag <标签> 指定")

    if args.strict and total >= args.min_frags and ratio >= args.threshold:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
