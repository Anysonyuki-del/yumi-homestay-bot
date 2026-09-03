# 供应来源

本目录是随仓库固化的第三方技能，不是 YuMi 民宿 AI 的自有代码。

- 名称：impeccable
- 版本：4.1.2（见 `SKILL.md` 的 `version` 字段）
- 上游：https://github.com/pbakaus/impeccable
- 许可证：Apache-2.0，副本见同目录 `LICENSE`

固化原因：`.codex/hooks.json` 在 `PostToolUse` 与 `Stop` 两个时机调用
`.agents/skills/impeccable/scripts/hook.mjs`。只提交 hooks.json 而不带脚本树，
克隆后钩子会因找不到文件而失效，因此两者必须一起进入仓库。

本目录内容未经修改。升级请使用上游发布流程，不要就地编辑。
