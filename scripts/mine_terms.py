#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""mine_terms.py — 从具体小说里自动提炼关键词（零 LLM，不预先指定）。

算法：无监督新词发现 —— 频次 + 凝固度(PMI) + 左右邻字熵，在句子内的 CJK 连续段取 2-4 字 n-gram。
产物：<lib>/terms.json（按书分组的词条表：词条/频次/凝固度/熵/出现章节）
默认：拆书（segment.py）时会对新书自动提炼一次；本脚本用于手动重炼 / 全库批量 / 调参预览。
应用：segment.py 打标时会把命中词条的片段加上 term 维度标签（如 term:咖啡馆）
      → query --tag term:咖啡馆 / table.py / export --by term 都可用；不想要就编辑 terms.json 删掉

用法：
  python mine_terms.py done/某书.md --lib taglib
  python mine_terms.py done/某书.md --lib taglib --min-freq 5 --top 80
  python mine_terms.py --lib taglib --from-lib            # 对库里所有书（用已拆片段）提炼
  python mine_terms.py --lib taglib --from-lib --book 某书
应用/刷新标签：
  python segment.py <同文件> --lib <lib> --retag --only-dims term
"""
from __future__ import annotations

import argparse
import math
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _common as C

DEFAULT_CFG = Path(__file__).resolve().parent.parent / "config"

CJK_RUN = re.compile(r"[\u4e00-\u9fff]+")
BLOCK_ANY = set("的了着过吗呢吧啊呀哦嗯么们")  # 词条内部一律不允许出现（几乎不在实词里）
TERM_STOP = set((
    "自己 对方 大家 我们 你们 他们 她们 它们 什么 怎么 为什么 这样 那样 这些 那些 这里 那里 这种 那种 "
    "时候 一个 两个 一下 一声 一阵 一道 一直 一样 一切 一些 一边 一面 有些 有的 没有 不是 就是 还是 还有 "
    "知道 觉得 认为 发现 看到 听到 见到 感到 应该 可能 也许 大概 几乎 更加 非常 特别 真的 的确 "
    "已经 曾经 现在 如今 当时 此时 此刻 然后 接着 随后 同时 于是 但是 可是 不过 而且 因为 所以 如果 虽然 即使 "
    "突然 忽然 顿时 立刻 马上 终于 果然 居然 竟然 仍然 依然 当然 只是 只有 只要 除了 关于 对于 由于 为了 "
    "出来 起来 过来 过去 下来 上去 回来 回去 进来 出去 上来 下去 一起 一定 刚刚 已然 即将 简直 甚至 "
    "说道 回答 开口 看向 露出 谢谢 再见 对不起 干嘛 有点 有些 可否 立刻 连忙 急忙 赶紧 稍等 "
    "小时 分钟 一点 一杯 一句 一眼 一遍 一趟 一次 一头 一口 一身 一手 一旁 半天 "
    "心里 脸上 身上 手中 手里 眼中 眼里 眼前 耳边 身后 面前 身边 旁边 对面 前面 后面 上面 下面 里面 外面 "
    "声音 样子 "
    "这个 那个 哪个 每个 一位 两位 几位 两人 三人 双手 不停 不断 於是 "
    "在我 在你 在他 在她 被我 被你 被他 被她 把我 把你 把他 把她 让我 让你 让他 让她 "
    "给我 给你 给他 给她 为我 为你 对我 对你 对他 对她 "
    "我也 你也 他也 她也 一股 说完 好像 似乎 这位 那位"
).split())


def entropy_of(counter: Counter) -> float:
    tot = sum(counter.values())
    if tot <= 0:
        return 0.0
    return -sum((v / tot) * math.log2(v / tot) for v in counter.values())


def cohesion_of(cand: str, counts: Counter, chars: Counter, total: int) -> float:
    """凝固度 = 各切分点 PMI 的最小值（越大越像一个整体词）。"""
    def cnt(s: str) -> int:
        return chars.get(s, 0) if len(s) == 1 else counts.get(s, 0)

    cw = counts.get(cand, 0)
    if cw <= 0 or total <= 0:
        return 0.0
    best = None
    for k in range(1, len(cand)):
        l, r = cand[:k], cand[k:]
        cl, cr = cnt(l), cnt(r)
        if cl <= 0 or cr <= 0:
            return 0.0
        pmi = math.log2(cw * total / (cl * cr))
        best = pmi if best is None else min(best, pmi)
    return best or 0.0


def mine_book(chapters, exclude_names, params=None) -> list[dict]:
    """对一本书的章节文本提炼词条。params: min_freq/min_cohesion/min_entropy/top。"""
    p = params or {}
    min_freq = int(p.get("min_freq", 6))
    min_cohesion = float(p.get("min_cohesion", 2.5))
    min_entropy = float(p.get("min_entropy", 1.0))
    top = int(p.get("top", 80))
    counts = Counter()
    chars = Counter()
    total = 0
    for _, txt in chapters:
        for run in CJK_RUN.findall(txt):
            L = len(run)
            total += L
            for ch in run:
                chars[ch] += 1
            for n in (2, 3, 4):
                if L < n:
                    continue
                for i in range(L - n + 1):
                    cand = run[i:i + n]
                    if any(c in BLOCK_ANY for c in cand):
                        continue
                    counts[cand] += 1

    shortlist = [c for c, n in counts.items() if n >= min_freq]
    if len(shortlist) > 4000:
        shortlist = sorted(shortlist, key=lambda c: -counts[c])[:4000]
    shortset = set(shortlist)

    left = defaultdict(Counter)
    right = defaultdict(Counter)
    chapter_hits = defaultdict(set)
    for cno, txt in chapters:
        for run in CJK_RUN.findall(txt):
            L = len(run)
            for n in (2, 3, 4):
                if L < n:
                    continue
                for i in range(L - n + 1):
                    cand = run[i:i + n]
                    if cand not in shortset:
                        continue
                    if i > 0:
                        left[cand][run[i - 1]] += 1
                    if i + n < L:
                        right[cand][run[i + n]] += 1
                    chapter_hits[cand].add(cno)

    total = max(total, 1)
    out = []
    for cand in shortlist:
        if cand in TERM_STOP:
            continue
        if any(nm in cand for nm in exclude_names):
            continue
        coh = cohesion_of(cand, counts, chars, total)
        if coh < min_cohesion:
            continue
        le, re_ = entropy_of(left[cand]), entropy_of(right[cand])
        if min(le, re_) < min_entropy:
            continue
        out.append({
            "term": cand,
            "freq": counts[cand],
            "cohesion": round(coh, 2),
            "entropy": round(min(le, re_), 2),
            "chapters": sorted(chapter_hits[cand]),
        })
    out.sort(key=lambda x: (-x["freq"], -x["cohesion"]))
    return out[: top]


def auto_params(total_chars: int) -> dict:
    """按书的长短自动选提炼参数（大书收紧频次，小样本放宽）。"""
    n = max(int(total_chars or 0), 0)
    if n < 8000:
        mf = 3
    elif n < 30000:
        mf = 4
    elif n < 80000:
        mf = 6
    else:
        mf = 8
    return {"min_freq": mf, "min_cohesion": 2.5, "min_entropy": 1.0, "top": 80}


def save_book_terms(lib, book, terms, params, source="auto"):
    """把一本书的词条合并写入 <lib>/terms.json（不动其它书），返回文件路径。"""
    tpath = Path(lib) / "terms.json"
    data = C.load_config(tpath, default={"version": 1, "books": {}}) or {"version": 1, "books": {}}
    data.setdefault("books", {})
    data["books"][book] = {
        "source": source,
        "generated": C.now_str(),
        "params": dict(params or {}),
        "terms": list(terms or []),
    }
    C.dump_json(tpath, data)
    return tpath


def chapters_from_file(fp, rules):
    import segment as SEG
    text = C.read_text(fp).replace("\r\n", "\n").replace("\r", "\n")
    chs = SEG.split_chapters(text, rules)
    return [(c["no"], c["body"]) for c in chs if (c.get("body") or "").strip()]


def load_exclude_names(lib) -> set:
    """人物表里已有的名字/别名不必再当词条（人物标签走 chars 维度）。"""
    names = set()
    for fn in ("characters.yaml", "characters.json"):
        p = Path(lib) / fn
        if p.exists():
            cfg = C.load_config(p) or {}
            for c in cfg.get("characters", []) or []:
                for nm in [c.get("name", "")] + list(c.get("aliases") or []):
                    if nm and len(nm) >= 2:
                        names.add(nm)
            break
    return names


def main():
    C.setup_console()
    ap = argparse.ArgumentParser(description="从小说里自动提炼关键词（新词发现，零 LLM）")
    ap.add_argument("files", nargs="*", help="小说文件（.md/.txt）；或用 --from-lib")
    ap.add_argument("--lib", default="taglib")
    ap.add_argument("--book-name", default=None, help="单文件模式书名（默认取文件名）")
    ap.add_argument("--from-lib", action="store_true", help="对素材库里已拆的所有书提炼（用 fragments.jsonl）")
    ap.add_argument("--book", default=None, help="--from-lib 时只看匹配的书名")
    ap.add_argument("--min-freq", type=int, default=6)
    ap.add_argument("--min-cohesion", type=float, default=2.5)
    ap.add_argument("--min-entropy", type=float, default=1.0)
    ap.add_argument("--top", type=int, default=80)
    ap.add_argument("--dry-run", action="store_true", help="只预览，不写入 terms.json")
    ap.add_argument("--show-terms", action="store_true",
                    help="打印词条词面（默认只报数量：避免书内敏感词进入对话上下文）")
    args = ap.parse_args()

    lib = Path(args.lib)
    lib.mkdir(parents=True, exist_ok=True)
    rules, _, _, _ = C.load_libraries(DEFAULT_CFG, lib)

    jobs = []
    if args.from_lib:
        rows = list(C.iter_jsonl(lib / "fragments.jsonl"))
        by_book = defaultdict(lambda: defaultdict(list))
        for r in rows:
            if args.book and args.book not in (r.get("book") or ""):
                continue
            by_book[r["book"]][r.get("chapter") or 0].append(r.get("text") or "")
        for book, chmap in by_book.items():
            jobs.append((book, [(cno, "\n".join(txts)) for cno, txts in sorted(chmap.items())]))
    else:
        if not args.files:
            C.die("请给出小说文件，或用 --from-lib 对库里所有书提炼")
        for fp in args.files:
            fp = Path(fp)
            if not fp.exists():
                print(f"[跳过] 文件不存在：{fp}")
                continue
            book = args.book_name or fp.stem
            jobs.append((book, chapters_from_file(fp, rules)))
    if not jobs:
        C.die("没有可处理的内容")

    exclude = load_exclude_names(lib)
    tpath = lib / "terms.json"
    data = C.load_config(tpath, default={"version": 1, "books": {}}) or {"version": 1, "books": {}}
    data.setdefault("books", {})

    for book, chapters in jobs:
        body_len = sum(len(t) for _, t in chapters)
        if body_len < 500:
            print(f"[{book}] 文本过短（{body_len} 字），跳过")
            continue
        params = {"min_freq": args.min_freq, "min_cohesion": args.min_cohesion,
                  "min_entropy": args.min_entropy, "top": args.top}
        terms = mine_book(chapters, exclude, params)
        print(f"\n[{book}] 提炼词条 {len(terms)} 条"
              f"（频次≥{args.min_freq}，凝固度≥{args.min_cohesion}，熵≥{args.min_entropy}）")
        if args.show_terms:
            for t in terms[:20]:
                print(f"  {t['freq']:>4}  {t['term']}   凝固{t['cohesion']} 熵{t['entropy']} 章节{len(t['chapters'])}")
        else:
            print("  词面默认不回显（--show-terms 查看；或直接编辑 terms.json）")
        if not args.dry_run:
            src = "fragments.jsonl" if args.from_lib else "file"
            data["books"][book] = {
                "source": src,
                "generated": C.now_str(),
                "params": {"min_freq": args.min_freq, "min_cohesion": args.min_cohesion,
                           "min_entropy": args.min_entropy, "top": args.top},
                "terms": terms,
            }

    if args.dry_run:
        print("\n[dry-run] 未写入 terms.json")
        return
    C.dump_json(tpath, data)
    total_terms = sum(len(v.get("terms", [])) for v in data["books"].values())
    print(f"\n已写入 {tpath}（{len(data['books'])} 本书，共 {total_terms} 条词条）")
    print("应用词条标签：python segment.py <同文件> --lib " + str(lib) + " --retag --only-dims term")
    print("有不满意的词：直接编辑 terms.json 删掉，再重跑上面命令即可")


if __name__ == "__main__":
    main()
