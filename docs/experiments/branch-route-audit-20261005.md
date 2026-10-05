# 2026-10-05 上下文路线与分支整理审计

## 用户要求与实际实现

最初用户要求本地与 GitHub 仅保留平等维护的 `New` / `Old` 两条分支，
其他代码保持一致，仅区分上下文获取入口。经澄清，指现有 TSF 路线与
直接 Windows 原生读屏接口路线；后者应指 UI Automation（UIA）。
确认 UIA 尚未实现后，用户改为：统一保留一个 `main`，另提 issue
说明未来 New 路线。本次不实现 UIA，不保留 New/Old 占位分支。

当前代码仅实现 TSF 取文：

- `native/tsf/surrounding_text_edit_session.cc` 使用 `ITfRange::GetText`
  读取 UTF-16，保护字段先受采集策略限制。
- `native/context/context_update_bridge.cc` 使用
  `WideCharToMultiByte(CP_UTF8, WC_ERR_INVALID_CHARS, ...)` 转为 UTF-8
  传输。因此 UTF-8 与 TSF 并非两套独立获取接口。
- `docs/architecture/context-prompt.md` 的
  `Deferred Windows context acquisition` 明确把直接 Windows/UIA 取文
  列为未实施任务。
- FIM 改变模型上下文编码和评分，不改变编辑器取文入口，不能作为此次
  两条分支的区别。
- 已删除的历史 Lua translator 只收集输入法自身 commit history；不是
  编辑器周围正文。恢复它还会改变前端与传输架构，不满足其他代码一致。

未来路线任务已创建：
[issue #43：New 上下文路线：评估并实现 Windows UIA 取文 provider](https://github.com/Yi-frank-phy/neural-weasel/issues/43)。
它是 #39 的 provider 子任务，明确接口方向、安全边界及真实 Windows
编辑器验收；不把 FIM 或 UTF-8 编码冒充新的取文能力。

## 共同代码基线

整理前最新本地与远端工作分支 `codex/context-engine-fim-phase1` 均指向
`b3e3b62f4b7fd10bab9d754b50353d948bb2ce1a`；远端 `main` 指向
`d4fd82431cb4c79113a1aa4cb90eb6cf2189de8b`。

两者差异是最新一个 55 文件提交，包含候选搜索、缓存界、锁预算、
PRIVATE 清理、桌面句柄及管道/receipt 恢复等通用改动。
`Old=db73b703e0de71241c14e8691eb67d488adf7492` 会遗漏这些通用改动，
不满足最初要求。未来取文路线应共用最新代码基线，而非使用新旧快照。
这里未重新证明最新全部实验代码适合生产部署；分支整理不等于部署。

## 历史分支审计与恢复

已读取 35 条远端与 12 条本地分支。7 条远端 tip 直接被 `origin/main`
包含：`main`、`agent/q4-runtime-selector`、`codex/combine-candidate-ui`、
`codex/fix-acceptance-r1-r2-20260907`、
`codex/pr36-cloud-handoff-20260906`、`task4/surrounding-text-capture`、
`tmp-unused`。

已核对全 tree 等价：

- 本地 `0e2708d` 与主线历史 `7d19d24`。
- 远端 `23212a0` 与主线历史 `084a125`。
- 历史 C2 `cbb2988` 与验收整合链 `63011ae`；整合终点
  `0e2708d` 又与主线 `7d19d24` 等价。

其余分支不能一概声称已合并。尤其 `c820a5f` 是候选流水线大型快照，
`a8b7a4e` 包含旧单键固定页快速路径；当前已重构，未证明该路径等价
保留，不宜直接移植旧提交。删除历史分支指针必须依靠以下完整备份，
保留恢复能力，不能将删除说成已合并。

备份目录：
`C:\Users\zhaoy\Downloads\neural-weasel-branch-backup-20261005-231001`。

- `all-refs.bundle`：已通过 `git bundle verify`。
- SHA-256：`4D7F12C3208333039E34AC707B077C82514BA569C9597A1CC9405230AF5C8118`。
- `refs-before.txt`：完整本地及 remote-tracking 指针清单。
- `remote-heads-before.txt`：当时 GitHub live heads 清单。

整理前失效 worktree 登记
`C:/Users/zhaoy/Downloads/neural-weasel-fix40` 指向不存在的位置，占用
`fix/single-syllable-latency`；已再次确认目录不存在，并仅通过
`git worktree prune` 清除失效 Git 登记，没有删除真实工作目录。

GitHub 当时还有 10 个旧 open PR：#41、#35、#34、#33、#32、#30、
#26、#25、#23、#21。它们涉及旧分支；最终整理须保留 PR 历史并明确
关闭或分支删除的影响，不能把它们盲目合入共同基线。

## 最终整理结果

- `50c512c647148b2c0b1b02ddca41d58679a48aa2` 无冲突合并既有远端
  `main` 历史与 `b3e3b62`。已用 `git diff --exit-code b3e3b62 HEAD`
  验证合并后受版本控制内容一致；未重放过时诊断代码或声称所有历史
  独有实验均已整合。最新通用修复、FIM 实验及已有记录均在 `main`。
- 本地工作分支改名为 `main`，追踪 `origin/main`；远端默认分支仍为
  `main`，不需要修改仓库设置。
- 10 个上述旧 PR 已通过 GitHub connector 关闭，保留 PR 历史，
  没有将关闭标记为合并或功能完成。
- 34 条其他远端分支已通过一个 atomic push 删除，每条 ref 使用
  整理前 SHA 的显式 lease；删除前再次读取 live heads 核对无变更。
- 其他本地分支指针已删除。历史对象、标签、stash 和 Codex 内部 refs
  不在分支清理范围内；完整 bundle 保留删除前所有 tips 的恢复能力。
- 已用本地 `git branch -avv`、`git worktree list --porcelain` 及 GitHub
  branches / open pulls / repository API 独立读回：仅一个本地 `main`、
  一个远端 `main`、一个真实 worktree、零 open PR，默认分支为 `main`。
- 分支整理后的主线源码与已同步工作内容相同，本次仅新增本审计文档；
  不新增运行时代码，不部署，也不重新执行输入法实机测试。

恢复旧分支时，从外部 bundle 获取指定完整 ref；例如：

```powershell
git fetch C:/Users/zhaoy/Downloads/neural-weasel-branch-backup-20261005-231001/all-refs.bundle refs/heads/fix/single-syllable-latency:refs/heads/recovery-single-syllable
```

恢复命令会有意新增本地分支，仅在需要恢复时执行。

本审计只检查源码、Git 历史及远端状态；未操作前台、输入法服务或
生产安装，未将静态审计冒充真实编辑器验收。
