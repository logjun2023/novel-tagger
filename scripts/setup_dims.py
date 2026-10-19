#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""setup_dims.py — 拆书前的「维度/标签/覆盖方式」配置向导（生成用户库文件）。

三种用法：
  1) 查看可选维度与标签（给 AI 或人看）：
     python setup_dims.py --lib taglib --list
  2) 按计划文件生成（推荐：AI 问完用户后写 plan.json）：
     python setup_dims.py --lib taglib --plan plan.json
  3) 终端交互问答（人在终端里用）：
     python setup_dims.py --lib taglib --ask
  快捷预设：
     python setup_dims.py --lib taglib --preset dialogue-only    # 只按 对话/非对话 拆书

说明：
  · 问答分两步：先选维度，再按所选维度逐个问标签（展示该维度默认标签）。
  · 关键词提炼是内置特征：默认开启、不用问（关闭用 segment.py --no-terms）。
  · chars（人物）自动生成两类特征：人物ID + 人物数量（<100 精确「N人」；≥100 按数量级），无需选标签。

plan.json 格式：
{
  "mode": "override",            // override=覆盖（只用选的）；merge=增加（在默认基础上加）
  "dimensions": {
    "scene":   {"tags": ["对话", "战斗"]},        // tags 省略 = 该维度默认标签全保留
    "chars":   {},                               // 人物维度（值自由，由人物表决定）
    "emotion": {"tags": ["愤怒", "开心"]}         // 自定义维度
  },
  "keywords": {                  // 新标签配词（老标签会自动带上标准库的现成词）
    "emotion.愤怒": ["怒", "吼", "咬牙"],
    "emotion.开心": {"words": ["笑", "开心"], "threshold": 1}
  },
  "dialogue": {"label": "对话", "negative_label": "非对话"},   // 可选；scene 要 对话+非对话 时自动推断
  "rules": {"fragment": {"max_chars": 1500}}                   // 可选：其它规则覆盖
}

生成的文件（写前自动把同名旧文件备份为 .bak）：
  <lib>/taxonomy.user.yaml / keywords.user.yaml / rules.user.yaml
之后：
  python segment.py --lib <lib> --retag        # 已有片段重打标签
  python segment.py 某书.md --lib <lib>        # 开始拆新书
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from copy import deepcopy
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _common as C

DEFAULT_CFG = Path(__file__).resolve().parent.parent / "config"

PRESETS = {
    "dialogue-only": {
        "mode": "override",
        "dimensions": {"scene": {"tags": ["对话", "非对话"], "method": ["rule"]}},
        "dialogue": {"label": "对话", "negative_label": "非对话"},
    },
}

MODE_ALIASES = {
    "覆盖": "override", "override": "override", "overwrite": "override", "replace": "override",
    "增加": "merge", "追加": "merge", "merge": "merge", "add": "merge",
}


def norm_mode(v) -> str:
    m = MODE_ALIASES.get(str(v or "").strip().lower())
    if not m:
        C.die(f"mode 只能是 override（覆盖）或 merge（增加），收到：{v!r}")
    return m


def split_names(s) -> list[str]:
    return [x.strip() for x in re.split(r"[，,、]", str(s or "")) if x.strip()]


def dim_alias(tax: dict) -> dict:
    """维度键/中文标签 → 维度键。"""
    m = {}
    for d, spec in ((tax.get("dimensions")) or {}).items():
        m[d] = d
        lb = (spec or {}).get("label")
        if lb:
            m[str(lb)] = d
    return m


def resolve_plan(plan: dict, tax: dict, kw: dict):
    """plan → (taxonomy_user, keywords_user, rules_user, info)"""
    mode = norm_mode(plan.get("mode"))
    std_dims = tax.get("dimensions") or {}
    dims_out = {}
    for dim, spec in (plan.get("dimensions") or {}).items():
        spec = spec or {}
        std = std_dims.get(dim) or {}
        d = {"label": spec.get("label") or std.get("label") or dim}
        tags = spec.get("tags")
        if isinstance(tags, str):
            tags = split_names(tags)
        if tags is None and mode == "override" and std:
            tags = list(std.get("values") or [])
        if tags is not None:
            d["values"] = [str(t).strip() for t in tags if str(t).strip()]
        elif mode == "override":
            d["values"] = []
        if dim == "scene" and d.get("values") is not None:
            # 「其他」默认必含：未分类片段兜底；有 非xx 时它自然不命中（无需特殊处理）
            if "其他" not in d["values"] and "其它" not in d["values"]:
                d["values"].append("其他")
        d["method"] = list(spec.get("method") or std.get("method") or ["keyword"])
        if spec.get("desc"):
            d["desc"] = spec["desc"]
        dims_out[dim] = d

    # 关键词：先带上标准库里「选中标签」的现成词，再叠加 plan 自定义词
    kws_out = {}
    for dim, d in dims_out.items():
        for tag in (d.get("values") or []):
            spec_kw = (kw.get(dim) or {}).get(tag)
            if isinstance(spec_kw, dict):
                kws_out.setdefault(dim, {})[tag] = deepcopy(spec_kw)
    for key, val in (plan.get("keywords") or {}).items():
        if "." not in str(key):
            print(f"[跳过] keywords 键应为「维度.标签」格式：{key}")
            continue
        dim, tag = str(key).split(".", 1)
        if isinstance(val, dict):
            words = val.get("words") or val.get("keywords") or []
            spec = {"keywords": [str(w).strip() for w in words if str(w).strip()]}
            if "threshold" in val:
                spec["threshold"] = int(val["threshold"])
        else:
            spec = {"keywords": [str(w).strip() for w in (val or []) if str(w).strip()]}
        if spec["keywords"]:
            kws_out.setdefault(dim, {})[tag] = spec

    # 对话规则：显式给用显式的；scene 同时要 对话+非对话 时自动推断
    dialogue = plan.get("dialogue")
    auto_dialogue = False
    if not dialogue:
        svals = (dims_out.get("scene") or {}).get("values") or []
        if "对话" in svals and "非对话" in svals:
            dialogue = {"label": "对话", "negative_label": "非对话"}
            auto_dialogue = True

    if mode == "override":
        tax_user = {"override": "all", "dimensions": dims_out,
                    "llm_dimensions": [d for d, s in dims_out.items() if "llm" in (s.get("method") or [])]}
        kw_user = {"override": "all"}
        kw_user.update(kws_out)
    else:
        tax_user = {"dimensions": dims_out}
        kw_user = kws_out or None

    rules_user = None
    if dialogue:
        if mode == "override":
            rules_user = {"override": ["dialogue"], "dialogue": dict(dialogue)}
        else:
            rules_user = {"dialogue": dict(dialogue)}
    if plan.get("rules"):
        extra = deepcopy(plan["rules"])
        if rules_user is None:
            rules_user = extra
        else:
            rules_user.update(extra)
    info = {"mode": mode, "dims": dims_out, "auto_dialogue": auto_dialogue}
    return tax_user, kw_user, rules_user, info


def write_user_files(lib, files, dry: bool = False) -> list[str]:
    written = []
    for name, obj in files:
        if obj is None:
            continue
        p = Path(lib) / name
        if dry:
            written.append(str(p) + "（dry-run）")
            continue
        if p.exists():
            bak = p.with_name(p.name + ".bak")
            if not bak.exists():
                bak.write_text(p.read_text(encoding="utf-8"), encoding="utf-8")
        C.save_config(p, obj)
        written.append(str(p))
    return written


def _method_names(methods) -> str:
    m = {"rule": "规则", "keyword": "词库", "llm": "LLM补标", "mine": "自动提炼"}
    return "+".join(m.get(x, str(x)) for x in (methods or [])) or "?"


def cmd_list(lib, tax: dict, kw: dict, human: bool = False):
    dims = {}
    for d, spec in (tax.get("dimensions") or {}).items():
        spec = spec or {}
        dims[d] = {
            "label": spec.get("label") or d,
            "values": spec.get("values") or [],
            "method": spec.get("method") or [],
            "keyword_tags": sorted((kw.get(d) or {}).keys()),
        }
    existing = [f for f in ("taxonomy.user.yaml", "keywords.user.yaml", "rules.user.yaml",
                            "characters.yaml", "terms.json")
                if (Path(lib) / f).exists()]

    if human:
        print("===== 默认维度（不配置 = 全部开启；可只挑几个）=====")
        for d, info in ((d, x) for d, x in dims.items() if d != "term"):
            if d == "chars":
                lbl = "自动：人物ID（来自人物表）+ 人物数量（<100 精确「N人」；≥100 按数量级）"
            else:
                lbl = "、".join(info["values"]) if info["values"] else "自由值"
            print(f"[{d}] {info['label']} · 默认标签：{lbl} · 来源：{_method_names(info['method'])}")
        print("（内置特征：关键词提炼默认开启——书内专有词自动提炼，不用选、不用配；关闭用 --no-terms）")
        print("")
        print("===== 无需配置的自动标签 =====")
        print("scene：对话（对白占比规则）；转场（片段以时间/转场词开头自动打）")
        print("chars：人物ID（人物表 role，如 男1/女2）+ 人物数量（<100 精确「N人」；≥100 按数量级 上百人/上千人/上万人）")
        print("term：关键词（内置，默认开启；从书里自动提炼）")
        print("")
        print("===== 本库当前配置 =====")
        print("文件：" + ("、".join(existing) if existing else "无"))
        tp = Path(lib) / "taxonomy.user.yaml"
        if tp.exists():
            try:
                tu = C.load_config(tp) or {}
            except Exception:
                tu = {}
            ov = C._norm_override(tu.get("override"))
            if ov == "all":
                mode_txt = "覆盖模式 → 只打标下面列出的维度"
            elif isinstance(ov, list):
                mode_txt = "局部覆盖：" + "、".join(ov)
            else:
                mode_txt = "合并模式（在默认基础上增加）"
            dims_x = tu.get("dimensions") or {}
            if dims_x:
                mode_txt += "；维度：" + "、".join(
                    f"{d}[{'/'.join((s or {}).get('values') or []) or '自由值'}]" for d, s in dims_x.items())
            print(mode_txt)
        else:
            print("未配置（全部维度默认开启）")
        print("")
        print("===== 建议问法（先给用户看默认值；两步问）=====")
        print("1) 要哪些维度？（默认全要：场景/人物/类型/描写；关键词是内置特征不用问；可只挑几个）")
        print("2) 用户选完维度后，再逐个问所选维度的标签：展示该维度默认标签，可只留部分或自定义；人物维度跳过（自动 人物ID+人物数量）")
        print("3) 覆盖还是增加？（覆盖=只用所选的；增加=在默认基础上加）")
        return

    out = {
        "可选维度": {d: x for d, x in dims.items() if d != "term"},
        "默认特性": {
            "关键词提炼": "内置特征，默认开启、无需选择：拆书时自动提炼书内专有词（零 LLM）；关闭用 segment.py --no-terms",
        },
        "自动标签": {
            "scene": ["对话（对白占比规则 rules.dialogue）",
                      "转场（rules.transition.label，命中时间/转场词自动打）"],
            "chars": ["人物ID：人物表 characters.yaml 的 role/名字（自动）",
                      "人物数量：<100 精确「N人」；≥100 按数量级 上百人/上千人/上万人（自动）"],
            "term": ["关键词：默认开启的内置特征，词条从书里自动提炼"],
        },
        "预设": sorted(PRESETS),
        "本库已有文件": existing,
        "提示": ("先问维度、再按所选维度逐个问标签（展示默认值）；mode: override=覆盖 / merge=增加；"
                 "不运行本脚本 = 全部维度默认开启"),
    }
    print(json.dumps(out, ensure_ascii=False, indent=2))


def cmd_ask(lib, tax: dict, kw: dict):
    std_dims = tax.get("dimensions") or {}
    selectable = {d: (s or {}) for d, s in std_dims.items() if d != "term"}
    alias = dim_alias(tax)

    def ask_line(prompt: str) -> str:
        try:
            return input(prompt).strip()
        except EOFError:
            return ""

    print("== 拆书维度设置（回车走默认值） ==")
    print("可选维度（默认全要，可只挑几个）：")
    for d, spec in selectable.items():
        if d == "chars":
            vals = "自动：人物ID（人物表）+ 人物数量（<100 精确、≥100 数量级），无需选标签"
        else:
            vals = "、".join(spec.get("values") or []) or "自由值（如人物名）"
        print(f"  {d}（{spec.get('label') or d}）：{vals}")
    print("（关键词提炼是内置特征：默认开启、不用选）")
    s1 = ask_line("【1/4】要哪些维度？逗号分隔；可写 自定义维度名:标签1,标签2；回车=全部默认： ")
    default_all = not s1
    dims = {}
    if default_all:
        for d in selectable:
            dims[d] = {}
    else:
        for part in split_names(s1):
            if ":" in part or "：" in part:
                name, tags = re.split(r"[:：]", part, 1)
                key = alias.get(name.strip(), name.strip())
                dims[key] = {"tags": split_names(tags)}
            else:
                dims[alias.get(part, part)] = {}
    if "term" in dims:
        dims.pop("term")
        print("（关键词提炼是内置特征：默认开启，已忽略对 term 的选择）")
    # 用户选完维度后，逐个维度问标签（展示该维度默认标签）；人物维度不用问
    for d in list(dims):
        std = selectable.get(d) or {}
        if d == "chars":
            print(f"【2/4】{d}（人物）：自动生成 人物ID + 人物数量，无需选标签")
            continue
        if dims[d].get("tags"):
            continue  # 输入维度时就给了自定义标签
        vals = std.get("values") or []
        tip = "、".join(vals) if vals else "自由值"
        s2 = ask_line(f"【2/4】{d}（{std.get('label') or d}）的标签？默认：{tip}；回车=默认，或逗号分隔自定义： ")
        if s2:
            dims[d]["tags"] = split_names(s2)
    s3 = ask_line("【3/4】覆盖还是增加？1=覆盖（推荐：只用上面选的）2=增加（在默认基础上加）[1]： ")
    mode = "merge" if s3 == "2" else "override"
    kws = {}
    s4 = ask_line("【4/4】给新标签配关键词？格式 维度.标签=词1,词2（分号分隔多条），回车跳过： ")
    if s4:
        for seg in re.split(r"[;；]", s4):
            seg = seg.strip()
            if not seg or "=" not in seg:
                continue
            key, words = seg.split("=", 1)
            kws[key.strip()] = split_names(words)
    if default_all and not any("tags" in v for v in dims.values()) and not kws:
        return None  # 与默认完全一致，不必生成文件
    return {"mode": mode, "dimensions": dims, "keywords": kws}


def main():
    C.setup_console()
    ap = argparse.ArgumentParser(description="拆书前配置「要哪些维度/标签、覆盖还是增加」")
    ap.add_argument("--lib", required=True, help="素材库目录（如 taglib）")
    ap.add_argument("--list", action="store_true", help="列出可选维度与标签（默认 JSON；加 --human 给人看）")
    ap.add_argument("--human", action="store_true", help="--list 时输出中文清单：默认维度 + 每维默认标签 + 本库现状 + 建议问法")
    ap.add_argument("--plan", default=None, help="按 plan.json / plan.yaml 生成配置")
    ap.add_argument("--preset", default=None, choices=sorted(PRESETS), help="快捷预设")
    ap.add_argument("--ask", action="store_true", help="终端交互问答（人用）")
    ap.add_argument("--dry-run", action="store_true", help="只预览将写入什么")
    args = ap.parse_args()

    lib = Path(args.lib)
    tax = C.load_config(DEFAULT_CFG / "taxonomy.yaml")
    kw = C.load_config(DEFAULT_CFG / "keywords.yaml")

    if args.list:
        cmd_list(lib, tax, kw, human=args.human)
        return

    if args.preset:
        plan = deepcopy(PRESETS[args.preset])
    elif args.plan:
        plan = C.load_config(args.plan)
    elif args.ask:
        plan = cmd_ask(lib, tax, kw)
        if plan is None:
            print("你的选择与默认一致，无需生成配置文件")
            return
    else:
        C.die("用法：--list 查看选项 / --plan plan.json 按计划生成 / --preset 预设 / --ask 交互问答")

    if not isinstance(plan, dict):
        C.die("plan 内容不是有效对象（应为 {mode, dimensions, keywords...}）")
    tax_user, kw_user, rules_user, info = resolve_plan(plan, tax, kw)
    files = [("taxonomy.user.yaml", tax_user), ("keywords.user.yaml", kw_user), ("rules.user.yaml", rules_user)]
    written = write_user_files(lib, files, dry=args.dry_run)

    print(f"模式: {'覆盖（只用你选的）' if info['mode'] == 'override' else '增加（在默认基础上加）'}")
    dims_txt = "、".join(
        f"{d}[{'/'.join(s.get('values') or []) or '自由值'}]" for d, s in info["dims"].items()
    ) or "（无）"
    print(f"维度: {dims_txt}")
    if info["auto_dialogue"]:
        print("已自动设置：对话规则输出「对话 / 非对话」")
    if not info["dims"]:
        print("警告: 一个维度都没选 → 覆盖模式下所有维度关闭，片段将没有任何标签")

    missing = []
    custom_kw = set(plan.get("keywords") or {})
    for d, s in info["dims"].items():
        if "keyword" not in (s.get("method") or []) or d in ("chars", "term"):
            continue
        vals = s.get("values") or []
        for tag in vals:
            if d == "scene":
                if tag in ("对话", "转场", "其他", "其它"):
                    continue
                if tag.startswith("非") and len(tag) > 1 and tag[1:] in vals:
                    continue  # 与 xx 成对的补全标签，不需要词
            if (kw.get(d) or {}).get(tag) or f"{d}.{tag}" in custom_kw:
                continue
            missing.append(f"{d}.{tag}")
    if missing:
        print("提醒: 以下标签还没有关键词，本地打标不会命中（可补词，或让 LLM 语义补标）：")
        print("  " + "、".join(missing))

    if args.dry_run:
        print("[dry-run] 将写入：")
        for w in written:
            print("  " + w)
        return
    print("已写入：")
    for w in written:
        print("  " + w)
    print("下一步：")
    print(f"  已有片段重打标签：python segment.py --lib {args.lib} --retag")
    print(f"  开始拆新书：      python segment.py <小说文件> --lib {args.lib}")


if __name__ == "__main__":
    main()
