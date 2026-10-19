# novel-tagger

[中文](#中文) · [English](#english)

> 把中文小说拆成「章节 → 场景片段」，为每个片段打上多维标签，产出可查询、可导出的素材库。
>
> Split Chinese novels into chapter → scene fragments, tag each fragment across multiple dimensions, and build a queryable, exportable material library.

---

## 中文

### 简介

**novel-tagger** 是一个中文小说「片段切分 + 多维打标」工具，以 [OpenCode](https://opencode.ai) Agent Skill 的形式提供，也可以直接当作一组 Python 脚本使用。

它把整本书切成互不重叠的场景片段，并为每个片段记录：

- **位置**：书名 / 章号 / 片段号 / 原文起止偏移 / 字数 / 对白占比
- **标签**：场景、人物、类型、描写、自定义维度（同一片段可多标签共存）
- **命中词**：每个标签由哪些关键词命中、命中几次（可追溯）
- **原文**：供按标签或关键词取回

### 特性

- **场景是唯一拆分标准**：只按章节、转场词、分隔符、长度切分；人物 / 类型 / 描写等维度只作为片段特征，不会把片段越切越碎。
- **场景语义**：多个场景标签可共存；`xx` / `非xx` 成对互斥并自动补全（如 对话 / 非对话）；值域含「其他」时自动兜底。
- **人物标签**：人物 ID（名字 / 别名匹配）+ 代词就近归属（我 / 他 / 她，本地启发式，零 LLM）；人物数量只记总人数（`N人` / `上百人` / `上千人` / `上万人`），不区分性别年龄。
- **词条自动提炼**：拆解新书时自动做一次「新词发现」，提炼书内专有词 / 主题词（零 LLM，默认开启，`--no-terms` 关闭）。
- **词库可覆盖**：标准库（技能自带）+ 用户库（素材库目录下的 `*.user.yaml`）；`override` 可完全替换默认维度、标签或规则。
- **本地优先、省 token**：切分、对话检测、人物匹配、关键词打标、索引、查询、导出全部本地完成；LLM 仅用于可选的语义补标。
- **敏感内容安全输出**：默认只汇报统计，词条词面 / 片段上下文需显式开关（`--show-terms` / `--show-ctx`），避免触发模型安全过滤。

### 安装

要求 **Python 3.9+**，仅使用标准库（可选安装 PyYAML；没有时可用 `.json` 配置）。

作为 OpenCode 技能安装：

```bash
git clone https://github.com/logjun2023/novel-tagger.git ~/.config/opencode/skills/novel-tagger
```

Windows（PowerShell）：

```powershell
git clone https://github.com/logjun2023/novel-tagger.git "$env:USERPROFILE\.config\opencode\skills\novel-tagger"
```

也可以不安装，直接把仓库当作普通脚本集合使用。

### 快速开始

以下命令在仓库（技能）根目录执行，`<lib>` 默认为 `./taglib`：

```bash
# 拆书 + 本地打标（零 token）
python scripts/segment.py path/to/novel.md --lib taglib

# 查看已拆出的标签 / 关键字
python scripts/list_tags.py --lib taglib
python scripts/list_tags.py --lib taglib --book 某书 --library

# 按标签 / 关键词查询（默认只给摘要与片段 ID）
python scripts/query.py --lib taglib --tag scene:对话 --tag plot:恋爱
python scripts/query.py --lib taglib --kw 某关键词 --limit 5
python scripts/query.py --lib taglib --kw 某关键词 --full      # 取回原文

# 多关键字表格 / 按标签分存导出
python scripts/table.py --lib taglib
python scripts/export.py --lib taglib --by plot
```

人物表（可选，用于人物 ID 与代词归属）：

```bash
python scripts/bootstrap_chars.py path/to/novel.md --top 30 \
  --write taglib/characters.yaml --role 角色A=男1 --role 角色B=女1
python scripts/segment.py path/to/novel.md --lib taglib --retag --only-dims chars
```

维度 / 标签定制（可选，一次问答对整库生效）：

```bash
python scripts/setup_dims.py --lib taglib --list --human   # 查看默认维度与标签
python scripts/setup_dims.py --lib taglib --ask            # 终端问答
python scripts/setup_dims.py --lib taglib --preset dialogue-only
```

### 目录结构

```
novel-tagger/
├── SKILL.md            # Agent Skill 说明（触发词、工作流、规则）
├── config/             # 标准库：维度 / 关键词 / 切分规则
├── scripts/            # 全部命令行工具与查询函数
├── references/         # 切分调参、LLM 补标协议
└── README.md
```

运行后默认生成素材库 `taglib/`：

```
taglib/
├── fragments.jsonl   主数据：每行一个片段（位置 + 标签 + 命中词 + 原文）
├── index.sqlite      查询索引（自动重建，无需手管）
├── characters.yaml   人物表（名字 / 别名 / 角色 / pov）
├── *.user.yaml       用户覆盖：taxonomy / keywords / rules
├── terms.json        自动提炼的书内词条（可手工增删）
├── llm_inbox/        可选语义补标的批次与结果
├── tables/           多关键字表格（CSV / Markdown）
└── export/           按标签分存导出
```

### 配置

| 文件 | 作用 |
| --- | --- |
| `config/taxonomy.yaml` | 维度与值域定义 |
| `config/keywords.yaml` | 标签 → 触发词 + 阈值 |
| `config/rules.yaml` | 章节正则、转场词、分隔符、片段长度、对话阈值 |
| `<lib>/taxonomy.user.yaml` | 增加维度 / 标签，或 `override` 完全覆盖 |
| `<lib>/keywords.user.yaml` | 加词、改阈值、加新标签 |
| `<lib>/characters.yaml` | 人物 ID、别名、角色、`pov` |
| `<lib>/rules.user.yaml` | 覆盖切分规则 |

修改词库或人物表后，重打标签（切片不动）：

```bash
python scripts/segment.py path/to/novel.md --lib taglib --retag
```

### 敏感内容说明

拆书全程本地执行，正文不会进入模型。但自动词条、命中词、片段上下文仍可能是敏感内容，因此脚本默认只输出统计；需要查看时显式加 `--show-terms`（词条词面）或 `--show-ctx`（候选上下文）。

可选的 LLM 语义补标会把片段正文交给模型，敏感内容被安全过滤拦截属预期情况：跳过该批即可，不影响本地标签、查询与导出。

### 英文小说

当前版本面向中文：英文文本可以跑通切分，但对话检测（中文引号）、关键词词库、人物候选挖掘、代词归属（我 / 他 / 她）与词条提炼（CJK n-gram）均针对中文，英文片段基本只会得到兜底的「其他」标签。暂未提供英文语言包。

### 许可

[MIT](LICENSE)

---

## English

### What is this?

**novel-tagger** is a Chinese-novel segmentation and multi-dimensional tagging toolkit. It ships as an [OpenCode](https://opencode.ai) Agent Skill and can also be used as a plain set of Python scripts.

It splits a book into non-overlapping scene fragments and records, for each fragment:

- **Location** — book / chapter / fragment number / character offsets / length / dialogue ratio
- **Tags** — scene, characters, plot, style, and custom dimensions (multiple tags per fragment)
- **Matched keywords** — which terms triggered each tag, and how many times
- **Source text** — retrievable by tag or keyword

### Features

- **Scene is the only split criterion** — only chapters, transition phrases, separators, and length drive segmentation; character / plot / style dimensions never fragment the text further.
- **Scene semantics** — multiple scene tags may coexist; `X` / `not-X` pairs are mutually exclusive and auto-completed (e.g. dialogue / non-dialogue); an "other" tag catches fragments without a scene label.
- **Character tags** — character IDs via name / alias matching plus local pronoun resolution (Chinese pronouns 我 / 他 / 她, heuristic, zero LLM); person count records the total only (`N人`, `上百人`, `上千人`, `上万人`), with no gender or age breakdown.
- **Automatic term mining** — a new-word-discovery pass per new book (zero LLM, on by default; disable with `--no-terms`).
- **Overridable lexicon** — bundled standard library plus per-library `*.user.yaml` overrides that can fully replace dimensions, tags, or rules.
- **Local-first, token-light** — segmentation, dialogue detection, character matching, keyword tagging, indexing, querying, and exporting all run locally; the LLM is only used for optional semantic tagging.
- **Safe output for sensitive content** — statistics only by default; term surfaces and fragment context require explicit flags (`--show-terms` / `--show-ctx`) to avoid tripping model safety filters.

### Install

Requires **Python 3.9+** and the standard library only (PyYAML is optional; `.json` configs are supported when it is absent).

As an OpenCode skill:

```bash
git clone https://github.com/logjun2023/novel-tagger.git ~/.config/opencode/skills/novel-tagger
```

Windows (PowerShell):

```powershell
git clone https://github.com/logjun2023/novel-tagger.git "$env:USERPROFILE\.config\opencode\skills\novel-tagger"
```

You can also use the repository as a standalone collection of scripts.

### Quick start

Run from the repository (skill) root; `<lib>` defaults to `./taglib`:

```bash
# Segment + local tagging (zero tokens)
python scripts/segment.py path/to/novel.md --lib taglib

# Inspect tags / keywords
python scripts/list_tags.py --lib taglib
python scripts/list_tags.py --lib taglib --book "Some Book" --library

# Query by tag or keyword (summary + fragment IDs by default)
python scripts/query.py --lib taglib --tag scene:对话 --tag plot:恋爱
python scripts/query.py --lib taglib --kw keyword --limit 5
python scripts/query.py --lib taglib --kw keyword --full      # return source text

# Multi-keyword tables / export by tag
python scripts/table.py --lib taglib
python scripts/export.py --lib taglib --by plot
```

Character table (optional; enables character IDs and pronoun attribution). Tag values below are the default Chinese labels and can be renamed in the taxonomy:

```bash
python scripts/bootstrap_chars.py path/to/novel.md --top 30 \
  --write taglib/characters.yaml --role Alice=女1 --role Bob=男1
python scripts/segment.py path/to/novel.md --lib taglib --retag --only-dims chars
```

Dimension / tag customization (optional; one Q&A session applies to the whole library):

```bash
python scripts/setup_dims.py --lib taglib --list --human   # show default dimensions and tags
python scripts/setup_dims.py --lib taglib --ask            # interactive terminal Q&A
python scripts/setup_dims.py --lib taglib --preset dialogue-only
```

### Repository layout

```
novel-tagger/
├── SKILL.md            # Agent Skill definition (triggers, workflow, rules)
├── config/             # Standard library: dimensions / keywords / rules
├── scripts/            # CLI tools and query functions
├── references/         # Segmentation tuning, LLM tagging protocol
└── README.md
```

Running the tool creates a material library at `taglib/` (default):

```
taglib/
├── fragments.jsonl   Main data: one fragment per line (location + tags + matches + text)
├── index.sqlite      Query index (rebuilt automatically)
├── characters.yaml   Character table (names / aliases / roles / pov)
├── *.user.yaml       User overrides: taxonomy / keywords / rules
├── terms.json        Mined in-book terms (edit freely)
├── llm_inbox/        Optional semantic-tagging batches and results
├── tables/           Multi-keyword tables (CSV / Markdown)
└── export/           Exports grouped by tag
```

### Configuration

| File | Purpose |
| --- | --- |
| `config/taxonomy.yaml` | Dimension and value definitions |
| `config/keywords.yaml` | Tag → trigger keywords + thresholds |
| `config/rules.yaml` | Chapter patterns, transitions, separators, fragment length, dialogue thresholds |
| `<lib>/taxonomy.user.yaml` | Add dimensions / tags, or fully override |
| `<lib>/keywords.user.yaml` | Add keywords, change thresholds, add tags |
| `<lib>/characters.yaml` | Character IDs, aliases, roles, `pov` |
| `<lib>/rules.user.yaml` | Override segmentation rules |

After changing the lexicon or character table, re-tag without re-splitting:

```bash
python scripts/segment.py path/to/novel.md --lib taglib --retag
```

### Sensitive content

Everything runs locally; the source text never enters the model during segmentation. Mined terms, matched keywords, and fragment context may still be sensitive, so scripts report statistics only by default. Use `--show-terms` (term surfaces) or `--show-ctx` (candidate context) explicitly when needed.

Optional LLM semantic tagging is the only step that sends fragment text to a model. Sensitive batches may be blocked by safety filters — this is expected: skip the batch; local tags, queries, and exports are unaffected.

### Language support

The current release targets Chinese. English text can be segmented, but dialogue detection (Chinese quotes), the keyword lexicon, character candidate mining, pronoun resolution (我 / 他 / 她), and term mining (CJK n-grams) are Chinese-specific. English fragments will mostly receive only the "other" fallback tag. No English language pack is provided yet.

### License

[MIT](LICENSE)
