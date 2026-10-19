# 示例：只按「对话 / 非对话」拆书

想要极简标签时的完整配置（3 个 user 文件）。把本目录下的 `*.user.yaml` 拷到素材库目录，然后重打标签：

```bash
python <skill>/scripts/segment.py --lib taglib --retag
```

之后整个库的标签只剩 `scene:对话` / `scene:非对话`（关键词提炼是内置特征、默认开启，所以还会有 `term:` 标签；不想要就加 `--no-terms`）：
- `taxonomy.user.yaml`（override: all）→ 只保留 scene 维度、值域锁定为 对话/非对话
- `rules.user.yaml`（override: [dialogue]）→ 对话规则给出「对话」，不达标的给「非对话」
- `keywords.user.yaml`（override: all）→ 清空关键词词典，不再有 战斗/恋爱/心理描写 等标签

撤销/回退：删掉素材库目录里的这几个 user 文件（或改名备份）再 `--retag` 即可，`terms.json`、`fragments.jsonl` 不受影响。

更多组合思路：
- 只想要「对话检测 + 人物标签」→ taxonomy 里只列 `scene` 和 `chars` 两个维度（chars 无需配标签：自动出 人物ID + 人物数量）
- 想改标签名，比如「对白 / 叙述」→ 把 values、dialogue.label、dialogue.negative_label 一起改成同名
