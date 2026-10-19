#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""_common.py — novel-tagger 公共工具（仅标准库 + 可选 PyYAML）。

包含：编码兼容读取、YAML/JSON 配置读写、「标准库 + 用户自定义库」合并、
JSONL 读写、引号统计、SQLite 索引构建、标签筛选查询等。
"""
from __future__ import annotations

import json
import re
import sqlite3
import sys
from copy import deepcopy
from datetime import datetime
from pathlib import Path

CJK_RE = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf]")
DEFAULT_QUOTE_PAIRS = [("“", "”"), ("「", "」"), ("『", "』")]
DB_NAME = "index.sqlite"

_MISSING = object()


# ---------------------------------------------------------------- 控制台/文件

def setup_console() -> None:
    """让 Windows 控制台按 UTF-8 输出中文。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def read_text(path) -> str:
    """编码兼容读取：utf-8-sig / utf-8 / gb18030。"""
    data = Path(path).read_bytes()
    for enc in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def load_config(path, default=_MISSING):
    """读取 YAML/JSON 配置；文件不存在：给了 default 返回 default，否则报错。"""
    path = Path(path)
    if not path.exists():
        if default is not _MISSING:
            return default
        raise FileNotFoundError(str(path))
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() in (".yaml", ".yml"):
        try:
            import yaml  # 可选依赖
            return yaml.safe_load(text)
        except ImportError:
            alt = path.with_suffix(".json")
            if alt.exists():
                return json.loads(alt.read_text(encoding="utf-8"))
            raise RuntimeError(f"读取 {path.name} 需要 PyYAML：pip install pyyaml（或提供同名 .json）")
    return json.loads(text)


def save_config(path, obj) -> Path:
    """保存配置；.yaml 优先 PyYAML，不可用则落盘为同名 .json。返回实际路径。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.suffix.lower() in (".yaml", ".yml"):
        try:
            import yaml
            text = yaml.safe_dump(obj, allow_unicode=True, sort_keys=False,
                                  default_flow_style=False, width=100)
            path.write_text(text, encoding="utf-8")
            return path
        except ImportError:
            path = path.with_suffix(".json")
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def dump_json(path, obj) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def iter_jsonl(path):
    p = Path(path)
    if not p.exists():
        return
    with p.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                yield json.loads(line)


def write_jsonl(path, rows) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def die(msg: str, code: int = 2):
    print(f"错误：{msg}", file=sys.stderr)
    raise SystemExit(code)


def now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M")


# ------------------------------------------------------- 标准库 + 用户库合并

def _norm_override(ov):
    """用户库顶层的 override 声明 → None（不覆盖）/ "all"（完全覆盖）/ [键名,...]（局部覆盖）。"""
    if ov is True:
        return "all"
    if isinstance(ov, str):
        s = ov.strip().lower()
        if s in ("all", "true", "yes", "完全覆盖", "全部"):
            return "all"
        return [x.strip() for x in ov.replace("，", ",").split(",") if x.strip()]
    if isinstance(ov, (list, tuple)):
        return [str(x).strip() for x in ov if str(x).strip()]
    return None


def merge_keywords(base: dict, user: dict | None) -> dict:
    """词典合并：同维度同标签 → 关键词并集、阈值以用户为准；新维度/新标签追加。

    覆盖：用户库顶层 `override: all` 完全覆盖标准库；
          `override: [维度,...]` → 列出的维度整体替换（未重新给出的维度被删除）。
    """
    out = deepcopy(base or {})
    if not user:
        return out
    ov = _norm_override(user.get("override"))
    if ov == "all":
        return deepcopy({k: v for k, v in user.items() if k != "override"})
    if isinstance(ov, list):
        for dim in ov:
            out.pop(dim, None)
    for dim, spec_map in user.items():
        if dim == "override":
            continue
        out.setdefault(dim, {})
        for tag, uspec in (spec_map or {}).items():
            if tag not in out[dim] or not isinstance(out[dim][tag], dict) or not isinstance(uspec, dict):
                out[dim][tag] = deepcopy(uspec)
                continue
            merged = dict(out[dim][tag])
            kws = list(merged.get("keywords", []) or [])
            for k in (uspec.get("keywords", []) or []):
                if k not in kws:
                    kws.append(k)
            merged["keywords"] = kws
            if "threshold" in uspec:
                merged["threshold"] = uspec["threshold"]
            out[dim][tag] = merged
    return out


def merge_rules(base: dict, user: dict | None) -> dict:
    """规则合并：列表字段取并集，字典字段浅合并，其它以用户为准。

    覆盖：顶层 `override: all` 完全覆盖；`override: [字段,...]` → 列出的字段整体替换。
    """
    out = deepcopy(base or {})
    if not user:
        return out
    ov = _norm_override(user.get("override"))
    if ov == "all":
        return deepcopy({k: v for k, v in user.items() if k != "override"})
    if isinstance(ov, list):
        for key in ov:
            out.pop(key, None)
    for key, uval in user.items():
        if key == "override":
            continue
        if key not in out:
            out[key] = deepcopy(uval)
            continue
        bval = out[key]
        if isinstance(bval, list) and isinstance(uval, list):
            for x in uval:
                if x not in bval:
                    bval.append(x)
        elif isinstance(bval, dict) and isinstance(uval, dict):
            bval.update(deepcopy(uval))
        else:
            out[key] = deepcopy(uval)
    return out


def merge_taxonomy(base: dict, user: dict | None) -> dict:
    """维度库合并：默认并集；`override: all` 完全覆盖；`override: [维度,...]` 局部覆盖。"""
    out = deepcopy(base or {})
    if not user:
        return out
    ov = _norm_override(user.get("override"))
    if ov == "all":
        return deepcopy({k: v for k, v in user.items() if k != "override"})
    if isinstance(ov, list):
        dims = out.setdefault("dimensions", {})
        for d in ov:
            dims.pop(d, None)
        if isinstance(out.get("llm_dimensions"), list):
            out["llm_dimensions"] = [d for d in out["llm_dimensions"] if d not in ov]
    for key, uval in user.items():
        if key == "override":
            continue
        if key == "dimensions" and isinstance(uval, dict):
            out.setdefault("dimensions", {})
            for dim, dspec in uval.items():
                if dim in out["dimensions"] and isinstance(dspec, dict) and isinstance(out["dimensions"][dim], dict):
                    cur = out["dimensions"][dim]
                    for k, v in dspec.items():
                        if k == "values" and isinstance(v, list):
                            vals = list(cur.get("values", []) or [])
                            for x in v:
                                if x not in vals:
                                    vals.append(x)
                            cur["values"] = vals
                        else:
                            cur[k] = deepcopy(v)
                else:
                    out["dimensions"][dim] = deepcopy(dspec)
        elif key == "llm_dimensions" and isinstance(uval, list):
            vals = list(out.get("llm_dimensions", []) or [])
            for x in uval:
                if x not in vals:
                    vals.append(x)
            out["llm_dimensions"] = vals
        else:
            out[key] = deepcopy(uval)
    # llm_dimensions 里清掉已不存在的维度
    dims_now = out.get("dimensions") or {}
    if isinstance(out.get("llm_dimensions"), list):
        out["llm_dimensions"] = [d for d in out["llm_dimensions"] if d in dims_now]
    return out


def compute_dim_gate(user_tax: dict | None):
    """根据 taxonomy.user.yaml 的 override 生成「打标限制」。

    没写 override → None（不限制，向后兼容）。
    - `override: all`     → 只有该文件里列出的维度会打标；列出的维度按 values 过滤标签值
    - `override: [d1,d2]` → 这些维度按 values 过滤；未重新定义的维度不再打标
    """
    ov = _norm_override((user_tax or {}).get("override"))
    if ov is None:
        return None
    dims = {}
    if isinstance(user_tax.get("dimensions"), dict):
        dims = user_tax["dimensions"]
    gate = {"drop": [], "only": None, "values": {}}
    if ov == "all":
        gate["only"] = list(dims.keys())
        for d, spec in dims.items():
            vals = (spec or {}).get("values") if isinstance(spec, dict) else None
            if vals:
                gate["values"][d] = list(vals)
    else:
        for d in ov:
            if d in dims and isinstance(dims.get(d), dict):
                vals = dims[d].get("values")
                if vals:
                    gate["values"][d] = list(vals)
            else:
                gate["drop"].append(d)
    return gate


def _load_maybe(path_yaml: Path):
    """尝试加载 xxx.yaml，其次 xxx.json；都不存在返回 None。"""
    if path_yaml.exists():
        return load_config(path_yaml)
    pj = path_yaml.with_suffix(".json")
    if pj.exists():
        return load_config(pj)
    return None


def load_libraries(cfg_dir, lib_dir):
    """加载「标准库（技能自带 config/）+ 用户自定义库（<lib>/ 下三个 *.user.yaml）」。

    返回 (rules, keywords, taxonomy, info)
    """
    cfg_dir = Path(cfg_dir)
    lib_dir = Path(lib_dir)
    rules = load_config(cfg_dir / "rules.yaml")
    keywords = load_config(cfg_dir / "keywords.yaml")
    taxonomy = load_config(cfg_dir / "taxonomy.yaml")
    info = {"standard_dir": str(cfg_dir), "user_files": []}
    u_tax = None
    for name, merger, which in (
        ("rules.user.yaml", merge_rules, "rules"),
        ("keywords.user.yaml", merge_keywords, "keywords"),
        ("taxonomy.user.yaml", merge_taxonomy, "taxonomy"),
    ):
        u = _load_maybe(lib_dir / name)
        if u is None:
            continue
        if which == "rules":
            rules = merger(rules, u)
        elif which == "keywords":
            keywords = merger(keywords, u)
        else:
            u_tax = u
            taxonomy = merger(taxonomy, u)
        info["user_files"].append(str(lib_dir / name))
    gate = compute_dim_gate(u_tax)
    if gate:
        info["dim_gate"] = gate
    return rules, keywords, taxonomy, info


def load_terms(lib) -> dict:
    """读取 <lib>/terms.json（mine_terms.py 产物）→ {书名: [词条dict, ...]}。"""
    p = Path(lib) / "terms.json"
    if not p.exists():
        return {}
    data = load_config(p) or {}
    out = {}
    for book, info in (data.get("books") or {}).items():
        terms = info.get("terms") if isinstance(info, dict) else info
        out[book] = terms or []  # 空列表也算「已提炼过」，避免重复自动提炼
    return out


# ------------------------------------------------------------------ 文本工具

def quote_spans(text: str, pairs=None) -> list:
    """引号区间列表（合并重叠），供判断某位置是否在对话引号内。"""
    pairs = [tuple(p) for p in (pairs or DEFAULT_QUOTE_PAIRS)]
    spans = []
    for lq, rq in pairs:
        start = 0
        while True:
            i = text.find(lq, start)
            if i < 0:
                break
            j = text.find(rq, i + 1)
            if j < 0:
                break
            spans.append((i, j + 1))
            start = j + 1
    spans.sort()
    merged = []
    for a, b in spans:
        if merged and a <= merged[-1][1]:
            if b > merged[-1][1]:
                merged[-1] = (merged[-1][0], b)
        else:
            merged.append((a, b))
    return merged


def quote_stats(text: str, pairs=None) -> dict:
    """对话占比统计：引号内字数占比 + 引号开头段落占比。"""
    merged = quote_spans(text, pairs)
    quoted = sum(b - a for a, b in merged)
    total = max(len(text), 1)
    paras = [x for x in text.split("\n") if x.strip()]
    dparas = sum(1 for x in paras if x.strip()[:1] in ("“", "「", "『"))
    return {
        "ratio": round(quoted / total, 3),
        "para_ratio": round(dparas / max(len(paras), 1), 3),
        "n_paras": len(paras),
        "n_chars": len(text),
    }


def slugify(s: str, fallback: str = "book") -> str:
    s = (s or "").strip()
    out = []
    for ch in s:
        if CJK_RE.match(ch) or ch.isalnum():
            out.append(ch)
        elif ch in " _-—·.":
            out.append("_")
    r = re.sub(r"_+", "_", "".join(out)).strip("_")
    return r or fallback


def sanitize_filename(s: str, fallback: str = "x") -> str:
    s = re.sub(r'[\\/:*?"<>|\r\n\t]', "_", str(s)).strip().strip(".")
    return s or fallback


# ------------------------------------------------------------- SQLite 索引层

def build_db(lib) -> int:
    """从 fragments.jsonl 重建 index.sqlite。返回片段总数。"""
    lib = Path(lib)
    rows = list(iter_jsonl(lib / "fragments.jsonl"))
    con = sqlite3.connect(str(lib / DB_NAME))
    try:
        con.executescript("""
        DROP TABLE IF EXISTS frags;
        DROP TABLE IF EXISTS tags;
        CREATE TABLE frags(
            id TEXT PRIMARY KEY, book TEXT, chapter INTEGER, frag_no INTEGER,
            chapter_title TEXT, start INTEGER, end INTEGER, n_chars INTEGER,
            dialogue_ratio REAL, llm_done INTEGER, tags_json TEXT, hits_json TEXT, text TEXT);
        CREATE TABLE tags(frag_id TEXT, dim TEXT, tag TEXT);
        CREATE INDEX idx_tags ON tags(dim, tag);
        CREATE INDEX idx_frags_book ON frags(book, chapter);
        """)
        for r in rows:
            con.execute(
                "INSERT OR REPLACE INTO frags VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (r.get("id"), r.get("book"), r.get("chapter"), r.get("frag_no"),
                 r.get("chapter_title", ""), r.get("start", 0), r.get("end", 0),
                 r.get("n_chars", 0), r.get("dialogue_ratio", 0.0),
                 1 if r.get("llm_done") else 0,
                 json.dumps(r.get("tags", {}), ensure_ascii=False),
                 json.dumps(r.get("hits", {}), ensure_ascii=False),
                 r.get("text", "")))
            for dim, vals in (r.get("tags") or {}).items():
                for v in vals:
                    con.execute("INSERT INTO tags VALUES(?,?,?)", (r["id"], dim, v))
        con.commit()
    finally:
        con.close()
    return len(rows)


def ensure_db(lib) -> Path:
    """确保索引存在且不比 jsonl 旧。"""
    lib = Path(lib)
    jl = lib / "fragments.jsonl"
    if not jl.exists():
        raise FileNotFoundError(f"未找到 {jl}，请先运行 segment.py 拆解小说")
    db = lib / DB_NAME
    if (not db.exists()) or db.stat().st_mtime < jl.stat().st_mtime:
        build_db(lib)
    return db


def connect(lib) -> sqlite3.Connection:
    con = sqlite3.connect(str(ensure_db(lib)))
    con.row_factory = sqlite3.Row
    return con


def row_to_dict(r) -> dict:
    d = dict(r)
    d["tags"] = json.loads(d.pop("tags_json") or "{}")
    d["hits"] = json.loads(d.pop("hits_json") or "{}")
    d["llm_done"] = bool(d.get("llm_done"))
    return d


def parse_tag_args(tag_args):
    """['plot:恋爱', 'scene:对话'] → [('plot', ['恋爱']), ('scene', ['对话'])]
    同维度多值 = 或；不同维度 = 且。不带维度的值直接在任意维度里找。"""
    groups = {}
    for t in tag_args or []:
        t = str(t).strip()
        if not t:
            continue
        if ":" in t:
            dim, val = t.split(":", 1)
        else:
            dim, val = "", t
        groups.setdefault(dim.strip(), []).append(val.strip())
    return list(groups.items())


_ORDER = {"id": "id", "chapter": "book, chapter, frag_no",
          "chars": "n_chars DESC", "ratio": "dialogue_ratio DESC"}


def select_rows(lib, tag_filters=None, book=None, chapter=None, text=None,
                limit=None, offset=0, order="id"):
    """组合筛选片段。tag_filters 为 [(dim, [values])]，逻辑见 parse_tag_args。"""
    con = connect(lib)
    try:
        ids = None
        for dim, vals in (tag_filters or []):
            vals = [v for v in (vals or []) if v]
            if not vals:
                continue
            if dim:
                q = "SELECT DISTINCT frag_id FROM tags WHERE dim=? AND tag IN (%s)" % ",".join("?" * len(vals))
                s = {row[0] for row in con.execute(q, [dim] + vals)}
            else:
                q = "SELECT DISTINCT frag_id FROM tags WHERE tag IN (%s)" % ",".join("?" * len(vals))
                s = {row[0] for row in con.execute(q, vals)}
            ids = s if ids is None else (ids & s)
        where, params = [], []
        if ids is not None:
            if not ids:
                return []
            where.append("id IN (%s)" % ",".join("?" * len(ids)))
            params.extend(ids)
        if book:
            where.append("book LIKE ?")
            params.append(f"%{book}%")
        if chapter is not None:
            where.append("chapter = ?")
            params.append(chapter)
        if text:
            where.append("text LIKE ?")
            params.append(f"%{text}%")
        sql = "SELECT * FROM frags"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY " + _ORDER.get(order, "id")
        if limit:
            sql += f" LIMIT {int(limit)}"
        if offset:
            sql += f" OFFSET {int(offset)}"
        return [row_to_dict(r) for r in con.execute(sql, params)]
    finally:
        con.close()
