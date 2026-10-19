# 切分规则与调参指南

## 切分三级流程

1. **章节切分**（`split_chapters`）
   - 按行匹配 `chapter_patterns`（第N章/回/节/卷、番外、Chapter N…），行内出现即可，不要求整行是标题。
   - 自动跳过：目录表格行（以 `|` 开头）、`<a id=...>` 锚点行、超过 `chapter_title_max_len` 的长行。
   - 章节标题之前的内容（书名/简介/目录）整段跳过。
   - **识别不到章节** → 整个文件当一章处理。
2. **场景切片**（`split_scenes`，在章内进行）
   - 整行为分隔标记（`***`、`---`、「本章完」等 `break_markers`）→ 切。
   - 段落以**时间转场词**开头（第二天、半小时后、多年后… `time_transitions`）→ 切。
   - 段落以**视角切换词**开头（与此同时、另一边… `refocus_prefixes`）→ 切。
   - 片段超过 `fragment.max_chars` → 在段落边界切开；小于 `fragment.min_chars` → 并入相邻片段。
3. **打标**（`tag_fragment`）
   - 对话：引号内字数占比 ≥ `dialogue.ratio_threshold` 或 引号开头段落占比 ≥ `paragraph.ratio_threshold`。
   - 转场：片段开头命中转场词表。
   - 关键词维度：`keywords.yaml` + 用户库；命中次数 ≥ 该标签 `threshold`。
   - 人物：`characters.yaml` 的名字/别名出现即打「人物ID」标签；另自动附加「人物数量」（<100 精确「N人」；≥100 按数量级「上百人 / 上千人 / 上万人」，也识别「数百名观众」类文本估算；只统计总人数，不区分男女老幼）。

## 常见问题与调法

| 现象 | 调整 |
| --- | --- |
| 切得太碎 | 精简 `time_transitions`（去掉太常见的词，如「清晨」）；调大 `fragment.min_chars` |
| 一段混了多个场景 | 往 `time_transitions` / `refocus_prefixes` / `break_markers` 加词；调小 `fragment.max_chars` |
| 章节识别不到/误判 | 改 `chapter_patterns`（正则）；文件若是 txt 建议先过一遍 `txt2md.py` |
| 对话标签不准 | 调 `dialogue` 两个阈值；确认 `quote_pairs` 覆盖了你文本里的引号样式 |
| 某些标签老是误报 | 提高该标签 `threshold`；删掉歧义关键词（改用户库即可，不用动标准库） |
| 人物标签缺失 | 用 `bootstrap_chars.py` 补人物表；别名写全（昵称、姓氏称呼都要列） |
| 只想要「对话/非对话」或只保留某些维度 | 用 override：`config/examples/only-dialogue/` 拷进素材库 → `--retag`；taxonomy override 后未列维度自动停用、值域自动严格（关键词是内置特征不受影响；不要就 `--no-terms`） |
| 词条提炼不想要/想重炼 | 编辑 `<lib>/terms.json` 删词后 `--retag --only-dims term`；重炼跑 `mine_terms.py`；整体关闭用 `--no-terms` |

## 用户库的作用域

- 所有 `*.user.yaml` 放在**素材库目录**（如 `taglib/`），只影响该库。
- 合并优先级：用户库覆盖/追加标准库；同标签关键词取并集、阈值以用户为准。
- 改完任何规则/词库/人物表：`segment.py <文件> --lib <lib> --retag`（可配 `--only-dims` 缩小重算范围）。

## 拆书前问卷（setup_dims.py）

拆书前用一次问答定「要哪些维度/标签、覆盖还是增加」（对整库生效）：

- **两步问**：① 先问要哪些维度（场景/人物/类型/描写/自定义；默认全要）→ ② 用户选完后再逐个问所选维度的标签（展示该维度默认标签）。人物维度跳过（自动 人物ID + 人物数量）；关键词提炼是内置特征，默认开启、不用问。
- `--list [--human]` 看可选项（JSON / 中文清单：默认维度、每维默认标签、本库现状、建议问法）；`--ask` 终端问答；`--plan plan.json` 按计划生成（AI 推荐路径）；`--preset dialogue-only` 快捷预设。
- 产物 = `<lib>/taxonomy.user.yaml / keywords.user.yaml / rules.user.yaml`（旧文件自动备份 `.bak`）；不满意重新跑即可。
- 自动处理：选中老标签自动带上标准库现成词；场景同时要「对话+非对话」自动设 `negative_label`；覆盖模式下没列出的维度自动停用、值域严格。
- 问答后：老库 `segment.py --lib <lib> --retag`；新库直接开始拆。

## 场景语义（唯一拆分标准）

- 只有 `scene` 决定切分（章节 / 转场词 / 长度等场景边界信号）；人物、类型、描写、自定义维度都是**切好片段上的特征标签，不参与切分**。
- 场景特征**一般不互斥**：一个片段可同时是 战斗 + 吐槽；多特征命中的文本留在同一片段（**偏大优先**，不过度切碎），片段互不重叠、过小自动并邻。
- 两个例外（都只需写进 `taxonomy.user.yaml` 里 scene 的 `values`）：
  - **`xx` / `非xx` 成对** → 互斥；未命中 `xx` 的片段自动补 `非xx`（例：values 里加上「非对话」即可，等价于不再手配 `negative_label`）。
  - **「其他」**（标准值域默认必含，向导生成配置时自动带上）→ 兜底：没有任何场景标签的片段自动归入；若同时有 `非xx`，其他自然不命中，无需特殊处理。

## 代词指代（本地，零 LLM）

人物 ID 不仅匹配名字/别名，还会把代词按语义就近归属到人物，**不需要 LLM**：

- **规则**（`rules.yaml` 的 `chars.pronouns`，代词 → 男/女/pov）：
  - `他/他们→男`、`她/她们→女`：取「此前最近的同性别提及」（本片段 + 跨片段回溯窗口 `carry_window`，默认 3000 字符、每章重置）；片段开头没找到就再看「本片段内其后的最近提及」。
  - `我/我们→pov`：第一人称叙述者。需在 `characters.yaml` 给该人物标 `pov: true`（或顶层 `pov: 名字/role`，`bootstrap_chars.py --pov 名字` 也可）；**仅引号外的叙述行生效**，对话里的「我」不归属。
- **性别来源**：人物条目 `gender: 男/女`，或 role 里含 男/女（如 男1/女2 自动推断）。
- **证据可追溯**：命中记在 `hits` 里（如 `"他（代词）"`），table.py 可见；归属同时会让人物数量更准（如只写「他推开门」的片段也能带上对应人物 + `1人`）。
- **边界**：同性别多人挨着出现时可能归错人（就近启发式）；这类模糊片段可选择性交给 LLM 补标修正（`todo_llm.py`），不修正也不影响使用。

## 词条自动提炼（mine_terms.py）

- 原理：无监督「新词发现」——在句子内的中文连续段取 2-4 字候选，按 频次 × 凝固度(PMI) × 左右邻字熵 过滤；零 token、不依赖分词库。
- 常用命令：
  - `python mine_terms.py done/某书.md --lib taglib`（单本；结果合并进 `<lib>/terms.json`）
  - `python mine_terms.py --lib taglib --from-lib`（对库里所有书提炼，用已拆片段原文）
  - 加 `--dry-run` 只预览不写入。
- 调参：
  | 现象 | 调整 |
  | --- | --- |
  | 小样本提不出词 | 降低 `--min-freq`（如 3）；样本 <500 字会跳过 |
  | 词表里虚词/套话太多 | 提高 `--min-cohesion`（如 3.0）/ `--min-entropy`（如 1.2） |
  | 想要的词没进来 | 降低 `--min-freq`；或直接在 `terms.json` 里手写一条 `"term": "某词", "freq": 999` 再 retag |
  | 词太多/太杂 | 调小 `--top`；或编辑 `terms.json` 删词 |
- 与标签的关系：**关键词是内置特征，拆书默认自动提炼**——新书首拆时自动做一次，库里旧书在 `retag` 时自动补提炼；已提炼过的书不会重跑（手动重炼用 `mine_terms.py`）；**不受维度覆盖影响**，关闭用 `--no-terms`。词条命中会以 `term:词条` 形式打到片段上（已有片段用 `segment.py --retag --only-dims term` 刷新）。人物表里的名字不会重复出现在词条里。
- 已内置过滤：通用虚词/量词/方位词停用表；`chapters` 字段（词条出现在哪些章节）可帮助判断它是全书主题词还是局部插曲词。

## 完全覆盖默认库（override）

用户库任一文件顶层可声明：

```yaml
override: all            # 本文件完全替换标准库对应部分
override: [scene, plot]  # 只替换列出的维度/字段（未重新给出的 = 删除）
```

- `taxonomy.user.yaml` 用 override 后：
  - 未列出的维度不再打标（如只要 scene → chars/plot/style 全部关闭；**关键词是内置特征，不受影响**，不要用 `--no-terms`）；
  - 列出的维度按 `values` 严格过滤：不在值域里的规则/关键词标签会被丢弃（如 scene 只留 对话/非对话 时，转场标签自动消失）；
  - `llm_dimensions` 同步清理；全部维度 method 都不含 llm 时，`todo_llm.py` 会直接跳过（不生成无意义的 LLM 批次）。
- `keywords.user.yaml` / `rules.user.yaml` 的 override 同理：关键词维度整体替换 / 顶层字段整体替换（如 `override: [dialogue]` 后自定义对话阈值与标签名）。
- 现成示例：`config/examples/only-dialogue/`——把 3 个 user 文件拷进素材库 → `--retag` → 只剩 `scene:对话` / `scene:非对话`（关键词默认开，还会带 `term:` 标签；不要就 `--no-terms`）。
- 对话/转场标签名可改：`rules.user.yaml` 的 `dialogue.label / negative_label`（negative_label 让不达标的片段也有标签，如「非对话」）、`transition.label`。

## 已知边界

- 偏移量为近似字符偏移（按规范化后的正文计算），适合定位，不承诺与原始文件字节一致。
- 关键词打标是「高召回起点」，语义精度靠 LLM 补标或用户库迭代；不要期待全自动百分百准确。
- 每章开头若本身就是转场（比如章名后直接「第二天」），会被打上「转场」标签，属预期行为。
