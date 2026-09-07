# PR36：本机、云端与 Sol 交接（2026-09-06）

## 基线与范围

- 仓库：`Yi-frank-phy/neural-weasel`；PR：[#36](https://github.com/Yi-frank-phy/neural-weasel/pull/36)，必须保持 Draft，不 merge/release。
- 交接分支：`codex/pr36-cloud-handoff-20260906`；PR head 分支：`codex/combine-candidate-ui`。交接提交同步到两者；后续工作从交接分支对应的完整 SHA 创建独立任务分支。
- 原始源码快照：`cf44216945c5aa2c7b7f3d834bf0c124a1c9f154`，父提交 `c22082a3c304f52a108eb0e328f3a4e76fa2c719`。包含本机尚未上传的 37 个已跟踪文件改动和 2 个新文件。后续提交只做格式整理与本说明。
- 这是一份含已知缺陷的工作基线，不表示修复完成或目标机验收通过。旧 PR 描述中“仅剩本机验收”的判断已过时。
- 本轮工作是同步和拆分任务；下面的实现任务尚未派发，也未执行。

## 本机已完成与仍需完成

本轮已完成：源码审查、39 个源文件复制及 SHA-256 校验；原工作区全套 Python 测试 412 passed；分页/并发两组测试 26 passed；使用现有假运行时复现“旧任务占用模型，新 revision 搜索退出且首页读取不重启”的问题。没有启动 Q4、切换输入法或重新部署。

交接副本验证：5 个文件经 Ruff 格式整理，逐文件 Python AST 与原始快照相同；明确设置交接副本 `src` 为导入路径后重跑 `python -B -m pytest -p no:cacheprovider`，412 passed（9.29 s）；`ruff check .`、`ruff format --check .`、`git diff --check` 通过。再次检查原目录全部 39 个源文件的 SHA-256 未变。新 head 的 hosted CI 和原生 Windows bundle/CTest 本轮尚未完成，不能套用 `c22082a` 的历史结果。

此前目标机任务报告（2026-09-04，非本轮重新测量）：部署过 progressive-continuation/budget2500 版本，`hywt -> 还有问题` 可用，`mxbd -> 明显不对` 仍失败；page0 服务耗时记录为 26–69 ms、pipe timeout 为 0；任务结束时 UI 和单个 Q4 服务已停止。以上不能外推为 UI p95 或当前运行状态。

必须留在实际 Windows 目标机的工作：

1. 云端修改完成后，构建或核验同一 SHA 的 bundle，备份并更新产品自有安装文件，保留用户默认输入法及 RimeUser 自定义配置。
2. 用真实编辑器验证首键可见延迟、完整候选更新延迟、翻页/退格/分隔符、数字 1–9（含 9）、中英文切换、选中项稳定性、闪烁和混合输入。
3. 使用单个模型服务验证 CUDA 忙/超时后的恢复和显存占用。对 `mxbd` 做真实 Q4 定点诊断；不得为对照额外启动第二个 Q4 服务。
4. 验证实际应用里的密码/PIN/受保护输入零采集、PRIVATE 不持久化、跨焦点旧结果拒绝，以及 Win+Space、故障恢复、回滚和卸载。

云端 Windows CI 可以编译原生代码、执行 CTest 和安装 dry-run；它不能替代上述目标机验收。Q4/Q6/Q8 是运行时选择，Q8 并未被禁止。

## 可分配的任务

| 编号 | 执行位置/建议模型 | 任务 | 依赖与文件所有权 |
| --- | --- | --- | --- |
| C1 | 云端，较强模型 | 修复后台搜索、真实输入 revision 与 UI 展示更新的生命周期 | 统一负责 `src/neural_weasel/neural_candidate_pages_scored.py`、必要的 v2/协议接口、`native/rime/ai_translator.*`、`scripts/prepare-weasel-overlay.ps1`；若需要 processor 改动，等 S1 完成再集成 |
| C2 | 云端，较强模型 | 后台准备后续页，翻页读取已准备结果 | 依赖 C1；负责 pager v2/v3/scored 及分页协议测试，避免和 C1 同时改这些文件 |
| S1 | 云端或本地隔离分支，Sol | 修复英文组合态吞 `-`/`=` | 仅 `native/rime/bilingual_key_processor.cc`、`bilingual_key_semantics.*` 及对应原生测试；不改刷新调度 |
| S2 | 云端或本地隔离分支，Sol | 将忙模型/新 revision 的复现做成确定性的回归测试 | 新建 `tests/test_candidate_background_retry.py`，复用现有假运行时；先交 RED 测试提交，再由 C1 使用；不改生产代码 |

推荐顺序：S1 与 S2 可独立并行；C1 使用 S2 的复现，并在必要时集成 S1；C2 接在 C1 后；最后回目标机验收。每个任务独立分支和提交，不同时向公共交接分支推送。不额外安排大规模 pager 继承重构。

### C1：搜索与展示生命周期

已确认的代码路径：

- `scripts/prepare-weasel-overlay.ps1` 设置 850 ms、一次刷新；合成刷新通过 `native/rime/ai_translator.cc` 设置 `force_new_revision_`，增加 composition revision、清空 frozen pages。
- scored pager 的后台预算是 2500 ms。新 revision 的 `_new_session` 会删除同一客户端的旧会话；旧任务完成时被身份检查丢弃。
- 后端 single-flight 忙时返回 `None`；后台 `progressed <= 0` 即退出。相同 revision 的 page0 直接返回冻结页，没有恢复后台工作的机会。
- `_ConsiderNeuralRefresh` 只为未按修饰键的 A–Z 安排刷新；Backspace/分隔符取消刷新。

目标：只有真实输入/模式/焦点/上下文身份变化才使旧搜索过时；结果完成后有受身份约束的展示通知；模型忙保留最新有效请求，模型空闲后继续。采用哪种通知机制由 C1 设计，但不能简单延长计时器或无条件反复刷新。

验收：

1. 假运行时在 850 ms 之后、2500 ms 以内才给出完整候选，仍能通过有效展示通知到达 UI；展示更新不伪造用户输入 revision。
2. 旧计算正在运行时发生新输入，旧结果不发布，新输入会在模型空闲后自动继续；无需再按键或翻页。
3. 快速连续输入最终只服务最新有效身份；密码/焦点切换后无旧结果发布；线程、事件和缓存有界，无忙轮询。
4. 退格、分隔符同样能安排新输入的后续候选更新。保留已发布页和候选 ID 的稳定性；若替换首页，必须有明确展示版本及安全的选中项策略。
5. TSF 保持 capture/send-only，不在 DLL 中新增模型、Python IPC 所有权或阻塞后台等待。

### C2：翻页读已准备结果

当前 `_freeze_next_page` 对后续页调用 `_ensure_freezable`，会续算；不足整页且未 exhausted 时抛超时。原生下一页请求 timeout 为 120 ms，后台任务没有直接冻结后续页。

目标：后台逐步准备后续页；PageDown/PageUp 只读取稳定页面。未准备好时保留当前页/选中项，并返回明确的可重试状态。不要用同步模型计算补足翻页请求。

验收：PageDown 不增加 runtime 模型调用；后台完成后可读取下一页；同一已发布页永不重排；取消后拒绝旧页；不足整页、搜索耗尽、候选数上限时的 `has_more` 与边界一致。上限边界是待补覆盖的审计项，本交接未声称已经复现上限缺陷。

### S1：英文标点按键

`IntentFor` 把 `=`/`-` 映射为翻页，与语言模式无关；翻页分支在没有更多页或已经位于首页时仍返回 Accepted。英文组合态会吞字符。

目标：英文模式的 `-`/`=` 按正常文字/标点输入路径处理；中文模式保留现有翻页行为；真实 PageUp/PageDown 保持明确导航语义。检查按键路由和最终提交文本，而不只检查枚举映射。

验收：英文组合态下两字符不丢失；中文翻页、Shift 作为修饰键、Ctrl/Alt 快捷键与数字输入无回归。不要顺手改变 Space/Tab 或完整英文候选产品设计。AGENTS.md 中英文 candidate bar 与现有 Tab 补全的差异另行处理。

### S2：最小 RED 复现

参考 `tests/test_candidate_page_concurrency.py` 的 BlockingContinuationRuntime 和 make_index：

1. 用仅含“你”“好”的假词元索引和 fake logits，启动客户端 revision 1 的 `nihao` 首页，等待 continuation.started。
2. 保持旧 provider 阻塞，查询同客户端 revision 2 的 `nihao` 首页。
3. 确认新 revision 的后台尝试已经遇到 busy，再释放旧 provider。
4. 期望最新有效任务自动恢复并产生“你好”，且 revision 1 的结果不能直接污染 revision 2。

本轮临时复现的实际结果：新后台在旧 provider 释放前结束；释放后再次读取 revision 2 首页，provider 调用次数仍为 1，async completed cache 为 0，候选仅“你”。测试应断言期望行为并在当前基线 RED；用事件/屏障和有界等待，避免依赖碰运气的短 sleep。不需要下载模型、GPU、真实编辑器文本。

## 公共约束与交付要求

- 先读仓库 AGENTS.md；纯神经 model-token-path 候选，拼音索引只判断合法性，不能回退为静态词频排名或传统词典候选。
- 中文优先/英文优先是显式模式；英文候选不得输出 Han；保留中文模式的 Latin 候选路径，不把混合输入简化成全局语言锁。
- 密码/PIN/受保护字段零上下文采集；PRIVATE 仅临时使用；不得上传或记录真实编辑器文本、凭据、RimeUser、模型权重及本机运行日志。
- 分别报告自动化证据和目标机证据；不得把旧 head 的绿色 CI 记到新提交上。
- 每个执行者返回：任务编号、完整 commit SHA、修改文件、RED→GREEN 证据（S2 返回预期 RED）、测试命令及结果、仍需本机验证的项目。
- 本机原工作区 `pr36-candidate-latency` 及主仓库脏文件继续保留；后续集成从交接 SHA 出发，不对原目录强制 reset/clean。

## 可直接复制的任务提示

云端 C1：在 `Yi-frank-phy/neural-weasel` 的 `codex/pr36-cloud-handoff-20260906` 确认完整 HEAD SHA，阅读本文件，执行 C1；先接入 S2 的 RED 复现（如尚未提供则自己创建），保持 PR36 Draft，不 merge/release，不操作实际 Windows 安装。实现和验证后返回完整 SHA、测试证据、本机验收清单。

Sol S1：从同一交接 SHA 建独立分支，阅读本文件，只执行 S1 英文 `-`/`=` 丢字符修复及对应测试。不得修改 pager、刷新调度或 Space/Tab 产品规则。返回 commit SHA、测试结果，不合并 PR36。

Sol S2：从同一交接 SHA 建独立分支，阅读本文件，只执行 S2，在新测试文件中提供确定性 RED 复现。不要修改生产代码或使用真实模型。返回测试提交 SHA、失败断言及复现命令；不要把预期 RED 宣称为通过。
