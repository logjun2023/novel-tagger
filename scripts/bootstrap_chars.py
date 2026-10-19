#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""bootstrap_chars.py — 本地挖掘人物候选名（零 LLM），生成/合并人物表。

原理：统计“X说/道/问/答…”“”X说““X：”等对话归属句式里出现的 2-4 字人名，按频次排序。
只是候选，最后用 --role 指定角色标签（男1/女1…），或手工改 characters.yaml。

用法：
  python bootstrap_chars.py done/某书.md --top 30
  python bootstrap_chars.py done/某书.md --top 30 --write taglib/characters.yaml
  python bootstrap_chars.py done/某书.md --write taglib/characters.yaml --role 陆诚=男1 --role 苏晚=女1
  python bootstrap_chars.py done/某书.md --write taglib/characters.yaml --pov 陆诚   # 第一人称叙述者
"""
from __future__ import annotations

import argparse
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _common as C

STOPWORDS = set((
    "他们 我们 你们 她们 它们 自己 对方 大家 众人 两人 一人 三人 那人 此人 有人 没人 别人 "
    "这个 那个 哪个 什么 怎么 为什么 这里 那里 现在 如今 当时 此时 此刻 然后 接着 随后 同时 "
    "于是 可是 但是 只是 不过 而且 突然 顿时 忽然 立刻 马上 只见 只听 心中 心里 心头 眼中 面前 "
    "身后 声音 时候 一个 两个 三个 一下 一声 一阵 一道 一旁 一直 一样 一切 算了 罢了 好了 对了 "
    "不是 没有 就是 还是 真是 原来 果然 当然 依然 仍然 居然 竟然 终于 早就 已经 曾经 出来 起来 "
    "过来 过去 下来 上去 回来 回去 进来 出去 说道 听说 好看 不错 开始 结束 事情 东西 地方 问题 "
    "办法 主意 机会 样子 表情 眼神 目光 脸色 嘴角 眉头 双手 脚下 门外 窗外 楼上 楼下 医院 学校 "
    "公司 警察 老师 学生 医生 老板 总裁 女人 男人 女孩 男孩 少女 少年 老头 老人 姑娘 孩子 小姐 "
    "夫人 太太 师傅 哥哥 姐姐 弟弟 妹妹 爸爸 妈妈 父亲 母亲 儿子 女儿 管家 秘书 助理 服务员 "
    "轻声 低声 小声 大声 柔声 冷声 沉声 闻言 连忙 急忙 赶紧 随即 当下 这时 那时 一边 一面 "
    "转身 抬头 低头 回头 点头 摇头 开口 沉默 半晌 良久 伸手 抬手 皱眉 苦笑 干笑 大笑 微笑 "
    "继续 如果 可以 应该 刚想 随口 故意 老实 大概 也许 似乎 本来 原本 从来 向来 深知 明知"
).split())

BAD_CHARS = set("得了着过吗呢吧啊呀哦嗯是有在和就都也又还只很更最不没别被把让给对从向往到得地们之乎者我你他她它咱您谁")

PAT_ATTR = re.compile(
    r"([\u4e00-\u9fff]{2,4}?)(?:说道|说|道|问道|问|答道|答|喊道|叫道|笑道|冷笑道|开口|沉声道|怒道|叹道|哼道|低声道|轻声道)")
PAT_AFTER = re.compile(r"[”」]([\u4e00-\u9fff]{2,4}?)(?:说|道|问|答|笑|喊|叫|哼|叹)")
PAT_COLON = re.compile(r"([\u4e00-\u9fff]{2,4}?)(?=：[“「『]|:[“「『\"]| ：[“「『])")

SPEECH_TAIL = set("说问道答笑喊叫叹哼")

COMPOUND_SURNAMES = ("欧阳", "司马", "上官", "诸葛", "东方", "皇甫", "尉迟", "公孙", "慕容",
                     "司徒", "令狐", "宇文", "长孙", "南宫", "西门", "夏侯", "独孤", "轩辕",
                     "耶律", "完颜", "赫连")

_F_CUES = re.compile(r"她|女|姐|妹|妈|娘|姨|奶|婆|公主|小姐|女士|夫人|太太|姑娘|少女|女孩|闺蜜|女友|老婆|妻子|女儿|皇后|女王|护士|嫂子")
_M_CUES = re.compile(r"他|男|哥|弟|爸|爹|叔|爷|少爷|先生|少年|男孩|汉子|男友|老公|丈夫|儿子|皇帝|王子|老板|哥哥")


def guess_gender(full: str, name: str) -> str:
    """启发式性别：统计名字附近小窗口里的性别线索词投票（保守，拿不准返回空）。"""
    if not full or not name:
        return ""
    votes = {"女": 0, "男": 0}
    start, seen = 0, 0
    while seen < 150:
        i = full.find(name, start)
        if i < 0:
            break
        seen += 1
        start = i + len(name)
        w = full[max(0, i - 14): i + len(name) + 14]
        f, m = len(_F_CUES.findall(w)), len(_M_CUES.findall(w))
        if f > m:
            votes["女"] += 1
        elif m > f:
            votes["男"] += 1
    if votes["女"] >= 2 and votes["女"] >= votes["男"] * 2:
        return "女"
    if votes["男"] >= 2 and votes["男"] >= votes["女"] * 2:
        return "男"
    return ""


def bad(name: str) -> bool:
    if name in STOPWORDS or len(name) < 2:
        return True
    if any(ch in BAD_CHARS for ch in name):
        return True
    if len(set(name)) == 1:
        return True
    if name[-1] in SPEECH_TAIL:  # 名字不会以「说/道/笑…」结尾
        return True
    if len(name) == 4 and not name.startswith(COMPOUND_SURNAMES):
        return True
    return False


def main():
    C.setup_console()
    ap = argparse.ArgumentParser(description="本地挖掘人物候选名")
    ap.add_argument("files", nargs="+")
    ap.add_argument("--top", type=int, default=30)
    ap.add_argument("--min-count", type=int, default=4)
    ap.add_argument("--write", default=None, help="输出/合并到人物表（如 taglib/characters.yaml）")
    ap.add_argument("--role", action="append", default=[], help="名字=角色标签（如 陆诚=男1），可重复")
    ap.add_argument("--pov", default=None, help="第一人称叙述者（名字或 role；标 pov: true，「我」代词自动归属）")
    ap.add_argument("--auto-gender", action="store_true",
                    help="顺带启发式猜性别写入 gender（名字附近窗口的性别线索投票，供代词指代用）")
    ap.add_argument("--show-ctx", action="store_true",
                    help="候选行附原文小片段（默认不显示：避免把书内正文带进上下文）")
    args = ap.parse_args()

    role_map = {}
    for spec in args.role:
        if "=" in spec:
            nm, rl = spec.split("=", 1)
            role_map[nm.strip()] = rl.strip()

    counts = Counter()
    first_idx = {}
    parts = []
    for fp in args.files:
        parts.append(C.read_text(fp))
    full = "\n".join(parts)

    for pat in (PAT_ATTR, PAT_AFTER, PAT_COLON):
        for m in pat.finditer(full):
            name = m.group(1)
            if bad(name):
                continue
            counts[name] += 1
            if name not in first_idx:
                first_idx[name] = m.start()

    def _derivative(name: str) -> bool:
        """派生词过滤：如「林岳知」源自「林岳知道」——含有更高频的短候选前缀时丢弃。"""
        if name.startswith(COMPOUND_SURNAMES):
            return False
        for i in range(2, len(name)):
            p = name[:i]
            if p in counts and counts[p] >= max(3, 2 * counts[name]):
                return True
        return False

    cands = [(n, c) for n, c in counts.most_common()
             if c >= args.min_count and not _derivative(n)][: args.top]
    print(f"共扫描 {len(full)} 字，候选人物名 {len(cands)} 个（按对话归属句式频次）：")
    if cands and not args.show_ctx:
        print("  （候选上下文默认不显示：需要时加 --show-ctx；避免把书内正文带进上下文）")
    for name, cnt in cands:
        role = role_map.get(name, "")
        mark = f"  [{role}]" if role else ""
        if args.show_ctx:
            idx = first_idx.get(name, 0)
            ctx = full[max(0, idx - 12): idx + len(name) + 12].replace("\n", " ")
            print(f"  {cnt:>5}  {name}{mark}   …{ctx}…")
        else:
            print(f"  {cnt:>5}  {name}{mark}")
    if not cands:
        print("（未挖到候选：可降低 --min-count 或检查文本格式）")

    if args.write:
        wp = Path(args.write)
        data = {"characters": []}
        if wp.exists():
            data = C.load_config(wp) or {"characters": []}
            data.setdefault("characters", [])
        existing = {c.get("name") for c in data["characters"]}
        added = 0
        for name, cnt in cands:
            if name in existing:
                continue
            data["characters"].append({"name": name, "role": role_map.get(name, ""), "aliases": []})
            added += 1
        for c in data["characters"]:
            if not c.get("role") and c.get("name") in role_map:
                c["role"] = role_map[c["name"]]
        if args.pov:
            for c in data["characters"]:
                if args.pov in {c.get("name"), c.get("role")} or args.pov in (c.get("aliases") or []):
                    c["pov"] = True
        guessed = 0
        if args.auto_gender:
            for c in data["characters"]:
                nm = c.get("name") or ""
                if nm and not c.get("gender"):
                    g = guess_gender(full, nm)
                    if g:
                        c["gender"] = g
                        guessed += 1
        actual = C.save_config(wp, data)
        print(f"\n已写入 {actual}（本次新增 {added} 个，共 {len(data['characters'])} 个）")
        if args.auto_gender:
            print(f"顺带猜了性别 {guessed} 个（启发式：错的话直接改该条目的 gender 字段）")
        print("提示：把 role 改成 男1/女1/男2…（role 留空则直接用名字当标签）；"
              "代词（我/他/她）会自动就近归属到对应人物（本机消解，零 LLM）；然后重打标签：")
        print(f"  python segment.py <小说文件> --lib {wp.parent} --retag")


if __name__ == "__main__":
    main()
