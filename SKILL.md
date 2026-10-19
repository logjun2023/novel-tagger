---
name: novel-tagger
description: 中文小说片段切分与多维打标（本地优先、省 token）。场景维度是切分的唯一标准（场景特征一般不互斥可多标签共存；xx/非xx 成对互斥+自动补全、其他 兜底且默认必含），其余维度是片段上的特征标签；人物ID 含名字/别名与代词（我/他/她）就近归属（本地零 LLM）、人物数量只记总人数（不分男女老幼）；把整本小说按「章节→场景片段」切分，形成“位置信息×多个标签关键字”的多关键字表格；拆书前可问答式定制「要哪些维度、哪些标签、覆盖还是增加」（setup_dims.py 一键生成配置：先选维度、再逐个问所选维度的标签；用户库 override 可完全覆盖标准库；除该问卷外不向用户提问、直接执行）。标签词库 = 标准库（技能自带）+ 用户自定义库（素材库目录下的 *.user.yaml）；拆书时零 token 自动提炼书内专有词（新词发现，内置特征、默认开启）。提供查询函数：列出某本/整目录的已拆关键字、按关键字取回对应段落原文、按标签分存导出。本地脚本完成绝大多数处理（零 token），仅在需要语义补标时批量调用 LLM；敏感内容默认只回统计（自动词条词面/片段上下文遮挡，防触发模型安全过滤）。触发：拆文打标、片段切分、给小说打标签、素材库、关键字表格、场景标签、人物标签、多维标签、自定义维度/标签、关键词自动提炼、按标签查询/导出、taglib。
---

# novel-tagger — 小说片段切分 · 多维打标 · 素材库

## 它做什么

一本（或多本）中文小说 → 拆成「章节 → 场景片段」→ 每个片段得到：

- **位置信息**：书名 / 章号 / 片段号 / 原文起止偏移 / 字数 / 对白占比
- **多个标签**（多维度，同一段可同时命中多个，如「场景=对话 + 人物=男1,女2,3人 + 类型=恋爱,吵架」）：
  - `scene` 场景：对话 / 战斗 / 日常 / 情感 / 回忆 / 环境 / 转场 / 其他（未分类兜底，默认必含）
  - `chars` 人物：只有两类特征——人物ID（男1 / 女2 / …，来自人物表的名字/别名 + **代词就近指代**：他/她/他们/她们按性别归属最近的提及，「我」= 叙述者需在人物表标 `pov: true`）+ 人物数量（<100 精确「N人」；≥100 按数量级：上百人 / 上千人 / 上万人；只统计总人数，不区分男女老幼）
  - `plot` 类型：恋爱 / 吵架 / 打脸 / 冲突 / 搞笑 / 温情 / 虐心 / 升级 / 悬疑
  - `style` 描写：动作描写 / 心理描写 / 环境描写 / 外貌描写
  - `term` 关键词：内置特征（**默认开启**，不算可选维度、不用问）——拆书时自动从本书提炼书内专有词/主题词（人名、术语、道具、情节词…），不预先指定；不满意可修剪
- **命中关键词**：每个标签由哪些词命中、命中几次（可追溯）

产出「多关键字表格」（`table.py`）：行=片段位置，列=各维度标签+命中词；另附倒排索引表（标签/关键词 → 片段ID）。

## 拆分与打标语义

- **场景（scene）是唯一拆分标准**：切分只由章节 / 转场词 / 长度等场景边界信号驱动；其余维度（人物/类型/描写/自定义）只是打在已切好片段上的**特征标签，不参与切分**。
- 场景特征**一般不互斥**：同一片段可同时带多个场景标签（如 战斗 + 吐槽 同片，都打上）。
  - 例外①：**`xx` / `非xx` 成对**（如 对话/非对话）→ 互斥；未命中 `xx` 的片段自动补 `非xx`。只要在 scene 值域里**同时声明这两个值**即可（不必再配 `dialogue.negative_label`）。
  - 例外②：值域含「**其他**」（标准值域默认必含；向导生成配置时自动带上）→ 兜底：没有任何场景标签的片段自动归入。若同时有 `非xx`，其他自然不命中——无需特殊处理。
- **偏大优先、片段独立**：多特征命中的文本留在同一片段（特征不会把片段越切越碎）；过小片段自动并入相邻；片段互不重叠、起止偏移自成一体。

## 省 token 三原则

1. **本地零 token**：切分、对话检测、人物匹配（名字/别名 + 代词就近指代）、关键词打标、索引、查询、导出全部由脚本完成。
2. **LLM 只做可选语义补标**：批量（默认 12 条/批）、只发未标片段、可截断、结果落盘缓存（`llm_done`），重跑自动跳过 —— 每个片段只花一次。
3. **查询/导出默认不回传正文**：只给摘要和片段ID；要全文才用 `--full`。

## 敏感内容 · 安全输出（防触发模型安全过滤）

- 拆书全程本地跑（正文不进模型），但**书内自动词条词面 / 命中词 / 片段上下文 / 批次正文**可能是敏感内容：一旦进入模型上下文就可能触发安全过滤、导致任务中断。
- 脚本默认只给统计：词条词面用 `--show-terms` 才显示（`segment` / `mine_terms` / `list_tags` / `query`），候选上下文用 `bootstrap_chars --show-ctx`；**不开开关、也不要把这些词面/上下文复述进对话**。
- 不要打开小说原文或 `fragments.jsonl`（哪怕只读开头）；判断格式/章节用脚本输出即可。`table.py` / `export.py` 导出的文件里含词条与原文——只给路径，不要贴内容。
- 语义补标（LLM）是唯一把正文交给模型的步骤：敏感内容被拦属预期——跳过即可，拆书/查询/导出不受影响。

## 词库 = 标准库 + 用户自定义库（可完全覆盖）

- **标准库**（技能自带，`<skill>/config/`）：
  - `taxonomy.yaml` 维度/值域定义；`keywords.yaml` 标签触发词+阈值；`rules.yaml` 切分规则
- **用户库**（每个素材库目录 `<lib>/` 下，存在则自动合并）：
  - `keywords.user.yaml` — 加标签/加词/改阈值（同维度同标签=并集、阈值以用户库为准；新维度直接生效）
  - `taxonomy.user.yaml` — 加维度/加可选值；`rules.user.yaml` — 加章节正则/转场词/长度
  - `characters.yaml` — 人物表（`role` 即打在片段上的「人物标签」，如 男1/女2；代词 我/他/她… 按就近指代自动归属，「我」需该人物标 `pov: true`）
- **完全覆盖（override）**：任一 user 文件顶层写 `override: all`（该文件完全替换标准库）或 `override: [维度/字段,...]`（只替换列出的，未重新给出的 = 删除）。
  - taxonomy 用了 override 后：**未列出的维度不再打标**，列出的维度按 `values` **严格过滤标签值**（例：只留 对话/非对话）。
  - 现成示例：`<skill>/config/examples/only-dialogue/`（「只按 对话/非对话 拆书」的三件套，拷进素材库即用）。
- **关键词（`term`）是内置特征，默认开启**：拆解新书时自动做一次「新词发现」提炼书内专有词 → `<lib>/terms.json`（零 LLM）；已提炼过的书不重跑（你修剪过的词不会被复活）。**不受维度覆盖影响、不需要选择或配置**；整体关闭用 `--no-terms`。手动重炼/批量见快速开始。
- 用户库示例：`<skill>/config/keywords.user.example.yaml`。
- **改完词库/人物表后**：`segment.py <同文件> --lib <lib> --retag`（只重打标签，切片不动）。

## 快速开始

```bash
# 0) （可选，一次问答对整库生效）先定「拆哪些维度/标签、覆盖还是增加」：
#    · 让 AI 问你也行（它用 setup_dims.py --list 展示选项 → 写 plan → 生成配置）
#    · 终端问答 / 快捷预设 / 计划文件：
python <skill>/scripts/setup_dims.py --lib taglib --ask
python <skill>/scripts/setup_dims.py --lib taglib --preset dialogue-only   # 只按 对话/非对话 拆
python <skill>/scripts/setup_dims.py --lib taglib --plan plan.json
#（不设置 = 全部维度默认；设置后对已有片段：segment.py --lib taglib --retag）

# 1) 拆解 + 本地打标（零 token）
python <skill>/scripts/segment.py done/某书.md --lib taglib

# 2) 关键词（term）：内置特征，拆书时默认自动提炼（零 token），无需手动步骤。
#    手动重炼 / 对库里旧书批量补提炼（也可加 --dry-run 预览）：
python <skill>/scripts/mine_terms.py done/某书.md --lib taglib          # 或 --from-lib 全库
#（不满意的词直接编辑 taglib/terms.json 删掉，再 segment.py --retag --only-dims term 生效）

# 3) 人物表：本地挖候选 → 直接补角色标签（不用问用户，按常见映射定即可）
python <skill>/scripts/bootstrap_chars.py done/某书.md --top 30 --write taglib/characters.yaml \
  --role 陆诚=男1 --role 苏晚=女1
#    第一人称小说加 --pov 陆诚（「我」的代词指代自动归属叙述者）
python <skill>/scripts/segment.py done/某书.md --lib taglib --retag --only-dims chars

# 4) 语义补标（可选）：生成批次 → LLM 按协议打标 → 合并
python <skill>/scripts/todo_llm.py --lib taglib
#（读 batch_XX.json → 写 batch_XX.result.json，协议见 references/llm-tagging-protocol.md）
python <skill>/scripts/apply_tags.py --lib taglib taglib/llm_inbox/batch_01.result.json \
  --mark-batch taglib/llm_inbox/batch_01.json

# 5) 多关键字表格（位置 × 标签 × 命中词）
python <skill>/scripts/table.py --lib taglib

# 6) 查询
python <skill>/scripts/list_tags.py --lib taglib                 # 某本/整目录已拆关键字列表
python <skill>/scripts/list_tags.py --lib taglib --book 某书 --library
python <skill>/scripts/query.py --lib taglib --kw 亲吻 --full --limit 5        # 按关键字取回段落原文
python <skill>/scripts/query.py --lib taglib --tag plot:恋爱 --tag scene:对话
python <skill>/scripts/query.py --lib taglib --tag term:咖啡馆                    # 按自动提炼的词条查

# 7) 分存导出
python <skill>/scripts/export.py --lib taglib --by plot
```

## 拆书前先问用户（推荐流程）

用户要拆书、但没说标签范围时，**分两步问答**（一次问答，对整库生效）。
**除这两步问卷外，不要再向用户提问确认——直接执行**（建库、提炼词条、retag、角色映射等都不问；拿不准就按默认先做，后续可改；语义补标是可选步骤，见「敏感内容 · 安全输出」）。

1. **先问维度**：场景 / 人物 / 类型 / 描写 / 自定义维度（默认全要；可只挑几个）
   - 关键词提炼是内置特征：**默认开启、不用问**（关闭用 `--no-terms`）
2. **用户选完维度后，再逐个问所选维度的标签**：展示该维度的默认标签让用户勾选（可只留部分，如场景只留「对话、战斗」），或自定义新标签；提示只列标签本身，不必主动介绍 非xx/其他 这类特殊写法（「其他」默认已在值域里）
   - 人物维度不用问：自动生成「人物ID（人物表）+ 人物数量（<100 精确「N人」；≥100 按数量级）」
3. **覆盖还是增加**：覆盖 = 只用所选的；增加 = 在默认基础上添加

**提问时必须先展示「默认维度」和「每个维度的默认标签」**（不要干问「你要哪些维度」）。直接引用这个清单的输出，或做成多选选项：

```bash
python <skill>/scripts/setup_dims.py --lib taglib --list --human
# 输出：默认维度（含每维默认标签与来源）+ 内置特征说明 + 自动标签 + 本库当前配置 + 建议问法
# （AI 拿结构化数据用 --list 的 JSON；展示给用户用 --human）
```

拿到答复后（AI 操作路径）：

```bash
python <skill>/scripts/setup_dims.py --lib taglib --list          # ① 先看选项（JSON 给 AI；--human 给人）
# ② 把用户答复写成 plan.json（格式见下）→ 一键生成用户库配置
python <skill>/scripts/setup_dims.py --lib taglib --plan plan.json
python <skill>/scripts/segment.py --lib taglib --retag            # ③ 老库重打标签；新库直接开始拆
```

- 生成时会**自动处理细节**：场景要「对话+非对话」自动设置 `dialogue.negative_label`；选中老标签自动带上标准库现成词；写文件前旧配置自动备份 `.bak`。
- 用户也可自己在终端一问一答：`--ask`；只要对话/非对话：`--preset dialogue-only`（等同 `config/examples/only-dialogue/`）。
- 不满意就重跑问卷（会覆盖上一次，`.bak` 里留有旧版）；不运行问卷 = 全部维度默认开启。
- `plan.json` 格式：

```json
{
  "mode": "override",
  "dimensions": {
    "scene":   {"tags": ["对话", "战斗"]},
    "chars":   {},
    "emotion": {"tags": ["愤怒", "开心"]}
  },
  "keywords": {
    "emotion.愤怒": ["怒", "吼", "咬牙"],
    "emotion.开心": {"words": ["笑", "开心"], "threshold": 1}
  },
  "dialogue": {"label": "对话", "negative_label": "非对话"},
  "rules": {"fragment": {"max_chars": 1500}}
}
```

## 作为 Agent 的工作方式

- 用户说「拆书打标」→ 若没说标签范围，先跑 `setup_dims.py --lib <lib> --list --human`，**把默认维度展示给用户并先问维度选择；用户选完后再逐个展示所选维度的默认标签继续问**；最后问「覆盖还是增加」（可做多选）→ 按答复写 plan → `setup_dims.py --plan` 生成配置 → 跑 `segment.py`（本地），汇报章节数/片段数/标签统计；**不要把正文/词条词面/命中词读进上下文，也不要复述**（默认输出已遮挡；不要主动加 `--show-terms`/`--show-ctx` 去翻敏感内容）。关键词提炼默认开启不用问；用户说「默认就行/不用问」→ 跳过问卷直接拆。
- 用户说「关键词/词条从书里提炼，不想手配词库」→ `mine_terms.py`（本地零 token）→ `--retag --only-dims term`；汇报词条数即可（词面默认不回显，用户要看才用 `--show-terms`），并提醒：可直接编辑 `terms.json` 删掉不想要的词条再重跑。
- 用户要人物标签 → `bootstrap_chars.py` 挖候选（默认不带原文上下文，`--show-ctx` 才显示；**不要为了猜性别去读片段/上下文**，拿不准的性别留空优于错判），把 `role` 补成 男1/女2…（直接补，不必再问用户），然后 `--retag`。代词（我/他/她）会自动就近归属到对应人物（本地零 LLM）。
- 词条（`term`）是拆书默认步骤：跑 `segment.py` 会自动提炼并打标，汇报「自动提炼 N 条词条」即可；用户想重炼/批量补 → `mine_terms.py`；想删词 → 编辑 `terms.json` 再 `--retag --only-dims term`。
- 用户想「只要某几类标签」（如只分 对话/非对话）→ 引导用 override：把 `config/examples/only-dialogue/` 的 user 文件拷进素材库 → `--retag`；自定义组合照该示例改 taxonomy 的 dimensions。
- 用户要更准的语义标签 → `todo_llm.py` 生成批次；**你在当前会话里充当 LLM**：读 `batch_XX.json`（每个都不大），严格按 `PROMPT.md` 输出 `batch_XX.result.json`（只有标签，没有正文复述），再跑 `apply_tags.py`。逐批处理，绝不把整本小说或整个 fragments.jsonl 读进来。注意：批次里是片段正文，敏感内容可能被模型安全过滤拦截——被拦就跳过该批（保持未标）、不要重试、不要复述；本地标签不受影响。
- 用户查询/取段落 → `query.py` / `list_tags.py`；默认摘要+ID，用户要原文才 `--full`。
- 优先级：能用本地规则/词库解决的，绝不动用 LLM。

## 数据与目录（`<lib>/`，默认 `./taglib`）

```
taglib/
├── fragments.jsonl   主数据（每行一个片段：位置+标签+命中词+原文）
├── index.sqlite      查询索引（自动重建，无需手管）
├── characters.yaml   人物表
├── *.user.yaml       维度/标签/规则覆盖配置（setup_dims.py 可一键生成；.bak 为旧版备份）
├── terms.json        从书里自动提炼的词条（关键词来源，默认开启；可手改删词/加词）
├── llm_inbox/        待补标批次 + 结果
├── tables/           多关键字表格（csv/md）
└── export/           按标签分存
```

片段行字段：`id, book, chapter, chapter_title, frag_no, start, end, n_chars, dialogue_ratio, tags{维度:[标签]}, hits{"维度:标签":{n,kws}}, source, llm_done, text`

## 查询函数（脚本即函数；也可 `import qlib` 复用）

| 函数（scripts/qlib.py） | 作用 |
| --- | --- |
| `list_books(lib)` | 列书目：片段数/字数/待标数 |
| `list_tags(lib, book, dim)` | 已拆关键字/标签列表（某本或全库，含计数） |
| `keyword_index(lib, book)` | 倒排索引：标签/命中词 → 片段ID |
| `search(lib, tags/book/chapter/kw/mode/limit)` | 组合查询（标签且/或 + 原文关键字） |
| `get_fragment(lib, frag_id)` | 取单个片段全文 |
| `neighbor_fragments(lib, frag_id, n)` | 取相邻片段读上下文 |

对应命令行：`list_tags.py` / `query.py` / `table.py` / `export.py`。

## 调整与扩展

- 加自己的标签：`<lib>/keywords.user.yaml` 写新标签+词+阈值 → `--retag`。
- 拆书前问卷：`setup_dims.py --list`（看选项）/ `--plan plan.json`（按计划生成）/ `--ask`（终端问答）/ `--preset dialogue-only`。
- 完全接管默认库：user 文件顶层 `override: all` 或 `override: [维度/字段,...]`；taxonomy 用了 override 后未列维度不再打标、值域严格生效（「只按 对话/非对话 拆书」现成示例：`config/examples/only-dialogue/`）。
- 改对话/转场标签名：`rules.user.yaml` 的 `dialogue.label / negative_label`、`transition.label`（配合 taxonomy values）。
- 调词条提炼松紧：`mine_terms.py --min-freq / --min-cohesion / --min-entropy / --top`（拆书时的自动提炼按篇幅自适应；很小的样本调低 `--min-freq`）；不想要的词条直接编辑 `terms.json`。
- 调切分粒度：`rules.yaml`（`time_transitions` 转场词、`fragment.min_chars/max_chars`、章节正则）。
- 详见 `references/segmentation-tuning.md`。

## 注意

- 依赖：Python 3.9+（仅标准库）；建议装 PyYAML（没有则用 .json 配置）。
- 重复执行幂等：已切片段自动跳过；`--force` 重切重标；`--retag` 只重打标签。
- 偏移量是章节正文内的近似字符偏移（用于定位，不保证与原文件字节完全一致）。
- 章节标题之前的简介/目录自动跳过；`***`、`---`、「本章完」等分隔行自动识别。
