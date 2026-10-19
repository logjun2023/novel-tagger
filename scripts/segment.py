#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""segment.py — 小说片段切分 + 本地多维打标（零 LLM）。

流程：章节切分 → 段落 → 场景切片（规则：分隔标记 / 时间转场词 / 长度控制）
     → 本地打标（对话规则 + 关键词词典 + 人物 ID/数量 + 词条）
     → 写入 <lib>/fragments.jsonl 并重建 <lib>/index.sqlite

用法：
  python segment.py done/某书.md --lib taglib                 # 拆解 + 打标
  python segment.py done/*.md --lib taglib                    # 批量
  python segment.py done/某书.md --lib taglib --force         # 重切+重标（丢弃该书旧数据）
  python segment.py done/某书.md --lib taglib --retag         # 只重打标签（词典/人物表更新后用）
  python segment.py done/某书.md --lib taglib --retag --only-dims scene,plot
"""
from __future__ import annotations

import argparse
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _common as C
import mine_terms as MT

DEFAULT_CFG = Path(__file__).resolve().parent.parent / "config"


# ---------------------------------------------------------------- 章节切分

def split_chapters(text: str, rules: dict) -> list[dict]:
    lines = text.split("\n")
    pats = [re.compile(p) for p in (rules.get("chapter_patterns") or [])]
    max_len = int(rules.get("chapter_title_max_len", 60))
    marks = []
    for i, raw in enumerate(lines):
        s = raw.strip()
        if not s or len(s) > max_len:
            continue
        if s.startswith("|") or s.startswith("<a id="):
            continue  # 目录表格行 / 锚点行
        for p in pats:
            if p.search(s):
                marks.append(i)
                break
    chapters = []
    if not marks:
        body = text.strip()
        if body:
            chapters.append({"no": 1, "title": "", "body": body})
        return chapters
    for j, li in enumerate(marks):
        end = marks[j + 1] if j + 1 < len(marks) else len(lines)
        title = lines[li].strip().lstrip("#").strip()
        title = title.strip("=").strip()
        title = title.strip("【】〖〗[]").strip()
        body = "\n".join(lines[li + 1:end]).strip()
        chapters.append({"no": j + 1, "title": title, "body": body})
    return chapters


# ---------------------------------------------------------------- 场景切片

def _is_marker(s: str, marker_res) -> bool:
    return any(r.match(s) for r in marker_res)


def split_scenes(paras: list[str], rules: dict) -> list[tuple[int, int]]:
    """把段落切成场景片段（返回 [开始段, 结束段) 列表）。"""
    marker_res = [re.compile(p) for p in (rules.get("break_markers") or [])]
    times = list(rules.get("time_transitions") or [])
    refocus = list(rules.get("refocus_prefixes") or [])
    frag_cfg = rules.get("fragment", {}) or {}
    maxc = int(frag_cfg.get("max_chars", 2400) or 2400)
    minc = int(frag_cfg.get("min_chars", 90) or 90)

    def starts_new(s: str) -> bool:
        head = s[:14]
        return any(head.startswith(t) for t in times) or any(head.startswith(t) for t in refocus)

    n = len(paras)
    groups = []
    start = 0
    for i in range(1, n):
        if _is_marker(paras[i], marker_res) or _is_marker(paras[i - 1], marker_res) or starts_new(paras[i]):
            if i > start:
                groups.append((start, i))
            start = i
    if n > start:
        groups.append((start, n))
    if not groups and n:
        groups = [(0, n)]

    def seg_len(a: int, b: int) -> int:
        return sum(len(paras[i]) + 1 for i in range(a, b) if not _is_marker(paras[i], marker_res))

    # 1) 超长切分（在段落边界）
    split = []
    for a, b in groups:
        cur = a
        while True:
            if seg_len(cur, b) <= maxc or b - cur <= 2:
                split.append((cur, b))
                break
            acc = 0
            cut = None
            for i in range(cur, b - 1):
                acc += len(paras[i]) + 1
                if acc > maxc * 0.8:
                    cut = i + 1
                    break
            if cut is None or cut >= b:
                cut = (cur + b) // 2
            if cut <= cur:
                cut = cur + 1
            split.append((cur, cut))
            cur = cut

    # 2) 合并过短片段
    merged = []
    for g in split:
        if merged and seg_len(*merged[-1]) < minc:
            merged[-1] = (merged[-1][0], g[1])
        else:
            merged.append(g)
    if len(merged) >= 2 and seg_len(*merged[-1]) < minc:
        a0, b0 = merged[-1]
        pa, pb = merged[-2]
        merged[-2] = (pa, b0)
        merged.pop()
    return merged


def starts_with_transition(text: str, rules: dict) -> bool:
    head = text[:14]
    for t in (rules.get("time_transitions") or []) + (rules.get("refocus_prefixes") or []):
        if t and head.startswith(t):
            return True
    return False


# ---------------------------------------------------------------- 本地打标

def _allow(dim: str, tag: str, gate) -> bool:
    """按用户库维度覆盖（gate）判断某维度/标签是否允许打。gate=None 表示不限制。"""
    if not gate:
        return True
    if dim in (gate.get("drop") or ()):
        return False
    only = gate.get("only")
    if only is not None and dim not in only:
        return False
    vals = (gate.get("values") or {}).get(dim)
    if vals and tag and tag not in vals:
        return False
    return True


# 人物数量：文本里的数量级词（须紧跟人数类词，避免「数千字/上百次」类误报）
_QTY_HUMANS = r"(?:人|众|军|兵|骑|百姓|观众|士兵|弟子|人马|大军|民众|群众|修士|高手|敌人|妖兽|妖怪|怪物)"
_QTY_SUFFIX = r"(?:余|多|来)?(?:名|个|号|只|头|匹|条)?的?" + _QTY_HUMANS
_QTY_MAG = (
    (re.compile(r"(?:成千上万|上万|数万|万余|逾万)" + _QTY_SUFFIX), 10000),
    (re.compile(r"(?:上千|数千|千余|逾千)" + _QTY_SUFFIX), 1000),
    (re.compile(r"(?:上百|数百|百余)" + _QTY_SUFFIX), 100),
)


def quantity_tag(n: int, text: str):
    """人物数量特征标签：<100 精确到个位「N人」；≥100 按数量级「上百人 / 上千人 / 上万人」。

    同时识别文本里的数量级词（如「数百名观众」→ 上百人），与已知人物数取较大者。
    返回 (标签, 数量, [命中词]) 或 None。
    """
    level, cue = 0, None
    for rx, lv in _QTY_MAG:
        m = rx.search(text)
        if m and lv > level:
            level, cue = lv, m.group(0)
    if level == 0 and n < 100:
        return (f"{n}人", n, []) if n > 0 else None
    if n >= 10000 or level >= 10000:
        return "上万人", max(n, level), ([cue] if cue else [])
    if n >= 1000 or level >= 1000:
        return "上千人", max(n, level), ([cue] if cue else [])
    return "上百人", max(n, level), ([cue] if cue else [])


def derive_scene_auto(taxonomy) -> dict | None:
    """从（合并后的）taxonomy 的 scene 值域推出两个通用场景规则（未声明则 None）：

    · xx / 非xx 成对声明（如 对话/非对话）→ 互斥：片段不会同时有两者；
      未命中 xx 的片段自动补 非xx（等价于无需再配 dialogue.negative_label）。
    · 值域里有「其他」（或「其它」）→ 兜底：没有任何场景标签的片段自动归入。
    """
    dim = ((taxonomy or {}).get("dimensions") or {}).get("scene") or {}
    vals = [str(v) for v in (dim.get("values") or [])] if isinstance(dim, dict) else []
    negs = [(v[1:], v) for v in vals if v.startswith("非") and len(v) > 1 and v[1:] in vals]
    catch = "其他" if "其他" in vals else ("其它" if "其它" in vals else None)
    if not negs and not catch:
        return None
    return {"negs": negs, "catch": catch}


# ===== 人物代词就近指代（本地消解，零 LLM） =====

PRON_DEFAULT = {"我": "pov", "我们": "pov", "他": "男", "他们": "男", "她": "女", "她们": "女"}


def char_gender(cdef: dict) -> str:
    """人物性别：优先 gender 字段；否则看标签里的 男/女（如 男1/女配2）。"""
    g = str((cdef or {}).get("gender") or "").strip()
    if g in ("男", "女"):
        return g
    tag = str((cdef or {}).get("role") or (cdef or {}).get("name") or "")
    if "男" in tag:
        return "男"
    if "女" in tag:
        return "女"
    return ""


def _entry_books_ok(cdef: dict, book) -> bool:
    """人物条目的 books 作用域：不填=全库生效；填了只在列出的书里生效（book 未知时放开）。"""
    bs = (cdef or {}).get("books") or ([cdef["book"]] if (cdef or {}).get("book") else [])
    if not bs or book is None:
        return True
    return str(book) in {str(b) for b in bs}


def scan_char_mentions(text: str, chars_cfg, book=None):
    """人物名字/别名的出现位置 → [(pos, role, gender, name)]（按位置排序）。"""
    out = []
    for cdef in (chars_cfg or {}).get("characters", []) or []:
        if not _entry_books_ok(cdef, book):
            continue
        role = cdef.get("role") or cdef.get("name") or ""
        if not role:
            continue
        gender = char_gender(cdef)
        names = [cdef.get("name", "")] + list(cdef.get("aliases") or [])
        names = [x for x in dict.fromkeys(names) if x]
        for nm in names:
            i = text.find(nm)
            while i >= 0:
                out.append((i, role, gender, nm))
                i = text.find(nm, i + len(nm))
    out.sort(key=lambda x: x[0])
    return out


def _pov_role(chars_cfg, book=None) -> str:
    """第一人称叙述者：characters.yaml 里条目 pov: true（可用 books 限定书），或顶层 pov: 名字/role。"""
    chars = (chars_cfg or {}).get("characters", []) or []
    for cdef in chars:
        if (cdef or {}).get("pov") and _entry_books_ok(cdef, book):
            return cdef.get("role") or cdef.get("name") or ""
    top = (chars_cfg or {}).get("pov")
    if top:
        for cdef in chars:
            cand = [cdef.get("name"), cdef.get("role")] + list(cdef.get("aliases") or [])
            if str(top) in {str(x) for x in cand if x}:
                return cdef.get("role") or cdef.get("name") or ""
    return ""


def pronoun_evidence(text: str, chars_cfg, rules: dict, mentions, chars_ctx=None, book=None):
    """代词就近指代 → {role: {"n": n, "kws": ["他（代词）", ...]}}。

    · 我/我们 → 第一人称叙述者（characters.yaml 标 pov），仅引号外的叙述行归属；
    · 他/她/他们/她们 → 性别匹配：先取「此前最近的提及」（本片段 + 跨片段回溯窗口），
      再看「本片段内其后的最近提及」；找不到就不归属。
    """
    pmap = ((rules.get("chars") or {}).get("pronouns")) or PRON_DEFAULT
    if not pmap or not chars_cfg or not text:
        return {}
    # 代词出现位置（长代词优先，避免「他们」里的「他」重复计入）
    occ, occupied = [], set()
    for pron in sorted(pmap, key=len, reverse=True):
        i = text.find(pron)
        while i >= 0:
            if i not in occupied:
                occ.append((i, pron))
                occupied.update(range(i, i + len(pron)))
            i = text.find(pron, i + 1)
    if not occ:
        return {}
    occ.sort()
    base = (chars_ctx or {}).get("base", 0)
    window = int((rules.get("chars") or {}).get("carry_window") or 3000)
    carry = [(p, r, g) for (p, r, g) in ((chars_ctx or {}).get("recent") or []) if p >= base - window]
    pov = _pov_role(chars_cfg, book)
    qspans = C.quote_spans(text, rules.get("quote_pairs")) if pov else []

    def in_quote(pos):
        return any(a <= pos < b for a, b in qspans)

    out = {}
    for pos, pron in occ:
        cls = str(pmap.get(pron) or "")
        if cls == "pov":
            if not pov or in_quote(pos):
                continue
            target = pov
        elif cls in ("男", "女"):
            gpos = base + pos
            cands = [(base + p, r) for (p, r, g, _nm) in mentions if g == cls]
            cands += [(p, r) for (p, r, g) in carry if g == cls]
            before = [c for c in cands if c[0] < gpos]
            if before:
                target = max(before, key=lambda c: c[0])[1]
            else:
                after = [(base + p, r) for (p, r, g, _nm) in mentions if g == cls and base + p > gpos]
                if not after:
                    continue
                target = min(after, key=lambda c: c[0])[1]
        else:
            continue
        rec = out.setdefault(target, {"n": 0, "kws": []})
        rec["n"] += 1
        kw = f"{pron}（代词）"
        if kw not in rec["kws"]:
            rec["kws"].append(kw)
    return out


def chars_ctx_advance(text: str, mentions, chars_ctx, rules: dict):
    """把本片段的提及并入跨片段指代上下文（供后续片段开头的代词就近归属）。"""
    if chars_ctx is None:
        return
    base = chars_ctx.get("base", 0)
    recent = chars_ctx.setdefault("recent", [])
    for p, r, g, _nm in mentions:
        recent.append((base + p, r, g))
    window = int((rules.get("chars") or {}).get("carry_window") or 3000)
    end = base + len(text)
    chars_ctx["recent"] = [m for m in recent if m[0] >= end - window][-120:]
    chars_ctx["base"] = end + 1


def tag_fragment(text: str, rules: dict, keywords: dict, chars_cfg, terms=None, gate=None,
                 scene_auto=None, chars_ctx=None, book=None):
    """返回 (tags, dialogue_stats, hits)。

    tags  = {维度: [标签...]}
    hits  = {"维度:标签": {"n": 命中次数, "kws": [命中词...], "rule": True?}}
    terms = mine_terms.py 提炼的本书词条列表（可选，命中则加 term 维度标签）
    gate  = 用户库维度覆盖（taxonomy override 产物，None=默认）
    scene_auto = 场景通用规则（xx/非xx 互斥+补全、其他兜底；见 derive_scene_auto）
    chars_ctx  = 跨片段代词指代上下文（{"base": int, "recent": [(pos, role, gender)]}，调用方维护）
    book   = 当前书名（人物条目 books 作用域 / pov 限定用；None=不限定）
    """
    pairs = rules.get("quote_pairs") or None
    ds = C.quote_stats(text, pairs)
    dthr = rules.get("dialogue", {}) or {}
    is_dialog = (ds["ratio"] >= float(dthr.get("ratio_threshold", 0.38))
                 or ds["para_ratio"] >= float(dthr.get("para_threshold", 0.5)))

    tags: dict[str, list[str]] = {}
    hits: dict[str, dict] = {}

    # 场景：对话 / 负标签（规则；标签名可在 rules 的 dialogue.label / negative_label 配置）
    dlabel = dthr.get("label") or "对话"
    dneg = dthr.get("negative_label") or ""
    if is_dialog and _allow("scene", dlabel, gate):
        tags.setdefault("scene", []).append(dlabel)
        hits[f"scene:{dlabel}"] = {"rule": True, "n": ds["ratio"], "kws": []}
    elif not is_dialog and dneg and _allow("scene", dneg, gate):
        tags.setdefault("scene", []).append(dneg)
        hits[f"scene:{dneg}"] = {"rule": True, "n": ds["ratio"], "kws": []}

    # 场景：转场（规则；标签名可在 rules 的 transition.label 配置）
    tlabel = (rules.get("transition") or {}).get("label", "转场")
    if tlabel and starts_with_transition(text, rules) and _allow("scene", tlabel, gate):
        tags.setdefault("scene", []).append(tlabel)
        hits[f"scene:{tlabel}"] = {"rule": True, "n": 1, "kws": []}

    # 关键词维度（scene / plot / style 以及用户新增维度）
    for dim, spec_map in (keywords or {}).items():
        for tag, spec in (spec_map or {}).items():
            if not _allow(dim, str(tag), gate):
                continue
            if isinstance(spec, dict):
                kws = list(spec.get("keywords") or [])
                thr = int(spec.get("threshold", 2))
            else:
                kws = list(spec or [])
                thr = 2
            matched = []
            cnt = 0
            for k in kws:
                c = text.count(k)
                if c:
                    cnt += c
                    matched.append(k)
            if cnt >= thr:
                tags.setdefault(dim, [])
                if tag not in tags[dim]:
                    tags[dim].append(tag)
                hits[f"{dim}:{tag}"] = {"n": cnt, "kws": matched[:8]}

    # 关键词（内置特征，默认开启，不受维度覆盖影响；--no-terms 关闭）
    if terms:
        picked = []
        for t in sorted(terms, key=lambda x: -(x.get("freq", 0) if isinstance(x, dict) else 0)):
            w = t.get("term") if isinstance(t, dict) else str(t)
            if not w or len(w) < 2 or w not in text:
                continue
            if any((w in p) or (p in w) for p in picked):  # 互相包含时只留先命中的
                continue
            picked.append(w)
            if len(picked) >= 6:
                break
        if picked:
            tags.setdefault("term", [])
            tags["term"].extend(picked)
            for w in picked:
                hits[f"term:{w}"] = {"n": text.count(w), "kws": [w]}

    # 人物（人物ID：名字/别名 + 代词就近指代；人物数量）
    mentions = scan_char_mentions(text, chars_cfg, book)
    char_kws = {}
    for pos, role, gender, nm in mentions:
        rec = char_kws.setdefault(role, {"n": 0, "kws": []})
        rec["n"] += 1
        if nm not in rec["kws"] and len(rec["kws"]) < 8:
            rec["kws"].append(nm)
    # 代词指代（本地就近消解，零 LLM；不会重复计入名字命中的部分）
    for role, info in pronoun_evidence(text, chars_cfg, rules, mentions, chars_ctx, book).items():
        rec = char_kws.setdefault(role, {"n": 0, "kws": []})
        rec["n"] += info["n"]
        for kw in info["kws"]:
            if kw not in rec["kws"]:
                rec["kws"].append(kw)
    chars_ctx_advance(text, mentions, chars_ctx, rules)
    roles = [r for r in char_kws if _allow("chars", r, gate)]
    if roles:
        tags.setdefault("chars", [])
        tags["chars"].extend(roles)
        for role in roles:
            hits[f"chars:{role}"] = char_kws[role]
    qty = quantity_tag(len(roles), text)
    if qty and _allow("chars", qty[0], gate):
        tags.setdefault("chars", []).append(qty[0])
        hits[f"chars:{qty[0]}"] = {"rule": True, "n": qty[1], "kws": qty[2]}

    # 场景通用规则（由 scene 值域声明驱动）：xx/非xx 互斥+补全、其他兜底
    if scene_auto:
        stags = list(tags.get("scene") or [])
        for base, neg in scene_auto.get("negs") or []:
            if base in stags:
                if neg in stags:  # 互斥：命中 xx 时去掉 非xx
                    stags = [t for t in stags if t != neg]
                    hits.pop(f"scene:{neg}", None)
            elif neg not in stags and _allow("scene", neg, gate):
                stags.append(neg)
                hits[f"scene:{neg}"] = {"rule": True, "n": 0, "kws": []}
        catch = scene_auto.get("catch")
        if catch and not stags and _allow("scene", catch, gate):
            stags.append(catch)
            hits[f"scene:{catch}"] = {"rule": True, "n": 0, "kws": []}
        if stags:
            tags["scene"] = stags

    tags = {k: list(dict.fromkeys(v)) for k, v in tags.items() if v}
    return tags, ds, hits


# ---------------------------------------------------------------- 处理主流程

def _want_terms(gate) -> bool:
    """关键词提炼是否开启：内置默认特性，不受维度覆盖影响（关闭用 --no-terms）。"""
    return True


def auto_mine_book(lib, book, pairs, gate):
    """拆书默认步骤：书还没有词条时自动做一次新词发现并写入 terms.json。

    pairs = [(章号, 章正文), ...]；不满足条件时返回 None。
    已提炼过的书（terms.json 有该书记录）不会重跑 —— 用户修剪过的词表不会被覆盖。
    """
    if not _want_terms(gate):
        return None
    total = sum(len(t) for _, t in pairs)
    if total < 500:
        return None
    exclude = MT.load_exclude_names(lib)
    params = MT.auto_params(total)
    terms = MT.mine_book(pairs, exclude, params)
    MT.save_book_terms(lib, book, terms, params, source="auto")
    print(f"[{book}] 自动提炼词条 {len(terms)} 条（新词发现，零 LLM）")
    return terms


def process_file(fp, book: str, args, rules, keywords, chars_cfg, existing_ids, terms=None, gate=None, scene_auto=None):
    text = C.read_text(fp)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    book_slug = C.slugify(book)
    chapters = split_chapters(text, rules)
    if terms is None and not getattr(args, "no_terms", False):
        terms = auto_mine_book(Path(args.lib), book,
                               [(ch["no"], ch["body"]) for ch in chapters], gate)
    marker_res = [re.compile(p) for p in (rules.get("break_markers") or [])]
    new_rows, stats = [], {"chapters": len(chapters), "kept": 0, "skipped": 0}

    g_offset = 0
    for ch in chapters:
        cno, ctitle, body = ch["no"], ch["title"], ch["body"]
        paras = []
        for raw in body.split("\n"):
            s = raw.strip()
            if not s:
                continue
            if s.startswith("<a id=") or s.startswith("<!--"):
                continue
            paras.append(s)
        if not paras:
            g_offset += len(body) + 1
            continue

        groups = split_scenes(paras, rules)
        chars_ctx = {"base": 0, "recent": []}  # 代词指代上下文（每章重置）
        frag_no = 0
        for a, b in groups:
            frag_paras = [paras[i] for i in range(a, b) if not _is_marker(paras[i], marker_res)]
            frag_text = "\n".join(frag_paras).strip()
            if not frag_text:
                continue
            frag_no += 1
            fid = f"{book_slug}-c{cno:03d}-f{frag_no:03d}"
            if fid in existing_ids:
                stats["skipped"] += 1
                chars_ctx_advance(frag_text, scan_char_mentions(frag_text, chars_cfg, book), chars_ctx, rules)
                continue
            tags, ds, hits = tag_fragment(frag_text, rules, keywords, chars_cfg, terms, gate,
                                          scene_auto=scene_auto, chars_ctx=chars_ctx, book=book)
            if args.only_dims:
                keep = set(args.only_dims)
                tags = {k: v for k, v in tags.items() if k in keep}
                hits = {k: v for k, v in hits.items() if k.split(":", 1)[0] in keep}
            start = g_offset + sum(len(paras[i]) + 1 for i in range(a))
            new_rows.append({
                "id": fid, "book": book, "chapter": cno, "chapter_title": ctitle,
                "frag_no": frag_no, "start": start, "end": start + len(frag_text),
                "n_chars": len(frag_text), "dialogue_ratio": ds["ratio"],
                "tags": tags, "hits": hits,
                "source": {k: "rule" for k in tags},
                "llm_done": False, "text": frag_text,
            })
            stats["kept"] += 1
        g_offset += len(body) + 1
    return new_rows, stats


def retag_rows(rows, rules, keywords, chars_cfg, only_dims=None, terms_map=None, gate=None, scene_auto=None):
    """只重打标签：本地标签重算；LLM 打过的维度保留（取并集）。"""
    n = 0
    ctxs = {}
    for r in rows:
        old_tags = r.get("tags") or {}
        old_src = r.get("source") or {}
        llm_vals = {k: list(v) for k, v in old_tags.items() if "llm" in str(old_src.get(k, ""))}
        ctx = ctxs.setdefault((r.get("book"), r.get("chapter")), {"base": 0, "recent": []})
        new_tags, ds, hits = tag_fragment(r.get("text", ""), rules, keywords, chars_cfg,
                                          (terms_map or {}).get(r["book"]), gate,
                                          scene_auto=scene_auto, chars_ctx=ctx, book=r.get("book"))
        merged = {}
        for k in set(new_tags) | set(llm_vals):
            vals = list(dict.fromkeys((new_tags.get(k) or []) + (llm_vals.get(k) or [])))
            if vals:
                merged[k] = vals
        if only_dims:
            keep = set(only_dims)
            prev = {k: v for k, v in old_tags.items() if k not in keep}
            cur = {k: v for k, v in merged.items() if k in keep}
            merged = {**prev, **cur}
        src = {}
        for k in merged:
            in_llm = k in llm_vals
            in_rule = k in new_tags
            src[k] = "rule+llm" if (in_llm and in_rule) else ("llm" if in_llm else "rule")
        r["tags"] = merged
        r["hits"] = hits
        r["dialogue_ratio"] = ds["ratio"]
        r["source"] = src
        n += 1
    return n


def print_lib_summary(rows, show_terms=False):
    """结尾汇总。默认遮挡 term（自动词条）词面：避免书内敏感词进入模型上下文触发安全过滤。"""
    if not rows:
        return
    by_book = defaultdict(lambda: [0, 0, set()])
    dim_counter = Counter()
    terms = set()
    for r in rows:
        b = by_book[r["book"]]
        b[0] += 1
        b[1] += 0 if r.get("llm_done") else 1
        b[2].add(r.get("chapter") or 0)
        for dim, vals in (r.get("tags") or {}).items():
            for v in vals:
                if dim == "term":
                    terms.add(v)
                    if not show_terms:
                        continue
                dim_counter[f"{dim}:{v}"] += 1
    print("书目统计：")
    for book, (frags, pend, chs) in by_book.items():
        print(f"  《{book}》 章节 {len(chs)} ｜ 片段 {frags} ｜ 待LLM {pend}")
    top = dim_counter.most_common(12)
    if top:
        print("标签分布（Top12）：" + " ｜ ".join(f"{k} {v}" for k, v in top))
    if terms and not show_terms:
        print(f"自动词条：{len(terms)} 个（词面默认不回显；要看得加 --show-terms，或直接看 terms.json）")


def main():
    C.setup_console()
    ap = argparse.ArgumentParser(description="小说片段切分 + 本地多维打标（零 LLM）")
    ap.add_argument("files", nargs="*", help="要处理的小说文件（.md/.txt，可多个）")
    ap.add_argument("--lib", default="taglib", help="素材库目录（默认 ./taglib）")
    ap.add_argument("--book-name", default=None, help="书名（默认取文件名）")
    ap.add_argument("--characters", default=None, help="人物表路径（默认 <lib>/characters.yaml）")
    ap.add_argument("--config-dir", default=None, help="标准库目录（默认技能自带 config/）")
    ap.add_argument("--force", action="store_true", help="重切+重标（丢弃这些书的旧片段）")
    ap.add_argument("--retag", action="store_true", help="只重打标签，不重新切分")
    ap.add_argument("--only-dims", default=None, help="配合 --retag：只重算这些维度，如 scene,plot")
    ap.add_argument("--min-chars", type=int, default=None, help="片段最小字数（覆盖规则）")
    ap.add_argument("--max-chars", type=int, default=None, help="片段最大字数（覆盖规则）")
    ap.add_argument("--no-terms", action="store_true", help="关闭关键词提炼（默认开启）：不自动提炼、不使用 terms.json")
    ap.add_argument("--show-terms", action="store_true",
                    help="结尾汇总里显示自动词条（term）词面（默认遮挡：避免书内敏感词进入模型上下文）")
    args = ap.parse_args()

    lib = Path(args.lib)
    lib.mkdir(parents=True, exist_ok=True)
    cfg_dir = Path(args.config_dir) if args.config_dir else DEFAULT_CFG
    rules, keywords, taxonomy, lib_info = C.load_libraries(cfg_dir, lib)
    gate = lib_info.get("dim_gate")
    scene_auto = derive_scene_auto(taxonomy)
    if args.min_chars:
        rules.setdefault("fragment", {})["min_chars"] = args.min_chars
    if args.max_chars:
        rules.setdefault("fragment", {})["max_chars"] = args.max_chars
    only_dims = [d.strip() for d in (args.only_dims or "").split(",") if d.strip()]
    args.only_dims = only_dims or None

    chars_cfg = None
    chars_path = Path(args.characters) if args.characters else lib / "characters.yaml"
    if chars_path.exists():
        chars_cfg = C.load_config(chars_path)
    elif (lib / "characters.json").exists():
        chars_cfg = C.load_config(lib / "characters.json")

    terms_map = {} if args.no_terms else C.load_terms(lib)

    jl = lib / "fragments.jsonl"
    all_rows = list(C.iter_jsonl(jl))

    if args.retag:
        if not all_rows:
            C.die(f"{jl} 为空，先运行：python segment.py <小说文件> --lib {lib}")
        if not args.no_terms:
            # 库里还没有词条的书（早于本功能拆的）→ 自动补提炼
            missing = {r.get("book") for r in all_rows} - set(terms_map)
            for book in sorted(b for b in missing if b):
                chmap = {}
                for r in all_rows:
                    if r.get("book") == book:
                        chmap.setdefault(r.get("chapter") or 0, []).append(r.get("text") or "")
                pairs = [(cno, "\n".join(v)) for cno, v in sorted(chmap.items())]
                terms = auto_mine_book(lib, book, pairs, gate)
                if terms is not None:
                    terms_map[book] = terms
        n = retag_rows(all_rows, rules, keywords, chars_cfg, only_dims or None, terms_map, gate, scene_auto)
        C.write_jsonl(jl, all_rows)
        total = C.build_db(lib)
        print(f"[retag] 已重打标签 {n} 个片段，库内共 {total} 个")
        print_lib_summary(all_rows, show_terms=args.show_terms)
        return

    if not args.files:
        C.die("请给出要拆解的小说文件，或加 --retag 重打标签")

    for fp in args.files:
        fp = Path(fp)
        if not fp.exists():
            print(f"[跳过] 文件不存在：{fp}")
            continue
        book = args.book_name or fp.stem
        if args.force:
            before = len(all_rows)
            all_rows = [r for r in all_rows if r["book"] != book]
            if before != len(all_rows):
                print(f"[force] 已清除《{book}》旧片段 {before - len(all_rows)} 个")
        existing_ids = {r["id"] for r in all_rows}
        new_rows, stats = process_file(fp, book, args, rules, keywords, chars_cfg, existing_ids,
                                       terms_map.get(book), gate, scene_auto)
        all_rows.extend(new_rows)
        print(f"[{book}] 章节 {stats['chapters']} 个 → 新增片段 {stats['kept']} 个，跳过已存在 {stats['skipped']} 个")

    C.write_jsonl(jl, all_rows)
    total = C.build_db(lib)
    print(f"\n已写入 {jl}（库内共 {total} 个片段）")
    print(f"标准库: {lib_info['standard_dir']}")
    if lib_info["user_files"]:
        print("用户库: " + "；".join(lib_info["user_files"]))
    else:
        print("用户库: 未创建（= 默认全部维度）")
        print("提示: 想定制「拆哪些维度/标签」（覆盖或增加）→ 直接告诉 AI，或运行：python setup_dims.py --lib " + str(lib) + " --ask")
    if gate and gate.get("only") is not None:
        print("维度覆盖: taxonomy override 生效 → 仅打标维度 " + ",".join(gate["only"]))
    elif gate:
        parts = []
        if gate.get("values"):
            parts.append("严格值域 " + ",".join(gate["values"].keys()))
        if gate.get("drop"):
            parts.append("停用维度 " + ",".join(gate["drop"]))
        print("维度覆盖: " + "；".join(parts))
    if not chars_cfg:
        print("提示: 人物表未创建 → python bootstrap_chars.py <小说文件> --write <lib>/characters.yaml")
    if terms_map:
        print(f"词条库: terms.json 已加载（{len(terms_map)} 本书，共 {sum(len(v) for v in terms_map.values())} 条词）")
    elif not args.no_terms:
        print("提示: 关键词提炼默认开启（书内专有词，零 LLM）；拆解新书时自动提炼，也可手动跑 mine_terms.py")
    print("提示: 语义补标（可选；批次含正文，敏感内容可能被模型安全过滤拦截——被拦跳过即可）→ python todo_llm.py --lib " + str(lib))
    print_lib_summary(all_rows, show_terms=args.show_terms)


if __name__ == "__main__":
    main()
