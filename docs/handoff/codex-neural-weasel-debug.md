# Handoff: Neural Weasel target-machine candidate and latency RED

最新续接入口：文末「2026-10-02 根治修复交接」。当前真人候选显示和数字键提交正常，间歇超时及隔夜回收验收尚未闭合；待部署补丁与已安装版本已分开记录。下面 2026-08-31 的开头状态是历史基线。

Updated: 2026-08-31 (Asia/Shanghai)

## Current user-visible state

The experimental Neural TSF is registered and can show and commit Chinese candidates, but it is **not accepted as healthy**. The latest target-machine report is:

> 卡，候选诡异，很晚出，甚至没有词组就不出，长了也不出，而且无法数字选择

Backend protocol checks are green, but they do not prove the live TSF UI. Keep generation, display, selection, commit, and latency as separate acceptance dimensions.

## Branch scope ready for cloud review

Branch: `codex/combine-candidate-ui`, based on `origin/agent/q4-runtime-selector` (`31aa7f6`).

The change set at this handoff contains:

- opaque candidate-window colors without changing geometry/font defaults;
- removal of the internal `[coverage]` marker from visible comments;
- hard ranking tiers: exact pinyin before extension, fuzzy, and Latin; model scores rank only within a tier;
- English-context Latin candidates before Han candidates;
- Chinese number keys passed to Rime's selector, while English number keys remain literal input;
- regression tests for the above behavior.

Validation already completed against this exact source set:

- Python: `322 passed` (targeted candidate/UI subset: `37 passed`);
- native CTest: `9/9 passed`;
- `git diff --check`: passed;
- real Weasel/xmake build relinked both changed native sources;
- built bundle verification and installer dry-run: passed.

Installed binary hashes from that build:

- `NeuralWeaselExperimentalTSF.dll`: `9a809ebcd5a8ce54e9b70df14e7acb98658e70b055234a786f83cb7157bfef12`
- `NeuralWeaselServer.exe`: `0281a17818472242cee388bb8be3def3a91395de973e61ce46b45d158d75a498`

## Confirmed RED 1: installer leaves the old managed schema active

The source fix orders processors as:

```yaml
- bilingual_key_processor
- selector
- speller
- punctuator
```

The installed bundle contained that schema, but `scripts/install-dev-profile.ps1:267-272` only copies files from `rime-user` when the destination does not exist. The target therefore kept its 2026-08-04 schema with the old order:

```yaml
- speller
- bilingual_key_processor
- punctuator
- selector
```

Because `speller.alphabet` includes digits, the old runtime order consumes number keys before `selector`. This is the demonstrated cause of the number-selection failure.

Target-machine evidence:

- old live schema hash: `6EFE754AE7678D56DC4D313F13622D002BEF52BCAE46A0550D8F445BAD8DF28D`;
- bundle/new schema hash: `356F55673F236B4A5A832BCDAE9DCBF589FECA0B2C42C46961E151E3FFBE11D1`;
- even after copying the source schema, `RimeUser/build/neural_weasel.schema.yaml` remained old until it was patched explicitly.

The live source and generated schema were migrated manually and the experimental server was restarted. This still needs a user manual number-key retest.

### Cloud-safe next work

Add a regression and change installation/deployment so product-owned `neural_weasel.schema.yaml` is upgraded atomically. Preserve user-owned `*.custom.yaml` and unrelated configuration. Do not broadly overwrite `weasel.yaml` or the entire `RimeUser` directory.

## Confirmed RED 2: context refresh can starve the 50 ms query path

Metadata-only `ai-translator.log` evidence from the real TSF session:

- one focus/context transition left input lengths 1/2/3 at `identity-valid=0 model-epoch=0`; the first usable epoch arrived about `4359 ms` later;
- at epoch 33, the first three inputs returned `pipe-failure status=2 error=121` around the native 50 ms deadline; epoch 34 returned candidates about `5.6 s` later;
- epoch 38 repeated the three 50 ms failures; epoch 39 then returned candidates;
- successful queries are usually 0-47 ms, so the failure is correlated with model refresh rather than permanent pipe loss.

Relevant defaults:

- `native/rime/ai_translator.h`: `query_timeout_{50}`;
- `LlamaCppBackend`: `max_before_tokens=23552`, `n_ctx=24576`, `n_batch=512`;
- the Q4 launcher currently does not expose overrides for those values;
- `create_snapshot()` performs `llama.eval()` while holding the backend lock;
- there is no ordinary pinyin dictionary fallback in the deployed schema, so epoch 0 cannot produce Chinese candidates.

A synthetic, non-private benchmark against the running Q4 service measured refreshes of approximately 608 ms (64 synthetic words), 785 ms (256), 1192 ms (512), and 1219 ms (1024). A separate probe observed an old-snapshot query taking 788 ms during refresh. The Python client's timeout limits connection acquisition, not the response read, so use these as starvation evidence, not as a native deadline pass.

### Cloud-safe next work

- Add latency/refresh diagnostics that record durations and token counts only, never raw editor context.
- Keep the production context-window/runtime parameters explicit and testable at 23552/24576/512.
- Reproduce query starvation with a controlled blocking backend and prove that immutable previous snapshots remain queryable while a new snapshot is computed.
- Evaluate a bounded target profile (for example a smaller retained left context) with tests, but do not claim a parameter value is fixed until target-hardware measurement.
- Keep all model work outside the TSF DLL.

Do **not** paper over this by increasing the native 50 ms timeout, reusing an epoch from another focus/session, relaxing identity checks, or moving model work into the TSF process.

## Work that requires this Windows target machine

These cannot be completed honestly by cloud CI alone:

1. Confirm number keys 1-9 select the visible candidate after the live schema migration; confirm English digits remain literal.
2. Type short and long pinyin in a real editor and correlate each visible result with metadata-only epochs and query timings.
3. Rebuild the pinned Weasel/librime overlay, create the Windows bundle, perform the UAC install, and verify installed hashes.
4. Verify registration, `Win+Space`, default-input-method preservation, normal editor typing, real surrounding context, password/PIN zero capture, server-failure behavior, latency, uninstall, and recovery.
5. Repeat on the target GPU with Q4/Q8 choices treated only as runtime choices; functional ranking and security contracts remain quantization-independent.

## Target-machine recovery state

- Isolated worktree: `C:\Users\zhaoy\Downloads\neural-weasel-target-machine\combined-candidate-ui`
- Runtime: `C:\Users\zhaoy\AppData\Local\NeuralWeasel\Experimental\experimental-profile`
- Q4 GGUF: `C:\Users\zhaoy\AppData\Local\NeuralWeasel\gguf-poc\models\Qwen3.5-4B-Q4_K_M.gguf`
- Current bundle: `C:\Users\zhaoy\Downloads\neural-weasel-target-machine\combined-candidate-ui\dist\candidate-ranking-fix-20260831-1945`
- Schema rollback directory: `C:\Users\zhaoy\Downloads\neural-weasel-target-machine\backups\rime-schema-before-ranking-fix-20260831-2230`
- Pinned Weasel tree: `C:\Users\zhaoy\Downloads\neural-weasel-build-deps\weasel-combined` at `9cc96e20dc71b80876b12f689bb5863c76c2a7ed`
- Pinned librime: `1c23358157934bd6e6d6981f0c0164f05393b497`

The default input method was not changed. The experimental model and server processes may simply disappear at machine shutdown; no remote state depends on them.

## Privacy/security invariants

- Password/PIN/protected fields: zero surrounding-text capture.
- PRIVATE context may be used ephemerally but must not persist.
- Raw editor context must not enter logs, databases, telemetry, caches, crash artifacts, or Engram.
- Context transport remains bounded, one-way, nonblocking, identity-checked, and latest-revision-wins.
- Stale focus/session/revision state must never publish candidates into a newer editor context.
- Do not upload target logs without first confirming they contain metadata only.


## 2026-10-01：完整拼音搜索路径优化（目标机已部署，编辑器验收待完成）

本轮从单音节 `a → 啊` 间歇失败扩大到 `yanchi → 延迟`，按通用完整拼音搜索处理。不能将用户的“遍历算法有问题”直接当作已证明根因。空上下文的实际服务请求能返回目标词；调查期间也出现管道连接失败，后来服务恢复，但没有足够的相关证据把这段不可用归因于搜索。

- 明确的可优化机制：词法遍历在产出精确拼音结果前，先物化所有较长读音根并建堆。`src/neural_weasel/neural_candidate_pages_v3.py` 现在先保留这些根的不可变种子，在精确层耗尽后才物化，保持原有层内排序和合法尾候选。非完整拼音 `zhuyid` 的公平轮转分支不变。
- 新回归 `tests/test_candidate_exact_search_latency.py` 覆盖单 token 和 `延 + 迟` 两条精确路径：320 个较长读音根不得抢在 `延迟` 前物化；遍历到尾仍保留合法 `延迟入` 且不重复。两项均先 RED 后 GREEN。候选容量、后台重试、冻结发布、多 token、显式拼音边界等共 90 项回归通过；ruff check 和新测试格式检查通过。
- 实际 v5 索引、合成均匀 logits 的离线对比：`yanchi` 首 7 项中位搜索耗时 5.7999 → 3.4864 ms，首 35 项 8.1263 → 5.6192 ms。`a/nihao/yanchi/mingxiandui/zhuyid` 五组前 35 项候选序列相同。这不是实际按键端到端延迟；未修改的 `zhuyid` 分支测得 37.5362 → 45.4752 ms，不能宣称所有输入均加速。
- 曾尝试首屏仅补 7 项，但两项原有 35 项分页契约测试失败，已完全撤回。最终未改首屏容量、native 50 ms 或 Python page0 35 ms 预算，也未引入单音节特判。
- 仅将 v3 文件复制到当前安装，旧 SHA-256 `950633078C466ACE8CCD4FB73640E7F7D00FEEF3520AC287AB351BF45A8A2DBD`，新 SHA-256 `2EE96A5732531F33AD120EB73F570ABD1C3AC6109197C6288A1ED5AB5BAAC970`。安装 pager 的既有完整单字母修复保留。仅结束经身份检查的 Q4 子进程树，由原游戏守护进程恢复；UI 未重启，默认输入法、RimeUser、屏幕和键盘未操作。
- 使用仓库原生 NamedPipeClient，绝对 50 ms，epoch=0、唯一 synthetic session。部署前后 `a`、`yanchi` 各 20 次均首次请求成功并含目标词，无重试。部署后最大值分别 43.591 / 19.430 ms，部署前分别 16.822 / 15.135 ms；不能以这组样本宣称真实端到端加速或间歇故障根治。

持久回滚与元数据证据：`C:\Users\zhaoy\AppData\Local\NeuralWeasel\Experimental\backups\exact-search-20261001`，其中旧 v3、deployment.json、evidence/native-summary.json、原生探针及部署前后请求元数据。离线候选对比仅使用合成输入和空上下文，不含真实编辑器文字。

仍需用户手动在实际编辑器验证 `a → 啊`、`yanchi → 延迟` 的可见候选及空格/数字上屏，以及非零合法 editor-context revision 下的间歇失败。当前自动化证据没有覆盖 TSF 选择/提交，不能标记真实编辑器验收完成。如再失败，应关联候选集身份、合法 context source/session/revision、错误码和耗时；不记录真实周围文字，不借用其他焦点的 epoch，不延长超时掩盖故障。

## 2026-10-01：上下文引擎前的实验性 Release 基线

用户要求先发布包含此前全部改动的 Release，再从新分支落实 GitHub #39。FIM 原型已从本次发布范围隔离；此基线仍使用 continuation。GitHub CLI 不使用，提交/推送使用本地 git，发布由现有 CI 的标签限定 job 完成。

- 发布检查修复：scored 页管理器通过完整父类链清会话；词法游标在短锁内检查会话有效性，拒绝清理后的旧任务写回，已耗尽集合也随会话回收。公开清理与创建/耗尽两种竞态共三项回归通过。
- 游戏守护不再根据历史 PID 和进程名强杀进程；持久 PID 仍存活时拒绝启动，已死亡时可继续。两种路径的隔离 PowerShell 测试均证明不调用 taskkill。
- Python 3.12 全套 540 项测试通过；pywin32 必须存在于测试环境。锁定 ruff 0.16.0 的格式、静态检查和 git diff --check 通过。移除一段完全相同的重复测试定义，保留原验收。
- 新发布 job 等待 Python 与 Windows 原生构建/CTest、安装和启动 dry run 全部通过，验证清单 commit 后打包；先上传 ZIP、SHA256SUMS.txt、build-manifest.json，再公开实验性 prerelease。待 Actions 和已发布附件读回后才算 Release 完成。

Release 说明：`docs/releases/v0.1.0-experimental.20261001.md`。真实编辑器候选/提交/保护字段/延迟验收仍待完成；此次发布不改变当前安装，也不操作前台。

## 2026-10-02：Release 完成，独立分支实现上下文引擎 phase1

- `v0.1.0-experimental.20261001` 已公开为 prerelease，指向 `db73b703e0de71241c14e8691eb67d488adf7492`；GitHub Actions 36937692781 的 Python、Windows 和发布三项 job 均通过。Windows 9/9 CTest、资源隔离、安装/卸载及启动 dry run 通过。Release API 已读回 ZIP、SHA256SUMS.txt、build-manifest.json 三个附件；未将附件本地解包核验冒充已完成。
- 从此基线创建 `codex/context-engine-fim-phase1`。FIM 是明确 opt-in 的后台候选评分配置，默认 continuation；正文不解释特殊标记，native marker/BOS 能力验证、左右裁剪、至少 16 token 候选余量、完整 root/cache 身份与重放均实现。按键仍只读 immutable logits，安全与 session/revision 发布边界保留。
- Python 3.12 全套 569 项测试通过，ruff 0.16.0 check/format 和 diff whitespace 检查通过；只读独立审阅未发现本轮 FIM 正确性或安全阻断。真实本机 Q4 GGUF 的 vocab-only 原生探针确认 FIM IDs `248060/248062/248061`、CONTROL `8/8/8`、`add_bos=False`，没有加载权重或推理上下文。
- 本机空闲内存约 2.5 GB，GPU 自由显存不足以并载另一模型，因此不停止当前服务来获取 A/B。新增六组公开合成样例的 CPU 原生对照工具及临时 Colab highram 驱动，使用已推送 immutable source commit 和 pinned Q8；此时真实推理对照尚未执行。六样例模型联合概率比较不能代表完整候选搜索、TSF 或 GPU 延迟。

设计与重现入口：`docs/architecture/context-prompt.md`。下一步记录真实原生 A/B 结果，仍不部署、不切换前台、不改生产默认。UIA high-context 是后续阶段；真实编辑器可见候选/选择/提交/保护字段/延迟验收仍待人工完成。

首次临时 Colab 任务从 `a66be7a47717c95c07cae56fa095b33ba5f64a0d` 启动，正常退出并释放运行时，但 Colab 只转发父 Python 的 kernel stdout，子进程的排名输出未返回本地，故无法据此报告质量结果。驱动改为要求子进程写 JSON、父进程读取并输出结果，失败则明确返回子进程错误；公开模型下载显式 `token=False`，避免无意义的 Colab secret 查询。需用新 immutable commit 重跑一次来取得可读回证据。

第二次从 `e88f2326d191dfe3a272646a2d0530e850af99e0` 重跑，成功读回原生 Q8 排名后释放运行时。模型 SHA-256 `2a5266475777daf23e21991592a006f6dbc0be4620ac6a12d7d57240b0027711`，llama-cpp-python 0.3.23，CPU 两线程、n_ctx=256；六个预设合成样例 top-1 从 continuation 2/6 到 FIM 6/6。相同左侧和 raw_keys 时，右侧变化分别使 `实施 → 事实`、`compress → compile`，证明该模型能利用 suffix。候选均为单 token；这不是代表性质量或多 token 原生验收，10.847 秒整轮耗时也不是按键延迟。完整概率、来源和限制保存于 `docs/experiments/context-fim-ab-20261002.json`。

Windows CLI 的 GBK stdout 被按 UTF-8 解码，损坏了中文标签；仅对固定公开样例的标签，先验证源候选经过此精确变换后的唯一对应，再恢复标签并交叉检查排名及正确标记。数值不变，报告注明修复过程；驱动今后使用 ASCII JSON，避免重现此传输问题。当前仍默认 continuation，已运行的输入法服务未部署 FIM。后续需要代表性中文/英文约束候选集、原生多 token 评分、真实 TSF smoke 与延迟测量，再评估默认切换；UIA high-context 后置。

## 2026-10-02：隔夜持续慢/无候选的后台诊断及守护句柄修复

用户反馈今晨持续慢或不出候选，怀疑隔夜启动/恢复鲁棒性。本轮保留前台、默认输入法、现有 Q4 安装与其他应用，只做后台诊断和已复现缺陷的源码修复。未安装/重启生产服务，未启用新的正文日志，未保存真实编辑器上下文；所有候选探针使用空上下文和独立合成 session。

- 现场任务并未缺失：UI PID 13028、守护 PID 8488 来自昨日启动；模型 worker PID 34160 来自昨日 21:44 UTC 游戏后恢复。任务 Running、state running 和启动脚本父 PID 都不能证明 worker 响应健康。
- 原生 50 ms deadline 探针首轮连续 5 次运输 timeout，随后一次 `candidate_page_timeout` 在重试后恢复；另一轮首发 34/40 成功、6/40 timeout。后续三轮分别 40/40、39/40、40/40，最大耗时 28.226/62.665/17.326 ms。证明后端有间歇超时，不能把后来变快当作真人持续无候选已解决。
- 模型工作集最初约 0.9 MB，探针后约 94 MB，10:20 UTC 又降到 585728 bytes；UI 同时仅 237568 bytes。模型 private bytes 约 7.4 GB，物理 RAM 约 13.8 GiB、空闲约 1.2 GiB。反复失去驻留是冷响应风险，但没有足够的时间关联证据证明具体清理工具或其对持续无候选的因果关系。
- 当前 Mem Reduct 配置实际在 `C:\Program Files\Mem Reduct\memreduct.ini`：自动阈值 95%，定时清理关闭，最后统计 2026-10-02 09:46:17 UTC；AppData 旧 ini 的 80%/10min 不代表当前配置。Mem Reduct 和 HP SystemOptimizer 在运行，均未停止或改设置。模型再次变冷时最后清理统计没有变化，不能直接归因 Mem Reduct。
- 有界外层子进程诊断返回 ready、无 context error；health 79.035 ms、diagnostics 113.626 ms。诊断会覆盖“最后一次请求”指标，不能据此推断真人编辑器请求耗时。`NamedPipeClient(timeout_ms=...)` 的同步读取并不由该 connect timeout 限制，需要外层进程 deadline 才能保证探针退出。
- 守护源码的桌面分支只以 child 不存在或 `HasExited` 为恢复条件，进程活但超时没有恢复判据；游戏检查的 `Thread.Join()` 也没有 deadline。这些是恢复机制缺口，未以泛化重启或放宽按键 deadline 掩盖。

已复现并修复的缺陷：`scripts/start-model-service-hidden.ps1` 的 `NeuralWeaselGameDetector.Check()` 在检测线程仍绑定 desktop 时调用 `CloseDesktop`，每检查一次泄漏一个句柄。旧代码独立 60 次检查增长 60，运行守护约 2.7 万句柄，符合每两秒检查的累积量。修复让调用线程持有 desktop handle，等待检查线程退出后在 finally 关闭，覆盖各个早返回分支。未改变游戏判断、模型配置或候选/TSF 安全边界。

验证：新增 Windows 实测句柄回归，预热后检查 120 次；隔离复制的 HEAD 旧脚本出现 `handle growth=120`（另一轮 119），回归 RED。修改后独立 60 次句柄 536→536、525→525；相关 `tests/test_game_mode_guard.py` 与 `tests/test_one_click_launcher_contract.py` 共 29 项 GREEN，ruff 0.16.0 check/format 和 diff whitespace 检查通过。测试依赖在 uv 独立环境中，生产 venv 未加 pytest。修复尚未同步到已安装脚本，现有守护仍有泄漏。

最后现场变化：2026-10-02 10:25:56 UTC，原生产守护记录停止模型进程树，状态变为 `suspended-fullscreen`、pid null；这属于守护标记的全屏暂停，不能写成我们重启或模型崩溃。未接管前台或强制恢复，真实全屏判断是否符合用户场景尚未确认。

仍未闭环：持续慢/无候选的真人 TSF 路径；驻留下降的具体来源及与请求超时的时间关联；活进程异常响应的安全恢复策略；句柄修复部署与隔夜/全屏退出后的真实编辑器验收。下一步不能仅凭当前 29 项或暖机探针宣告输入法健康。

## 2026-10-02：续查自动恢复、冷请求和首次上下文丢失

本轮继续后台诊断，保留现有源码修复及其他聊天的工作区变化；未部署、重启服务、改变前台或清理工具设置。生产守护于 10:40:53.716 UTC 自行退出全屏暂停并恢复模型，约 10:41 UTC 新 worker PID 16040 上线，UI 仍为 PID 13028。新 worker 存在并不代表请求健康：一次原生空上下文探针首发 34/40 成功、6 次运输 timeout，开头四次连续约 59–63 ms timeout。

- 10:49:14 UTC 开始三分钟观测，每 0.5 秒采样数值、每 30 秒一次合成候选探针。第一组首发 40 请求中 28 次候选成功、9 次运输 timeout、3 次 `candidate_page_timeout`；包含重试共 45 请求、31 次候选成功。后五组首发共 200/200 成功，最大单组耗时依次 17.377/24.811/26.222/23.718/54.432 ms。探针预算名义为 50 ms，实测墙钟可能超过预算，不能把成功返回等同于严格小于 50 ms。
- 此段模型工作集从约 1.9 MB 暖起至约 27.7 MB，没有记录到骤降；Mem Reduct 最后清理统计始终是 10:36:22 UTC。随后三分钟停止周期探测；用户在其中做真人输入，所以该段不能当作纯空闲对照。模型工作集因实测升至约 147 MB，段末约 126 MB，没有骤降，最后一组仍为 39/40 成功、1 次 62.549 ms timeout。这说明极低驻留并非所有间歇超时的必要条件；没有证据锁定某个内存清理工具。
- 用户随后明确反馈现在能正常出候选。对应 TSF 元数据记录了实际 `candidates=7 composing=1`，桥接层后续发布 epoch 2/3/4/8，支持当前编辑器候选及上下文链路暂时恢复；不是隔夜稳定性、数字选词或保护字段的完整验收。没有读取或持久化候选正文、编辑器正文或窗口标题。

新确认的恢复缺口：真人实测首条上下文更新 sequence 544 报 `event=process result=update-transport-error sequence=544 status=3 error=232`（`ERROR_NO_DATA`，管道正在关闭），后续更新才成功。`native/pipe/named_pipe_client.cc` 复用已有 handle，遇到传输错误才断开；`native/context/context_update_bridge.cc` 在初次更新的传输错误后直接返回，丢弃本次更新，不会在新连接上主动恢复同一最新 snapshot。日志与模型曾重启的情形一致，但单凭错误码不能证明现场错误确由旧 handle 引起。

隔离原生复现使用当前实际 bridge/client/epoch 源码和一次返回 `kDisconnected/ERROR_NO_DATA` 的合成 transport，不连接生产、不保存真实上下文，测试的 LOCALAPPDATA 指向临时目录。输出 `single_update_abandoned=1 calls=1 epoch=0`：等待 300 ms 仍没有第二次调用；提交下一条更新后 `next_update_published=1 calls=3 epoch=1`，退出 0。证明这个恢复缺口本身，不能证明它独自造成用户整段持续无候选。探针及数值证据在 `%TEMP%/nw-debug-20261002/` 的 `bridge-drop-once.cc`、`observations-104914.jsonl`、`idle-105510.jsonl`。

后续最小修复需先验证重启后的首条最新更新能够在有界后台策略下恢复，并覆盖 superseded/secure cleanup/ack 丢失，防止重复发送或恢复旧上下文；不扩大 TSF 同步等待。当前尚未修改桥接层或生产安装，上一轮句柄修复仍仅在源码。持续慢的完整因果链及隔夜真实编辑器验收仍未闭环。

## 2026-10-02：分阶段超时定位与 Windows 计时精度

用户要求继续 debug。本轮仍只做隔离探针和后台元数据采样，未部署新代码、重启生产、修改内存清理配置或操作前台。复核已安装 Python 3.12.11：候选 pager 默认 `time.monotonic`，生产请求的 `deadline_started` 也用该时钟，因此不是只存在于未安装源码中的问题。

- 原生探针仍用公共合成 `a` / `yanchi`、唯一 session、epoch=0、50 ms 预算，不读取真人上下文。在临时副本为连接、四段读写、等待和取消完成分别计时，不修改仓库客户端。第一轮六组共 240 个首发请求，首组 35/40 成功、其余出现一条 `candidate_page_timeout`；另一组六组首组 28/40 成功（11 运输 timeout、1 `candidate_page_timeout`），后五组 200/200 成功，其中两组用未加计时的原探针。两次首组表现更差是观测结果，不能凭此认定冷模型或某个优化器为原因。
- 第二轮 11 个运输 timeout 中，8 个主要卡在读响应头，3 个卡在重新连接；这收窄了失败阶段，但尚未拆出服务内部调度、锁等待和计算各自耗时。响应等待的实测可达 62.69 ms，而失败后的取消完成仅 0.0099–0.0233 ms；当前样本不支持“取消 I/O 的无限等待导致这批卡顿”。
- 确认计时精度不足：该 Python 的 `time.monotonic` 是 `GetTickCount64()`，分辨率 15.625 ms；`time.perf_counter` 是 `QueryPerformanceCounter()`，分辨率 0.0001 ms。隔离构建同一已安装 v3 pager，用空合成依赖，只调用其实际截止检查，并错开起始相位，各 24 次。12 ms 预算用 monotonic 实测 0.301–15.571 ms（可过早结束）；35 ms 实测 32.189–46.993 ms。注入 perf_counter 后分别为 12.004–12.015 ms 和 35.009–35.023 ms。证明短预算精度缺陷及高精度时钟可纠正该孤立检查，不证明其独自造成全部真人卡顿；实际计算还可能在两次截止检查之间超时。
- Windows 等待也不能当作严格墙钟上限：独立未置位事件的 `WaitForSingleObject(...,49)`，16 次实测 61.429–64.308 ms、中位 62.185 ms，和原生超时耗时相近。它说明约 62 ms 的客户端返回本身不证明模型计算了 62 ms，亦不保证改 Python 时钟后所有请求严格低于 50 ms。本轮没有改变系统计时器分辨率。

临时数值证据：`C:\Users\zhaoy\AppData\Local\Temp\nw-debug-20261002\stage-observations.jsonl` 保存第二组六组及分阶段数值；`clock-budget-comparison.json` 保存隔离 pager 的四组采样；`kernel-wait-comparison.json` 保存事件等待采样。第一组六组原始文件被第二组同名输出覆盖，其关键结果仍保留在本聊天及本节，不能声称临时文件包含第一组。临时 C++/脚本只含公开合成请求及数值，没有记录真实编辑器文字。

修复方向已具体化但尚未实施：生产请求起始时刻与 pager 默认时钟需要成对改为同一高精度时钟，防止混用不同时间基准；不能仅改一端、延长 50 ms 客户端预算或改变全局计时器设置来掩盖问题。此前首次上下文更新遇到 error=232 后丢弃的恢复缺口仍成立，需另外覆盖后台恢复、superseded、secure cleanup、ack 丢失。句柄修复仍仅在源码；完整因果链、生产修复及隔夜真人验收仍未闭环。

## 2026-10-02：同步清理、驻留恢复与分页读取差分

用户要求继续找根因，并要求先用 Sol 高推理、必要时才考虑 Astra。使用一次 GPT-6.1 Sol xhigh 有界只读复核；未调用 Astra。以下仅诊断和隔离实验，不部署、不重启生产、不改变前台或优化器配置。保留其他聊天正在修改的桥接、管道及上下文测试文件。

- 已安装 Python 的启动路径确实调用 `gc.collect(); gc.freeze()`，不是仓库有而生产漏装。隔离使用真实公开 v5 拼音索引（53186 行）、synthetic logits 和 FakeRuntime，不加载第二个模型；未 freeze 时出现约 461 ms 的 gen2 GC，并使独立真实管道 120 次首发中 3 次运输超时；按生产策略 freeze 后 120/120 成功、最大 handler 18.754 ms。这只验证策略及实验机制，不能把未冻结实例的长 GC 归为现场根因。
- 生产 PID 16040 的 20 秒只读采样：工作集 589824→27131904 bytes，缺页计数增加 10543，40 首发中 37 成功。首条运输超时 64.663 ms，约 778 缺页；随后超时/重试约 700–1406 缺页；恢复后的成功请求约 5–16 ms、20–54 缺页。PageFaultCount 包含软、硬缺页，不能把这些数字都叫磁盘读取。
- 同期 py-spy 非阻塞、只采函数栈，无 locals/正文。叶采样权重包括 installed v3:1726 的游标 pop 约 110 ms、installed neural_candidates:1053 约 75 ms。纠正行号归因：1053 实际是 `for candidate_set_id, session in tuple(self._sessions.items()):`，真正 `del` 在 1055；这不是逐请求删除耗时。过滤空闲后的 speedscope 权重也不是完整墙钟时间线，不能据此对齐某条 timeout。
- Sol 复核及主代理读回确认：词法快照的 `continuation_root=None`，epoch=0 原会话的 baseline root 被管理器永久保留；实际安装的 llama runtime 生产路径使用 replay token IDs 和空 state_bytes，不复制巨大模型 C 状态。因此只能把临时 Python 路径/集合的同步释放、动态 GC 视为待验证机制，不能声称已经证明大模型 C 状态析构。
- 新反证：在一次性独立进程中保留 startup freeze，并统一用 perf_counter；每 100 ms 公共 synthetic 查询，于第 20/40/60 条前只对自身调用 EmptyWorkingSet。工作集约 237 MB→0.75 MB，首请求约 2210/2320/2373 缺页，仍分别仅 5.486/5.557/5.766 ms；80/80 成功，最大 26.479 ms。再同时把隔离 pager 时钟推进 30 秒，触发 15 秒旧会话过期，另 80/80 成功、最大 24.467 ms。没有清空生产工作集或调整系统时钟。只 trim、甚至 trim 加过期，均未充分复现现场；不能把低驻留单独定为完整根因。
- 又一次生产只读采样加入 PDH 系统分页原始累计数，每约 10 ms 取样，未加 py-spy。工作集 1720320→44789760 bytes，缺页增量 10554，首发 37/40 成功。三次运输 timeout（序号 1/2/36）为 54.565/63.534/63.682 ms；对应进程缺页 1174/1069/1516，同时全系统分页读取次数增量 365/196/312、读入页数 1244/528/2168。序号 5/6 成功约 13 ms 时分页读取仅 2/4 次；末尾 38–40 成功 14.371/14.710/7.873 ms，进程缺页 1/0/0，分页读取均 0。第 36 条在已经暖过后再次触到冷页，因此不是单纯首请求初始化。PDH 是系统级且窗口为近邻样本，仍不能把全部读取归给 PID 16040；它为冷页回读提供比总缺页数更强的相关证据。
- Mem Reduct 仍为 95% 自动阈值、定时关闭，最后统计 11:13:43 UTC；HP SystemOptimizer 也仍运行。二者二进制具有工作集/内存清理接口，只证明能力，没证明本次执行者。当前诊断进程非管理员，未开启按进程区分硬缺页/调度的 ETW；没有升权、弹 UAC 或启动全系统追踪。

数值/函数栈证据均在 `C:\Users\zhaoy\AppData\Local\Temp\nw-debug-20261002\`：`live-1790941659-summary.json` 与 `.speedscope.json`，`candidate-trim-stages-perf_counter-timeout-gc-freeze-transport-False.json`，`candidate-expire-trim-stages-perf_counter-timeout-gc-freeze-transport-False.json`，`live-system-1790942492-counters.json`、`live-system-1790942492-summary.json` 及对应 client/stages jsonl。只包含公开 synthetic 请求及数值/函数位置，没有真实编辑器上下文、候选正文或内存转储。

当前最强方向是冷工作页回读拖慢同步候选路径；同步旧游标释放/动态 GC 的贡献及工作页回收者未闭环。安全的下一差分是在隔离实例中比较正常移除与暂存公开 synthetic 旧对象、动态 GC 开关；检查请求尖峰是否转移到响应后的释放阶段。暂存策略不得直接用于 PRIVATE 或 secure cleanup，也不能先修改生产以“验证”根因。逐进程硬缺页/调度证据仍缺，真人隔夜稳定性、完整生产修复仍未完成。

## 2026-10-02：保留内存回收，验证公共热页保护与进程硬缺页

用户明确 Mem Reduct 回收是预期行为，继续 debug 并考虑白名单。本节未停用、改配置或触发 Mem Reduct，未调整生产工作集、重启输入法或移动前台。独立实验只处理自身公共 synthetic 数据。生产仍为原 PID 16040/Q4_K_M；保留其他聊天改动。

- 核对 Mem Reduct v3.5.2 的公开 `src/main.c`/`src/main.h`：工作集清理使用 `NtSetSystemInformation(SystemMemoryListInformation, MemoryEmptyWorkingSets)`，是系统级操作，未找到进程白名单入口，不能虚构一个 ini 设置。当前 `ReductMask2=231=0xe7` 包含工作集、系统文件缓存、priority-0 standby、combine、registry、modified file cache，不包含全 standby purge(0x08)或 modified list(0x10)。来源：https://github.com/henrypp/memreduct/blob/v.3.5.2/src/main.c 。95% 自动阈值及关闭定时保持不变。
- 独立 Windows 进程分配 8 MiB 公共区：普通工作集最小值与 HARDWS_MIN_ENABLE 两组在自身 EmptyWorkingSet 后均无页面驻留；VirtualLock 组的前 2 MiB 全部 512 页保留，其余 6 MiB 全被移出。这里只证明自身 trim 行为，不代替系统级 Mem Reduct 验收，也不把 hard-min 当作热页白名单。
- 再用真实公开拼音库 53186 行及合成空上下文 scores 构建专用公共区：公开索引 JSON 3298990 bytes、52 组单字预热 74919 bytes、synthetic baseline float32 1048576 bytes，页对齐合计 4423680 bytes（4.21875 MiB/1080 页）。申请本进程 16 MiB 最小工作集作为锁页额度；VirtualLock 成功，QueryWorkingSetEx 在自身 trim 前后均 Valid 且 Locked 的页面为 1080/1080；解锁再 trim 为 0/1080。仅在 Valid=1 时解释 Locked 位；无效页对应 union 位不能读成锁定。没有锁 Python 通用堆、模型状态或 PRIVATE 数据；这些公共字节尚未接入候选检索，不能把驻留结果说成已经改善实际输入延迟。
- 完成正常移除/暂存旧公共对象至响应后释放 × 动态 GC 开/关四组，每组保留 startup freeze、统一 perf_counter、真实公开索引与 FakeRuntime；每组 80 查询，三次仅自身 trim 并推进隔离时钟使旧 4 session/4 cursor 过期。共 320/320 成功，四组最大 handler 为 19.765/27.610/22.662/29.127 ms。暂存组响应后的释放为约 3.877–6.337 ms，请求内 GC 最长 2.519/6.432 ms，关闭动态 GC 两组请求内 GC 为 0。330–615 ms 的 GC 极值属于 startup、request=None，不能归于请求。没有复现数十毫秒尖峰，不据此改生产对象清理路径。安装旧 v3 无仓库新加的 `_lexical_state_lock`，实验只用已有锁或独立实例 nullcontext，不伪造生产已部署新锁。
- 找到无需升权的逐进程硬缺页读数：`NtQuerySystemInformation(SystemProcessInformation=5)` 中 `HardFaultCount`。本机 64 位 Windows 10.0.26300.0 的字段布局与 phnt 一致：hard count offset16、CreateTime32、PID80、总 page fault128、WS144；每次用 GetProcessTimes 核对进程创建身份。phnt 是公开 NT 结构资料，不能称微软稳定公开 API。HardFaultCount 是进程累计硬缺页数，不是磁盘 I/O 次数、字节数、等待时间，也不专指 pagefile。来源：https://github.com/winsiderss/phnt/blob/master/ntexapi.h 。
- 生产只读 12 秒/944 样本：WS589824→25260032 bytes，目标硬缺页 +2346、总缺页 +6275，40 首发 36 成功。seq1 运输 timeout56.941 ms/目标硬缺页237，seq2 timeout62.081/252；seq3 deadline46.655/307，重试51.619/220及62.142/241后22.018/77成功；seq4 deadline49.516/227后重试1.610/6成功；seq5/6正常13.260/18.936ms，对应9/7次硬缺页。请求前后静默窗口硬缺页增量均0。seq1主要等响应 header55.631ms；seq2主要连接恢复61.793ms，不能把所有失败都叫搜索耗时。统计含后台线程、近邻窗口，且查询中位7.471ms/实际采样间隔12.858ms/max13.408ms，须保留观察者影响限制。
- 对照进一步取消所有请求期间的计数采样，只在原生40请求前后各读一次：自然低WS659456 bytes起，35/40首发成功，seq1/25/26运输timeout63.358/64.913/61.896ms，seq2/3 deadline52.944/41.499ms；整批目标硬缺页+2004、总缺页+11927，结束WS24850432 bytes。因此高频采样不是故障唯一来源；对照只给整批计数，不能给每条硬缺页分摊。

本轮证据确认服务自身的真实硬缺页集中在冷工作集候选/重试期间并伴随超时；没有把 Mem Reduct 的预期回收当成错误，也未认定具体回收者或唯一根因。4.22 MiB 公共热区保护已有独立证明，但当前查询还走旧 Python 对象图，单纯锁住序列化副本不足以改善它。下一步必须在独立实例接入实际公共检索/单字首屏读取，再作锁定/未锁定真实管道 A/B，检查候选顺序、首次延迟、锁页失败降级与总驻留预算；不可先锁通用 Python 堆、保留 PRIVATE 对象或直接改生产。自身 trim 通过后，还需自然 Mem Reduct 清理及真人输入验收，不能宣布已修好。

临时证据均位于 `C:\Users\zhaoy\AppData\Local\Temp\nw-debug-20261002\`：`working-set-whitelist-probe.py/.json`、`public-hot-arena-proof.py/.json`、`benchmark-retire-difference.py` 与四个 `candidate-retire-*-gc-*.json`、`profile-live-hardfaults.py` 与 `live-hardfault-1790947703-{counters,summary}.json`/client/stages jsonl、`profile-unobserved-hardfaults.py` 与 `live-hardfault-unobserved-1790948048-summary.json`/client/stages jsonl。文件只保存数值、公共 synthetic 请求及代码，没有真实编辑器上下文、候选正文或内存转储。初次实验的不存在 `_lexical_state_lock` fixture 错误已纠正后重跑，四组结果完整；中断后会话句柄不可再轮询，但输出文件已读回确认。
## 2026-10-02 公共热区接入真实候选读取的三组对照

继续保留 Mem Reduct 的预期回收；本轮没有改清理设置、生产代码或进程，也没有操作前台。独立实例使用已安装服务代码、公开拼音索引、固定 seed42 合成 logits、epoch=0 和 startup freeze；FakeRuntime 不加载第二个模型，也不接收真实编辑器上下文。

- 新的专用公共 arena 为 1126400 bytes（约 1.074 MiB，275 页），仅包含公共单字预热结果和合成空上下文 float32 baseline scores。单字缓存从 arena 的 JSON 字节重建 Candidate；baseline scores 改为直接指向 arena 的只读 ndarray view，并验证地址及数值一致。每批实测缓存读取20次、空上下文 `_score_root` 调用22次。这里没有接入完整53186行词法索引，旧索引/匹配器 Python 对象图仍可回收，也没有锁 PRIVATE、模型状态或通用 Python 堆。
- 真实 ProductionNamedPipeServer 配合独立 native 50ms 客户端，40条公共 a/yanchi 请求；管道 `\\.\pipe\NeuralWeasel-debug-hot-arena-20261002`，服务端限定只接受这两个 raw_keys、epoch0 的候选请求。三组均先仅对自身 EmptyWorkingSet，使用相同16MiB min/64MiB max额度：原路径、arena未锁、arena锁定，各40/40成功。
- 自身回收后 QueryWorkingSetEx 的有效/锁定页分别为0/0、0/0、275/275。三组首次 native 请求5.200/3.497/5.317ms，最大16.172/19.109/20.218ms。没有证明锁定组更快；不能把一次A/B波动当作收益或退化。
- 候选内容、顺序和评分等字段的摘要三组40条全部一致。初次直接对完整候选字典摘要的检查失败，原因是生产协议插入了各会话不同的 candidate_id；已明确只排除该会话标识后重跑三组，并与原路径对照，未删除候选内容或排名字段。摘要和计数持久化，不保存候选正文。
- 新加批前/批后两次自进程 HardFaultCount 读数，三组增量均0；总 PageFaultCount 增量7323/7256/7263仍包含soft faults和对象分配。此前两组总缺页相差292不能归为稳定保护收益。重要结论是独立自身trim没有重现现场硬缺页条件，因此120/120成功只证明窄公共热区实际读取、保住指定页及候选等价，不证明解决隔夜故障。需以自然 Mem Reduct 清理后的真实硬缺页场景补足闭环，不能先部署锁页或扩大到整堆。

最终证据：`C:\Users\zhaoy\AppData\Local\Temp\nw-debug-20261002\benchmark-hot-arena-pipe.py`、`timed-native-hot-arena.cc/.exe`、`build-timed-hot-arena.cmd`、`hot-arena-pipe-original-off.json`、`hot-arena-pipe-packed-off.json`、`hot-arena-pipe-packed-on.json`、`hot-arena-comparison.json`。早先仅锁/未锁的 `hot-arena-pipe-off.json`、`hot-arena-pipe-on.json` 是前一版，不含原路径和自进程硬缺页对照，不混用统计。所有临时对象为公开或synthetic；进程退出回收arena，避免异步线程仍持有ndarray时显式VirtualFree。生产最终读回worker16040仍为10:41:09UTC启动、WS704512bytes，UI13028/guard8488仍为昨日启动；句柄修复尚未部署，守护32990句柄。

## 2026-10-02 独立审查另一聊天的上下文恢复改动

用户担心另一聊天的上下文引擎修复破坏输入法。本轮只审查与隔离验证，没有部署、重启生产、操作前台或修改另一聊天的运行时代码；保留原有全部工作树改动。

- 独立干净 MSVC/Ninja 构建全部 native 测试，CTest 10/10 通过（0.62秒）。完整 Python 回归 587 passed in 23.76s；NEURAL_WEASEL_RECOVERY_PROBE 指向本次新构建的 executable，12项真实 Win32 管道故障/清理测试实际执行，非跳过。证据在 C:\Users\zhaoy\AppData\Local\Temp\nw-context-review-20261002\native-build.log 与 C:\Users\zhaoy\AppData\Local\Temp\nw-context-review-20261002\python-tests.log。
- Python receipt 仅保存最新更新的身份元数据、不保存正文或正文hash；全部身份匹配后才允许恢复ACK。reset/secure清除receipt；native保留最新sequence、source revision，以及ACK与health的服务PID+创建时间一致性校验。恢复等待位于原有后台worker，没有将模型/IPC等待搬入TSF同步路径。Sol xhigh独立原生审查没有发现可报告的新增回归。
- receipt阴性后重发与Invalidate/secure并发仍有检查后竞争；首次发送原本就有同类在途窗口，worker串行执行cleanup屏障、发布前再次拒绝旧sequence，因此目前不能称为新增跨焦点发布漏洞。现有门控测试未专门卡住阴性receipt后的重发位置，是更细测试缺口。CancelIoEx后INFINITE等待也是HEAD既有严格deadline限制，不归咎此次修复。
- FIM属于本分支先前的显式选项；CLI和ContextPromptConfig仍默认continuation，未暗切FIM或量化模型。候选/Rime/TSF主要查询源没有此次恢复补丁改动。
- 混版本不会绕过发布校验，但新桥接的receipt恢复遇到旧Python服务会因unknown_message_type失败关闭；桥接组件与Python服务必须配套部署，才能获得恢复能力。
- 测试前后四份源码hash一致：context_update_bridge.cc 3D0F9B932392871A0E86A80409E839D79282D622AF586FB8E16DA8F7A48D9942；named_pipe_client.cc 5677ECE5C5FCDC924EC3C1BF8D443FB40220344E79D7FE3C780158F4A4F6A89A；named_pipe_client.h E002DF76333CE523CAC6776D394592C6F8A817DA144378969092344A05891299；pipe_server.py 19592067CEB3C68A0C44F06A1D5FE09FDE844A51CA66653641A2CCB4407BE157。安装pipe_server.py仍为63394539E2496EE0B1051C0C7235B86474CB57A0673704F4B7E7AC9CA11A3E2D，worker16040仍10:41:09UTC启动，UI13028/guard8488仍昨日启动，证明此次源码修复未进入现有生产进程。

结论：当前查到的源码和独立测试没有明确输入法回归；尚不能宣布新版本通过真实TSF显示、数字选择、提交、保护字段、延迟与隔夜自然Mem Reduct回收验收。之前硬缺页/白名单实验与守护句柄修复仍是独立未完成事项，不能被此轮上下文恢复绿灯替代。

## 2026-10-02 用户澄清：审查合并后的回归风险

用户澄清担心的是另一个聊天尚未合并的上下文引擎改动进入main后破坏输入法，而非当前安装是否已被替换。此前587项回归包含本工作树未提交receipt恢复代码，不能直接当作已提交FIM分支的精确证据。

- GitHub connector当前确认main为db73b703e0de71241c14e8691eb67d488adf7492，codex/context-engine-fim-phase1为ff8cfcf6d614a61ad240a2932f5069f0f2881857；ahead3/behind0，merge base就是main，三个提交为a66be7a/e88f232/ff8cfcf。待合并15文件，native/TSF/Rime及候选页实现均无此次FIM提交改动，无分叉合并冲突。
- 从git archive HEAD创建独立干净快照 C:\Users\zhaoy\AppData\Local\Temp\nw-context-review-20261002\merge-ff8cfcf，不带工作树dirty或untracked；完整Python回归569 passed in 18.01s，ruff 0.16.0 check通过、format --check通过（168 files already formatted）。完整测试日志 C:\Users\zhaoy\AppData\Local\Temp\nw-context-review-20261002\merge-tests.log。
- 同一ff8cfcf提交的远端CI run36942375620 success：https://github.com/Yi-frank-phy/neural-weasel/actions/runs/36942375620。Python lint/format/tests、原生构建/CTest、experimental TSF/server/static neural module、hashed bundle、launcher dry-run通过。发布release任务skipped，没有发生发布。
- Sol xhigh独立审查支持已提交FIM分支单独合并：CLI五入口/运行时均默认continuation，该模式不探测FIM能力、不增加模型调用；create_snapshot仅显式fim才换左右文编码，默认原_tokenize_context(before)和后续候选/root路径保留。新增构造参数为可选关键字，候选请求协议未变，新增诊断字段有白名单。
- 结论：当前已提交FIM分支未见明确输入法回归，支持以实验开关仍默认关闭的形式合并。此判断不涵盖尚未提交的receipt恢复代码或未来改动，也不证明显式FIM的真实编辑器、延迟和隔夜回收验收已经通过。本聊天只审查，没有执行合并、切换分支、部署或生产重启。

## 2026-10-02 已合并FIM，并按聊天定位剩余上下文改动

用户明确授权合并已审查FIM，同时要求审核另一份改变上下文获取方式的改动。GitHub connector创建并合并PR #42：https://github.com/Yi-frank-phy/neural-weasel/pull/42，expected head固定ff8cfcf6d614a61ad240a2932f5069f0f2881857，merge commit为d4fd82431cb4c79113a1aa4cb90eb6cf2189de8b。远程main/PR readback确认已合并；main tree fd1bde3b5dda49f26a404e1a794847395df736e9与本地已测HEAD tree完全一致，未混入dirty代码。没有切换当前工作树、暂存别人改动、部署或重启生产。

- 用户给出聊天标题后，在本机只读聊天元数据与该聊天的最后交付记录定位到“当时github中的上下文引擎的设计你可以继续落实”，chat id 01a0f992-54ac-7bd2-a4a9-42817db8fcee。没有可用的native list/read_thread工具，使用state_5.sqlite的readonly连接查title/cwd/rollout_path，仅取相关交付文字；未读取编辑器/窗口正文，也未写聊天数据库。
- 该聊天剩余未提交补丁是context_update_bridge、named_pipe_client及Python pipe_server的断线恢复/receipt与服务身份校验，实际TSF编辑器取文方式未改。git diff HEAD -- native/tsf、context_capture_broker与prepare-weasel-overlay没有差异；它不是新增UIA/文件桥接。该聊天最后交付亦明确12项真实Windows管道故障测试、587项回归、10项原生测试，修复未部署。
- 本轮重新核对四份核心源码SHA，仍与本聊天独立审查及实际587/10测试的版本一致（见上节全部hash）。因此复用已经完成的同版本实测，不无理由重跑。main合并tree与测试时HEAD相同，也没有新的integration差异。receipt不存正文，secure/reset清除receipt，latest sequence与PID+creation_time一致性阻止旧epoch越焦点发布，等待仍在server worker，TSF不增加同步后端等待。
- 审核结论：该未提交恢复补丁未见明确新增输入法或取文安全回归；可作为独立恢复修复提交进一步交付。桥接与Python必须配套部署，真实编辑器体验和隔夜回收仍未验收；遗留取消INFINITE等待与更细receipt阴性重发门控测试缺口仍按前节保留。用户本次只授权它的审核，未合并或部署这组补丁。
- 本轮末只读进程校验发现旧worker16040已经不存在；当前launcher shim15600/实际Python25280于2026-10-02T15:52:45Z启动，仍Q4_K_M。没有由本聊天执行重启。安装pipe_server.py的SHA仍63394539E2496EE0B1051C0C7235B86474CB57A0673704F4B7E7AC9CA11A3E2D，说明当前安装仍未包含本次receipt补丁；未推测这次自然/其它来源重启的原因。

## 2026-10-02 成套部署与真人候选/提交确认

用户授权将已审核改动成套部署并测试，把直接 Windows 取文放入 TODO。现已更新安装内 11 个文件：FIM 实现、server worker 的断线/receipt 恢复及服务身份校验、Python 配套协议、游戏守护桌面句柄修复，以及仓库与安装候选代码的已测同步差异。默认仍为 continuation/Q4_K_M；Mem Reduct 设置保持原样，公共 arena/VirtualLock 实验未部署，TSF DLL 未更换，直接 Windows/UIA 取文仍为未来任务。仅停止身份确认后的输入法计划任务与自有进程树；未操作前台或终止其他应用。

- 构建采用 pinned Weasel 9cc96e20dc71b80876b12f689bb5863c76c2a7ed、MSVC x64、Boost 1.78.0，保留原安装 rime.dll。新原生 server 编译成功；同版本核心原生源码此前独立 CTest 10/10 通过。Python 组合源码保留安装独有的 neural_candidate_pages.py 单字延迟优化，避免部署时覆盖昨日优化。
- 组合回归第一次 586 通过/1 失败：test_real_service_process_restart[lost_ack-False] 的 native probe 退出 21，single_update_published=0 epoch=0。当时并行编译原生服务，但未证明负载是失败原因。随后故障矩阵 9/9、完整回归 587/587（24.72 秒），该用例额外单独重复 5/5 通过。未修改测试预算或代码掩盖失败；偶发失败仍需调查。
- 生产原生公共 a/yanchi 探针首发 39/40 成功，39 条成功均含预期目标词；一次运输 timeout 59.819 ms。健康读回 ready=true、context_epoch=requested_context_epoch=160、context_updating=false；诊断 context_mode=continuation、n_ctx=24576、n_batch=512、max_after_tokens=4096、continuation_reserve_tokens=16。candidate_page_timeout_count=13 是累计数，不可逐请求归因。新 receipt 对不存在的 synthetic 请求返回 ok=true/accepted=false/context_epoch=0；没有用 synthetic context_update 覆盖真实编辑器上下文。
- 新 UI server 的纯元数据日志已有上下文发布；用户在原编辑器确认候选和数字键提交正常。该确认支持真实候选显示及提交，不能替代完整保护字段、切焦点、卸载、自然 Mem Reduct 清理与隔夜验收。
- 安装守护 C# 隔离执行 120 次检查，GC 后句柄 542→542；实时守护的句柄仍会变化，隔离结果不足以证明整夜句柄完全稳定。
- 11 个变更文件安装 SHA 全部匹配 deployment.json。build-manifest.json 明确记录组合来源、dirty 恢复代码、保留单字优化、未替换 TSF DLL，保留旧整体构建 commit，不冒称全包来自一个新 commit。首次完整性检查仅报 neural_candidate_pages.py 的旧清单 hash 不符；安装文件与已通过 587 项测试的 stage 完全一致（SHA256 68b80167a123a6e170e31ed548cd7f23affedb5fdb0c783b72108d045d7ab9cf），原清单值为 2a7c5b46aa75b82d7ceb873f84bedcf4b60875ac3cc6712d7ddbd989cd542fdf。更新该保留文件的清单并记录旧值后，verify-windows-bundle.py 完整复查通过。
- 备份根 C:\Users\zhaoy\AppData\Local\NeuralWeasel\Experimental\backups\context-deploy-20261002-163815；deployment.json 含新旧 SHA 与文件存在性，原 build-manifest.json 单独备份。rollback-context.ps1 默认仅只读预检，已实机通过；添加 -Apply 才停止确切输入法任务/进程并恢复 11 个文件及原清单。它检查目标/备份边界、备份 hash、当前部署 hash、任务动作和 PID 创建身份，遇到后续部署拒绝覆盖。未执行实际回滚；恢复中途失败会保留任务停止，避免自动启动混合版本；回滚后的服务就绪与真人输入须另验。原清单本身已有单字优化 hash 过时，回滚保持原备份，不将原清单当作新验收证据。
- 直接 Windows 取文 TODO 已写入 docs/architecture/context-prompt.md 的 Deferred Windows context acquisition，保留保护字段零采集、PRIVATE 不持久化、非阻塞和旧焦点拒绝约束。

证据目录 C:\Users\zhaoy\AppData\Local\Temp\nw-deploy-context-20261002：deploy-context.ps1/deploy.log、python-stage-tests.log、python-stage-repeat.log、restart-retest.log、lost-ack-repeat.log、server-build.log、live-probe-build.log、live-candidate-postdeploy.jsonl、candidate-postdeploy-summary.json、live-health-receipt.json、installed-bundle-check.log。没有持久化真实编辑器上下文或候选正文。部署后曾确认 UI2332、守护16324、launcher35668/worker31196；PID 会变化，后续操作必须重新验证。

仍待完成：隔夜与自然回收后的真实输入验收；生产冷工作集硬缺页/运输超时的完整因果闭环及有实测收益的公共热页保护；偶发 lost_ack 测试失败解释。此次部署不能宣称隔夜卡顿已根治。

### 部署后运行记录与超时分阶段复核（2026-10-02）

用户要求读取实际运行记录解释失败探针。本轮只读 context-pipeline.log、ui-lifecycle.log、ai-translator.log，提取白名单身份、事件、数字计时和错误码；未保存编辑器或候选正文。当前 UI server PID2332 的上下文记录共有1550条，其中published110条，无非零error记录；原部署探针周围数秒存在真实编辑器candidates=7的UI记录，但不能将它们当作synthetic候选请求的响应。日志tick与探针QPC使用当前偏移做粗粒度关联，历史漂移未测，不能据此进行毫秒级因果排序。ai-translator.log最后更新于2026-09-09T08:16:25Z；候选诊断开关未启用，Python pipe_server的读请求/处理/写响应没有逐请求计时日志。因此现有运行日志无法追溯那条59.819ms超时具体停在哪一阶段，未记录错误不代表该候选请求成功。

- 用当前仓库named_pipe_client.cc保留服务身份检查，仅在临时副本添加数字阶段计时，编译current-stage-probe.exe；未替换生产文件、重启服务或操作前台。公共a/yanchi、synthetic session、epoch0、50ms预算，40次首发37次成功且含目标词；1次运输timeout、2次服务端candidate_page_timeout，后两次重试成功，共42次请求。不能把41条运输ok报告成41次候选成功。
- 首条请求总耗时63.98ms，connect_ms=0.0884、write_header_ms=0.0047、write_payload_ms=0.0022、read_header_ms=63.7054、wait_ms=63.6886、cancel_ms=0.0107，win32_error=121。这次失败位于发出请求后等待响应头；连接和取消都很快。该证据尚不能区分服务端线程调度、缺页、候选锁等待及搜索计算，也不能将全部等待算作模型推理时间。
- 两条服务端candidate_page_timeout分别为首发sequence2/48.396ms、sequence6/34.318ms，说明本轮还有明确的候选处理预算失败。新探针周围上下文及UI日志无新增事件；synthetic候选管道请求本来不会产生真人UI发布记录。服务端仍缺少同一请求的进入/候选处理/写回耗时关联，这是下一步诊断缺口。
- 安全证据位于C:\Users\zhaoy\AppData\Local\Temp\nw-deploy-context-20261002：runtime-log-correlation.json、current-stage-requests.jsonl、current-stage-timings.jsonl、current-stage-outcome.json、current-stage-log-correlation.json；临时源码current-timed-pipe-client.cc及准备/采样脚本同目录。Mem Reduct设置保持不变；隔夜与自然回收后的鲁棒性仍未证明。

### 2026-10-02 按词对照及首屏词法搜索定位

- 用户说明有的词流畅、有的词卡，并要求直接读报错，不记得具体词。已读取的记录支持 candidate_page_timeout 和运输错误 121，但安全日志没有近期原始输入，不能恢复或编造用户刚才的词。后续使用公开合成拼音矩阵，不保存真实编辑器正文或响应候选文本，生产仍为同一 worker PID 31196、创建时间 2026-10-02T16:38:32.277668Z；前台、Mem Reduct、生产源码和任务未改动。
- 12 个公开拼音、每个三次、epoch0、原生 50ms 预算，未采栈的一组首发 20/36 成功，整组硬缺页 +6083，工作集 2199552→203784192 bytes。随后带 py-spy --nonblocking、不带 locals 的一组首发 30/36 成功，硬缺页 +35；这是不同状态下的复现，不是采栈工具收益对照。zhuyid 两组均 0/3 首发成功；gongzuo 从前组 3/3 变为后组 0/3，不能把词永久分类为快/慢。
- 现场 py-spy 收到 132 条栈样本、6 次采栈错误；81 条含 _lexical_completion_fallback，其中可见首屏 _freeze_next_page 与后台 _run_page_preparation 两条调用链。样本数量不是耗时比例，且 speedscope 的时间轴不足以逐请求关联，不能将某条栈归给用户刚才的输入。证据指向词法补全/路径展开，尚未完整分离现场锁等待与线程调度。
- 使用安装代码、公开 53186 行拼音索引及随机 synthetic scores 的隔离测试，不加载第二模型。高精度 perf_counter、无模型 continuation 能力、循环 GC 关闭时，36 请求仍有 6 次 CandidatePageTimeout；一次 zhuyid handler 52.5217ms、主锁持有 52.3483ms、主锁等待 0、_freeze_next_page 40.6969ms、_lexical_completion_fallback 40.4165ms，单次 next(cursor) 32.8325ms。隔离环境仍保留词法后台线程，不能据此排除调度竞争，但模型调用与循环 GC 并非这条慢路径的必要条件。首次 TimeoutRuntime fixture 会在线程中抛 synthetic continuation unavailable，作为受限实验记录，未用其证明真实模型线程故障；随后上述结果来自不抛异常的 Runtime。
- 当前 src/neural_weasel/neural_candidate_pages.py:404 的 lexical_deadline = max(absolute_deadline, self.clock() + _PAGE_ZERO_LEXICAL_DEADLINE_MS / 1000.0) 仍可另加 12ms 尾预算；src/neural_weasel/neural_candidate_pages_v3.py:1103 仅在 next(cursor) 之间检查截止时间，下一步内部不能被该检查打断。额外 sorted 仪表显示一次 pinyin_partial.py:224 排序 4459 个匹配耗时 6.9708ms，当次游标步 27.8054ms；排序只解释其中一部分，不能单独认定为全部根因。单请求 cProfile 亦确认游标在路径构造、queue_priority/path_priority 上做 CPU 工作；它有测量开销，仅用于识别函数。
- 完整/未完成拼音对照：zhuyid、zhuyide、gongzuo、weishenme 各五次。自然低驻留组首发 6/20 成功、硬缺页 +7373；立即再跑暖组仍为 6/20、硬缺页 +51，工作集约 109MB 保持。暖组 zhuyid 0/5、zhuyide 1/5、gongzuo 3/5、weishenme 2/5。因此不能把问题只归为未完成拼音，也不能只归为回收后缺页。统计按首发 response_ok，不把运输成功或探针退出 0 当成候选全成功；重试可能恢复，但首发延迟仍失败。
- 证据目录 C:\Users\zhaoy\AppData\Local\Temp\nw-deploy-context-20261002：word-baseline-summary.json、word-profile-summary.json、word-profile-stacks.json、word-profile-profiler.log；对应 requests/timings.jsonl；word-candidate-stages-perf_counter-off-gc-off-transport-False.json、word-candidate-sort-perf_counter-off-gc-off-transport-False.json、zhuyid-cursor-functions.prof；prefix-baseline-summary.json、prefix-warm-summary.json 及对应 requests/timings.jsonl。临时探针/仪表未部署。
- 下一步修复范围：让首屏沿用统一绝对截止时间，并将词法游标中的长工作拆成可检查预算/取消的步骤；验证后台旧修订工作及时退出、首屏七候选与固定分页/硬排序语义不回归。Mem Reduct 白名单实验独立保留，不以关闭回收代替搜索预算修复。当前未声称根治，真实输入请求的精确阶段关联和自然隔夜验收仍待完成。

## 2026-10-02 根治修复交接

用户要求准备 handoff，本次止于记录和校验；下面三个最新补丁尚未部署。原目标仍是根治间歇卡顿，并证明自然 Mem Reduct 回收及隔夜恢复鲁棒性。真人正常与首发探针仍超时同时成立，不能用其一覆盖另一项。

### 当前安装及验收

- 安装根：`C:\Users\zhaoy\AppData\Local\NeuralWeasel\Experimental\experimental-profile`。Q4_K_M、默认 continuation、原生 50ms 客户端预算及 Mem Reduct 设置保持；FIM 实现已安装但未启用。TSF DLL/前台未操作。
- 已部署候选修复：合作式拼音扫描与稳定排序；首屏沿用绝对截止时间；管理器及服务请求起点成对使用 perf_counter；不可变 `_HanSearchPath` 的完整路径标识缓存；首屏先补七个、后台继续补到固定35个，保留首屏及五页不可变语义。安装独有的单字优化继续保留。
- 最新已安装修复备份：`C:\Users\zhaoy\AppData\Local\NeuralWeasel\Experimental\backups\candidate-search-20261002-183053`。该次实际只更新 `neural_candidate_pages.py`；`rollback-candidate-search.ps1` 默认只读预检已通过，未执行真实回滚。上两次备份为 `candidate-search-20261002-175917` 和 `candidate-search-20261002-181424`；跨多个备份恢复必须逐层核对 SHA，不能用旧脚本越过后续部署。
- 本次现场读回：worker PID13532、父PID12152、创建时间 `2026-10-02T19:31:02.952283+01:00`，可执行文件 `C:\Users\zhaoy\AppData\Roaming\uv\python\cpython-3.12.11-windows-x86_64-none\python.exe`；UI server PID2332、创建时间 `2026-10-02T17:38:26.182079+01:00`。Model Service Q4 和 UI Server 计划任务均 Running。后续操作必须重新验证 PID、创建时间、可执行路径和任务动作。
- 用户最近在原编辑器验收 a/yanchi/zhuyide，回复候选和数字键提交都正常。这是当前已安装版真人验收，不涵盖下面待部署补丁，也不涵盖隔夜、保护字段或所有切焦点场景。
- 已安装首屏分批版公开 zhuyid/zhuyide/gongzuo/weishenme 各五次：首发10/20成功、硬缺页+6；zhuyid 0/5、zhuyide 1/5、gongzuo 5/5、weishenme 4/5。带采栈一组为12/20首发。仍未根治。证据前缀 `candidate-firstpage-prefix-*` 位于下述临时根；不能把重试成功或运输 ok 记作首发候选成功。

### 最新待部署补丁及证据

待部署恰为三份源码：`src/neural_weasel/pinyin_partial.py`、`src/neural_weasel/neural_candidate_pages_v3.py`、`src/neural_weasel/neural_candidate_pages_scored.py`。

1. **精确拼音先搜索，后续再走完整扩展游标。** `PartialPinyinMatcher.neural_matches`/`iter_neural_matches` 增加可选关键字 `exact_only=False`，默认完整语义不变；精确阶段不访问预测后代或 shorthand/incomplete 分支，完整结果与精确结果分开缓存。v3 为精确根计划设置独立缓存；完整可拼写且长度大于一的输入先枚举精确层，再恢复原完整遍历并去重。单字继续走原静态排名。RED 用1024个扩展读音证明旧版在第一个精确候选前构造1028个匹配；修复后先产精确候选，后续1024个合法扩展仍全部保留。
2. **及时释放递归扫描临时对象。** 合作式扫描的递归闭包会保留工作字典，结束/取消后等循环 GC 才释放。`iter_neural_matches` 的 finally 清空工作表并解除递归函数引用；不关闭生产 GC、不改变 startup freeze。弱引用回归在暂时关闭测试进程循环 GC 的条件下，旧版关闭游标后闭包仍存活，新版立即释放且不缓存不完整结果。这里只证明对象生命周期缺陷，尚未将真实服务 GC 暂停与每次卡顿逐请求关联。
3. **翻页等待与重试统一管理器时钟。** scored 层三处仍用 time.monotonic，与已切换的 perf_counter 截止域不一致；改为 self.clock()。注入时钟7.0的 RED 中旧版重试起点123.0，新版7.0且剩余5ms不变。该低层路径不等于此前首屏慢点，不把它冒称为首屏根因。

临时根：`C:\Users\zhaoy\AppData\Local\Temp\nw-deploy-context-20261002`。

- 组合 stage：上述根下 `candidate-fix-stage\src\neural_weasel`。从实际安装复制，再覆盖六份仓库源码及组合页管理器；总共七份受 SHA 管控，保留安装独有单字优化。生成脚本为 `prepare-candidate-stage.py`，清单为 `candidate-fix-stage\candidate-stage-sha256.json`。不能直接拿仓库页管理器覆盖安装文件。
- **最终组合回归581 passed、3 skipped、14.44秒**：`candidate-exact-tier-stage-tests.log`。原生恢复 executable 通过 NEURAL_WEASEL_RECOVERY_PROBE 指向 `C:\Users\zhaoy\AppData\Local\Temp\nw-context-review-20261002\native-build\neural_weasel_context_recovery_test.exe`，相关 Win32 故障回归实际执行；三个 skip 为可选 Torch backend/sparse 项。源回归首次出现 instrumentation wrapper 不接受新增 exact_only 关键字的失败，已仅让计数包装转发 kwargs，未放宽预算或移除行为断言。
- `public-matcher-equivalence.json`：公开53186行索引，212种输入位置/边界、96061个匹配，新旧所有字段及顺序相同。`public-lexical-firstpage-equivalence.json`：14种公开拼音、seed42 synthetic scores，旧/新前35个 Candidate 全字段及顺序相同；比较在内存完成，只落盘计数、公共拼音标签和耗时。
- 单次隔离首七候选计时：zhuyide 54.8468→1.9736ms、weishenme 35.2591→3.5243ms、gongzuo 22.7138→1.9158ms。未加载第二模型；这是局部单次测量，不是生产改善证明。未完成拼音 zhuyid 新版仍114.6407ms、h 92.0332ms、hywt 49.5686ms，均需继续处理；de 也没有稳定改善。完整拼音精确阶段的加速不能泛化到所有输入。
- 轻量 NamedTuple 词法路径实验虽然14组顺序一致，但没有稳定耗时优势，**已撤回**，未部署。保留原条件搜索路径表示和已经验证的标识缓存。
- 最后 SHA/AST 读回为 `handoff-candidate-deployment-state.json`，生成脚本 `verify-handoff-deployment-state.py`。七份安装文件均匹配安装 manifest，stage SHA 未变；除安装专属页管理器外，stage 与对应仓库源码相同。安装与 stage 差异正好是上述三份待部署源码。Ruff相关源码/测试 check 与 format 已通过，git diff --check 通过，仅行尾规范警告。

### 续接顺序与约束

1. 先读本节、最新 AGENTS.md 及 applicable playbooks；保留所有其他聊天 dirty/untracked。本地分支为 `codex/context-engine-fim-phase1`，HEAD为 `ff8cfcf6d614a61ad240a2932f5069f0f2881857`。本地未提交的 context recovery、native pipe、FIM实验、守护修复及文档不能被 reset、覆盖或一并误提交。此前 FIM PR42 已合并 main，本地 dirty 工作树仍需单独处理同步。
2. 最新三个补丁已有成套部署授权。部署前复查 stage SHA、当前服务身份及备份脚本，并在 `deploy-candidate-fix.ps1` 的 manifest 元数据补记本轮精确阶段/递归清理/导航时钟行为。该脚本默认只读预检，`-Apply` 才备份并替换有差异的源码、重启确切 Model Service Q4；先停止守护再子进程，避免守护自动拉起模型。保留 UI/TSF、量化、context mode、50ms客户端及 Mem Reduct。不要直接复用过期PID清理进程。
3. 部署后用新 tag 保存安装 SHA/manifest 校验、公开完整和未完成拼音对照、首发与最终重试分开的计数、只在批前后读的硬缺页数；新 worker PID/创建身份必须重读。需要证明总35/每页7、首屏IDs不变、旧修订及时取消、候选排序和安全字段不回归；不得用 reset 真实上下文来清理合成探针。真人验收使用原编辑器，由用户操作，不移动前台。
4. 下一条性能路线是未完成拼音的词法扩展成本，以及实际前台/后台竞争；不要只反复跑 a/yanchi 或给所有请求延长等待。精确层已加速，下一步必须保留合法根和原公平/排序语义，区分模型背景 restore、主锁、CPU搜索、缺页与 GC。现有 py-spy 样本无可靠逐请求关联，样本数量不能当耗时比例。
5. Mem Reduct 回收是用户预期。已证冷工作集伴随真实硬缺页，但完整拼音暖组也失败，故不是唯一原因。独立1.074MiB公共锁页 arena 仅接入单字/baseline读取，三组自身trim各40/40、硬缺页均0，未证明生产收益；**未部署**。自然 Mem Reduct 回收及隔夜真人验收仍待完成，不虚构应用排除设置，不锁 PRIVATE 或通用 Python 堆。
6. 直接 Windows/UIA 取上下文仍在 `docs/architecture/context-prompt.md` 的 Deferred Windows context acquisition TODO，未实施。保护字段零采集、PRIVATE不持久化、TSF非阻塞、身份校验和旧焦点禁止发布始终有效。日志/探针/采栈只存公共合成请求和白名单元数据，不保存真实编辑器或候选正文、窗口标题、locals或内存转储。

本次结束时没有运行中的探针、测试或部署操作；Sol reviewer 因账户额度失败没有产出本轮有效独立复核。当前生产继续运行已安装首屏分批版，最新三份补丁只在源码/stage；全部回滚预检均不等于真实回滚验收。根治及隔夜目标保持未完成。

## 2026-10-02 续接更新：分阶段后缀修复已部署，混合首发仍有失败

本节更新上一节的安装状态：上面“三份补丁尚未部署”是历史状态；三补丁及下面的分阶段后缀修复现在均已部署。原目标仍未完成，不能把专项或暖组成功写成根治。保留前台、Mem Reduct 设置、Q4_K_M、continuation 和原生客户端50ms预算；不保存真实编辑器/候选正文、窗口标题、locals或内存转储。直接 Windows/UIA 取上下文仍只是 TODO。

### 安装版本与回退

- 三补丁实际部署备份为 `C:\Users\zhaoy\AppData\Local\NeuralWeasel\Experimental\backups\candidate-search-20261002-192432`。最新分阶段后缀部署备份为 `C:\Users\zhaoy\AppData\Local\NeuralWeasel\Experimental\backups\candidate-search-20261002-195420`，此次仅替换 `pinyin_partial.py`、`neural_candidate_pages_v3.py`。最新回滚脚本默认只读预检通过，未实际回滚；恢复到更早状态须逐层核对，不跨层套用旧脚本。
- 当前 worker PID12536、父PID38872，创建时间 `2026-10-02T20:54:29.029265+01:00`，NT创建身份134354444690292653，可执行文件为 `C:\Users\zhaoy\AppData\Roaming\uv\python\cpython-3.12.11-windows-x86_64-none\python.exe`。UI PID2332、创建时间 `2026-10-02T17:38:26.182079+01:00` 保持。所有后续操作必须重新核验进程身份，不能沿用本节PID清理。
- 七份安装候选源码、组合stage及manifest读回一致，pending=0。组合stage继续保留安装专属单字优化；不得直接用仓库页管理器覆盖安装。候选源码均仍是本地未提交工作；其他聊天的context recovery、native pipe、守护/FIM资料和文档dirty/untracked保持，未checkout/reset/stage/提交。
- 当前安装 `production_pipe.py:156` 请求起点为 `time.perf_counter()`，`neural_candidates.py:213` 搜索默认clock也为 `time.perf_counter`，与stage/manifest一致。早期monotonic的15.6ms分辨率缺陷已经修复，不再作为本轮剩余失败的归因；旧临时probe的时钟说明不得当作安装版本事实。

### 修复机制及回归

- 未完成拼音的旧路径在首七之前构造全部后缀子路径，并先扫描全部预测后代。现在 `_LexicalSuffixSeed` 复用后缀匹配/排序，每个父路径只构造当前堆头；先枚举覆盖全部输入且completion_syllables=0的短后缀，再恢复预测及部分匹配尾流。候选yield后才推进尾流，避免第七个返回前展开长尾。保留全部合法后缀、根分组公平性、35候选/五页不可变语义和原优先级；父分数浮点舍入产生同分时按实际父分数排序，保留同分次序。
- `iter_neural_matches(..., exact_only=False, include_descendants=True)` 默认完整语义保持。unextended、exact和完整缓存分域；include_descendants=False只跳过输入覆盖后的预测后代，仍保留shorthand/incomplete匹配。非递归双阶段子路径推进避免再次引入递归闭包保留问题。
- 新增回归：1025条潜在子路径在首七前只构造≤32；七短候选加1024预测扩展时匹配构造由旧1033降到≤64；浮点同分排序与分域缓存不吞预测尾流。合作式搜索11项通过。最终组合stage **585 passed, 3 skipped**，Win32恢复测试通过显式NEURAL_WEASEL_RECOVERY_PROBE实际执行；三个跳过为可选backend项。测试日志 `C:\Users\zhaoy\AppData\Local\Temp\nw-deploy-context-20261002\candidate-phased-suffix-stage-tests.log`。
- 公开53186行索引、seed42 synthetic scores、无第二模型：14种拼音的前35个Candidate全部字段及顺序与旧版本相同。单次局部zhuyid首七46.9141→10.2345ms，hywt47.7085→19.021ms，但h49.8756→67.362ms；这些不是稳定生产延迟证明。`public-lazy-child-firstpage-equivalence.json` 已覆盖先前lazy实验的同名结果，只含本轮版本。
- 部署前一次format检查失败后PowerShell仍继续了部署；随后仅规范matcher文档行尾，并验证AST完全相同后原子同步repo/stage/installed及manifest。最终Ruff格式和七SHA通过，测试覆盖同AST语义，未将格式失败称作通过。纠正证据为 `candidate-phased-suffix-formatting-correction.json`，同下述临时根；没有行为补丁未经回归混入安装。

### 实际服务结果：专项改善，但仍未根治

以下均为公开拼音、epoch0、同一生产worker、原生50ms预算；仅candidate_page_timeout按既有100ms间隔重试，运输timeout不重试。计数严格按首发response_ok，不把错误响应的运输ok或最终重试成功算首发成功。批前后硬缺页计数不能逐请求归因。

- 三补丁版本暖组：zhuyid 0/5，35.514–36.582ms返回candidate_page_timeout；zhuyide/gongzuo/weishenme合计15/15。分阶段后缀版本专项 **20/20首发成功**，每次7候选；zhuyid 5/5、8.680–25.715ms，其他三词各5/5、约1.4–18.1ms；硬缺页+8。
- 扩大到12词×3的混合组：**26/36首发成功**，3条candidate_page_timeout、7条运输timeout；后者约61–64ms，尚未有服务端逐请求阶段关联。de第一次额外4次重试仍失败后恢复；hywt第一次额外10次重试全部失败。整组硬缺页+43。对应暖组 **36/36**、约1.4–17.4ms、硬缺页+3，不能用暖组覆盖先前失败。
- 另12个新的完整拼音×3：**35/36首发成功**；shijian首次candidate_page_timeout、53.226ms，随后两次成功；硬缺页+4。这组开始前工作集已由前一组约1.13GB降到74125312 bytes，结束122576896 bytes；没有采到下降瞬间或清理发起者，不能单凭工作集证明Mem Reduct导致该失败。
- 带py-spy的混合暖组有38样本、3采栈错误及464条行号解析警告，存在不可信帧，**不作因果或耗时比例证据**。已删除该次原始栈文件并将日志替换为数字汇总，只保留按安装源码AST函数名/路径白名单过滤的safe-stacks；后续勿直接重用该采样分支保存未过滤错误帧。
- 隔离计时包装最初漏转发exact_only导致探针TypeError，已修正；假backend异常类型也改为CandidatePageTimeout，不算生产异常。v2隔离两组均33/36首屏成功，失败均hywt；无实模型的词法后台执行段约73–80ms，但未测到主线程锁等待。启动GC统计混在事件表，不能把最长GC事件直接归到请求，也不能据此断言真实后台竞争原因。

### 续接入口与未完成事项

- 临时根为 `C:\Users\zhaoy\AppData\Local\Temp\nw-deploy-context-20261002`。本轮安全证据前缀：`candidate-phased-suffix-prefix-*`、`candidate-phased-suffix-matrix-*`、`candidate-phased-suffix-matrix-warm-profile-*`、`candidate-phased-suffix-novel-*`；包含requests/timings JSONL及summary，全部只含公共标签、状态、耗时、候选数量和进程数值。
- 源码和stage已经是完成并部署的分阶段后缀版本，**不是**旧摘要中的未验证中间patch；不要重复部署或重新实现。生成组合stage使用现有 `prepare-candidate-stage.py`，安装核对使用 `verify-phased-candidate-final-state.py`。用户之前的真人“候选和数字键提交正常”属于早一版；本轮新验收已异步询问，未收到时保持待验收。
- 下一步优先补齐同一请求的安全数值时间线，区分进入服务线程、主锁等待、root计划/词法单步、写回，以及同时发生的GC/后台补页/模型工作；采样栈不能替代阶段关联。现有证据不足以把剩余运输超时归为后台竞争，先证明再改。不要延长50ms预算、关闭GC/内存回收或reset真实context来得到绿组。
- 根治、自然Mem Reduct回收后稳定性、隔夜恢复、最新真人验收仍未完成。公共锁页arena未部署，白名单路线保留；保护字段/切焦点等完整安全验收不能由epoch0公共探针代替。没有本轮有效独立Sol审核；无运行中的测试或部署操作。

## 2026-10-02 逻辑审查更新：首屏活性修复已部署，残余超时仍未根治

用户要求关注逻辑。本轮按状态迁移审查并构造反例，没有以测试数量代替根因证明。以下更新上一节安装状态；保留前台、Mem Reduct、Q4_K_M、continuation、原生50ms预算及其他聊天的 dirty/untracked；不保存真实编辑器/候选正文、窗口标题或locals。

### 已证明并修复的三处状态问题

- 首屏后台原先以35项为批量目标，补足七项后仍不发布，可能等第八项及后页慢路径。公开 nihao 索引、真实词法 cursor 的第八项人为阻塞时，原版报 `AssertionError: seven ready candidates were withheld by later-page work`。现在首批只补七项缺口，合并后按去重后的实际数量重新计算缺口，七项就绪即在锁内冻结首屏，后页继续同一 cursor；合并前仍检查取消和 session 身份。
- 首屏 continuation 的一次空结果原先在 finally 无条件标记搜索完成，同 identity 重试可冻结 literal fallback，不再调用已经恢复的 provider。公开 nh、真实 backend 的一次空结果反例报 `AssertionError: a single empty attempt permanently froze the literal fallback`。现在只有 session.exhausted 或首屏已经冻结才标终态；临时空结果、取消及超时保留恢复入口。
- 独立 Sol xhigh 审核指出第二项修复会把确定死路也保留为可恢复。公开两行索引 ni/hao 输入 nq，原中间版本报 `AssertionError: proven dead edges kept restarting first-page workers`。现在只退役完整约束遍历证明 legal_token_ids 为空的路径；剔除这些 frontier，登记 expanded_paths，仅整个 frontier 空时置 exhausted。有合法边但 provider 暂时空结果仍回滚、重试。独立复核确认合法边检查不是时间片截断，未发现新增身份/取消/发布阻断。
- 三个功能反例保存在 `C:\Users\zhaoy\Downloads\neural-weasel-repo\tests\test_candidate_page_zero_liveness.py`。不足七项的小索引补词法消费后丢弃、后页 provider 抛 RuntimeError/ValueError 后 page1 单独重试无工作者入口，仍是未修的逻辑风险。无条件合并小索引补项的尝试改变六项已有等待语义，已撤回；不得把这两个风险写成已修或已证线上原因。

### 精确安装与验证边界

- 组合 stage 从已安装源码只替换页管理器三个方法及 scorer 的确定死路处理，AST核对其余安装方法未变，保留安装独有单音节优化。两文件部署备份：`C:\Users\zhaoy\AppData\Local\NeuralWeasel\Experimental\backups\candidate-search-20261002-203228`。默认只读回滚预检不等于实际回滚；恢复旧版须按备份层次核对。
- 精确 stage 全量 **589 passed, 12 skipped in 13.69s**，601项收集；12跳过均 `isolated native probe required`，本轮没有设置 NEURAL_WEASEL_RECOVERY_PROBE，不能声称本轮重跑了native恢复门槛。36项相关验证、三个目标文件Ruff及diff检查通过；独立Sol审查为静态证据，不冒充其运行复现。
- 安装七文件SHA与stage/manifest严格一致，pending=0。页管理器SHA `d6732f958988d7862f53cdfa81c26b22140f49dcf8f0a3b6ad56a8a3da8c04b1`，scorer SHA `ce9d80107fef033312654d02840e384dc58dcc49297c5c90eff27d9807ede6fe`。scorer与repo的字节SHA差异来自CRLF，规范化文本及AST相同；页管理器与repo差异包括保留的安装专属优化，不能整文件覆盖。
- 部署后真实worker PID28860，创建 `2026-10-02T21:32:36.339623+01:00`，NT身份134354467563396234；UI PID2332仍创建于 `2026-10-02T17:38:26.182079+01:00`。后续操作重新核验身份，不复用PID清理。此次只重启模型任务，没有重启UI/TSF或修改Mem Reduct。

### 实际服务仍有失败，不能宣布完成

- 新版公开12词各3、epoch0、原生50ms预算：**29/36首发候选成功**；5运输timeout（shurufa/weishenme/gongzuo/shi/de，61.766–64.093ms），2条首发candidate_page_timeout（de 37.590ms、hywt 12.268ms）。de两次错误响应后成功；hywt同sequence额外10次重试仍全部candidate_page_timeout，后来新identity的两次成功。错误响应的transport ok不计候选成功，最终重试成功不计首发成功。
- 批前后硬缺页+35、工作集463425536→474902528 bytes，不足以证明某次失败由缺页或Mem Reduct引起。现有客户端等待只能说明响应未及时到达，不能把约63ms全部归到搜索。
- 本轮安全证据根为 `C:\Users\zhaoy\AppData\Local\Temp\nw-deploy-context-20261002`：`page-zero-liveness-tests.xml`、`page-zero-liveness-installed-state.json`、`page-zero-liveness-live-first-summary.json`、`page-zero-liveness-live-first-requests.jsonl`、`page-zero-liveness-live-first-timings.jsonl`。失败实验备份为同根 `page-zero-liveness-experiments.py`，不是生产代码。
- 下一步分别解释 hywt 同identity持续pending与跨词运输等待，用同请求的安全数值时间线拆服务线程进入/主锁等待/搜索/写回，不能反复跑暖组或扩大预算。静态另见 continuation generation 等待内层不检查总deadline、后页wake.wait依赖取消；目前只登记边界风险，不断言其是当前线上原因，不擅改等待策略。
- 根治、隔夜/自然回收鲁棒性、此版真人候选提交和完整安全验收仍未完成。直接Windows/UIA取上下文、公共锁页白名单路线继续TODO。结束此记录时无运行中的测试、探针或部署操作；独立reviewer只读任务已结束。


## 2026-10-02 22:49 BST — 实际锁因果、简拼发布与补页修复续接

本节覆盖上一节后本聊天的实质修复。用户要求继续查失败原因并修复，最新仍要求继续；不能将以下管道成功写成隔夜根治或真人验收。

### 三条实际失败链及修复

- 安全数值追踪只记固定阶段码、OS线程、QPC时刻、数值计数和严格synthetic请求序号，不保存真实keys/候选/编辑器正文、函数参数或locals。原主锁上后台首屏CPU连续批次持锁199.167/257.751ms，同请求前台等锁113.016/207.657ms，证明至少这些运输超时由后台占锁造成。scorer现每完整批次先发布、累计5ms后释放锁并可取消等待1ms，再恢复锁；generation idle wait使用一次建立的2500ms绝对截止，不随唤醒续期，不强行清除真实provider lane。两个机制RED分别为 successive CPU batches held the foreground state lock / an occupied provider lane outlived the worker deadline。
- 单字Han根top-k改为np.lexsort完整原rank列，排序后每identity取第一项。公开53186行索引、合成scores，14词×3score变体×2limit=84逐Candidate字段等价；shi旧1.0941ms→新0.08245ms中位，de1.0585→0.06795ms。排序中不可中断，不保证硬时限。真实de仍有Latin阶段30.513ms，不能把Han优化宣称根治。
- hywt无诊断原同identity21次重试、2500ms窗口仍无候选。新纯数值追踪确认runtime前三次完整完成两branches，后台已产122条完整Han，其中60条预测零桶，但minimum_future_bucket=0，freezable=0。不是provider一直空转，而是原 strict candidate.predicted_syllables < minimum_future 阻住已完成零桶。页管理器仅在首屏未冻结、Han、完整输入、预测桶0同时成立时放行当前排序快照；0桶已是最短，不破坏跨页短桶单调。冻结后恢复原过滤；不保证以后同桶高分候选改写首屏。实际RED completed neural candidates were hidden by shallower search roots。
- 放行hywt首屏后暴露另一条后台补页路径：tail线程连续持锁705.451/856.546/1071.033/1123.436ms，_ensure_freezable最长2529.356ms。6次运输超时中5次与该线程持锁重叠，等锁94.025–278.982ms。_run_page_preparation在锁外调用scored.query_page，后者持一次锁，基础query不再取锁；不是误猜嵌套RLock。继承的ensure loop不断resume+sort，CPU路径不经过provider解锁。新pages._ensure_freezable对已冻结首屏、登记的补页工作者，在完整展开/排序后累计5ms让锁；恢复后重查session对象身份和cancel，即使本步已exhausted亦检查；截止不续期，排序不改。真实preparer路线的两个RED：later-page CPU steps monopolized the foreground lock / invalidated later-page search continued after yielding。修后71相关通过，独立Sol静态复核无新增阻断。

### 当前精确部署与验收

- stage始终基于installed，只逐方法替换，AST核对其余方法未变，保留安装专属单音节优化，禁止整repo页管理器覆盖安装。最新stage全量594 passed, 12 skipped in 14.98s（606项）；随后设置已有隔离Win32 recovery probe，12个native/故障矩阵全部通过1.53s。两次范围互补，不说后一次重新跑全量。Ruff及阶段格式检查通过。XML证据为 C:\Users\zhaoy\AppData\Local\Temp\nw-deploy-context-20261002\later-page-fairness-tests.xml 与 C:\Users\zhaoy\AppData\Local\Temp\nw-deploy-context-20261002\final-isolated-native-tests.xml。
- 四层备份按部署顺序： C:\Users\zhaoy\AppData\Local\NeuralWeasel\Experimental\backups\candidate-search-20261002-205859（后台首屏公平性）、C:\Users\zhaoy\AppData\Local\NeuralWeasel\Experimental\backups\candidate-search-20261002-210839（vector根选择）、C:\Users\zhaoy\AppData\Local\NeuralWeasel\Experimental\backups\candidate-search-20261002-213103（零桶发布）、C:\Users\zhaoy\AppData\Local\NeuralWeasel\Experimental\backups\candidate-search-20261002-214126（补页公平性）。各备份只覆盖该层文件，不得以最后一个单文件回退称为全阶段回退；本轮没有实际回退演练。
- 安装7文件与stage/manifest严格SHA一致pending=0。pages SHA dba70e0829ce16f788d3eb4fdaa1e0eb9fae959ab45e8c84f7cb24b6efe5d807；scored SHA6a59756ebe856f73082957b90a5734dfa47e9a677c85485b85b440a067865800；v3 SHA686fd1ae0bec71e9112d1b86711945638738c9a2cb417b3cadbf07b52c8cac4d。读回 C:\Users\zhaoy\AppData\Local\Temp\nw-deploy-context-20261002\final-candidate-installed-state.json。
- 补页修复的诊断批首发33/36、最终36/36、0运输timeout；最大前台等锁10.7903ms，后台最长持锁68.3926ms，仍超过5ms阈值，不能承诺单步硬上限。追踪下shi/de/hywt分别169.576/177.017/568.225ms恢复。证据 C:\Users\zhaoy\AppData\Local\Temp\nw-deploy-context-20261002\later-page-fairness-trace-outcome.json。
- 已实际删除临时 C:\Users\zhaoy\AppData\Local\NeuralWeasel\Experimental\experimental-profile\python-service\.venv\Lib\site-packages\nw_numeric_diagnostic.pth，并只重启owned模型任务卸载hook；最终TestPath=false，无py-spy/计时monkeypatch。当前正常worker PID25720、创建2026-10-02T22:44:55.998985+01:00、NT身份134354510959989856；UI2332仍17:38:26.182079+01:00，MemReduct16068仍昨日启动，两计划任务Running。后续重核验PID身份。
- 无Python诊断、正常nativeclient、12公开词×3、每request原50ms、同identity至原生2500ms窗口：首发33/36，最终36/36，0运输timeout。shi首次35.288ms/1retry后143.4215ms，de38.531ms/2retry后319.2025ms，hywt9.389ms/5retry后559.1594ms。错误响应transport ok不算候选成功；硬缺页+71不是逐请求MemReduct因果。证据 C:\Users\zhaoy\AppData\Local\Temp\nw-deploy-context-20261002\final-normal-recovery-outcome.json 及同根first-requests/summary文件。
- 公共nihao/zhuyid/hywt实际5页×7项=105项均可取得，页0同identity重放候选逐字节内存比较相同（不落正文）。nihao/zhuyid页1约105/112ms，其余后页约0.26–0.75ms。该批处于已暖服务，不当冷简拼后页证据。初probe错误给page0附candidate_set_id触发协议拒绝，改为合法同identity无set-id重放后通过，非产品缺陷。数值文件 C:\Users\zhaoy\AppData\Local\Temp\nw-deploy-context-20261002\normal-public-paging-metadata.jsonl。

### 继续目标和限制

- 用户最新要求继续。下一步优先解释shi/de前台首次构造、Latin根筛选/内存驻留成本，依据实际分阶段信息做等价修复；不重复无假设暖探针，不扩大50ms预算。首屏重试已能恢复，但首发未全绿，不称完全不卡。
- 自然MemReduct/隔夜、最新真人TSF候选/翻页/数字提交、完整保护字段安全验收尚未完成。新的真人问题仍待回复；旧版正常回复不能替代最新版。
- 原TODO保留：单token多edge+pending扫描/原生排序不能保证单步时限；首屏取消对深根同桶覆盖的调度边界；小索引1–6词法项消费后丢弃；后页provider一般异常后的独立重启入口；直接Windows/UIA取文；公共热页驻留/白名单（MemReduct预期保持，不关闭工具）。这些未证现场因果，不擅称已修。
- 其它聊天dirty/untracked未checkout/reset/commit覆盖。本阶段只部署Python候选模块，未更换Q4/continuation、TSF/DLL、前台窗口或MemReduct设置。


## 2026-10-02 23:18 BST — Latin首次根构造的等价优化，已部署但首发未全通过

- 公开GGUF只解析tokenizer元数据（248320词表、105337合法Latin根）；未读取权重、未加载第二模型、未读取编辑器正文。原查询会扫描同initial全部词（shi 8654条、de 5083条），现在构造时仅将公开单token、ASCII合法、长度≤64的词表做排序索引，查询取descendant区间和proper ancestor，再恢复原ordinal。重复词/大小写/同token路径及原候选去重顺序保持，不缓存查询或PRIVATE快照。
- 第二处开销是scored层对字段已经正确的候选再次replace：仅在consumed_keys和completes_input均已正确时复用冻结Candidate，否则仍修正原字段；epoch处理、baseline缓存门槛、上下文打分不变。原型de总Latin根构造中位12.4837ms→7.9870ms；最终同新prefix索引比较复制策略10.34805→6.16785ms。隔离新策略仍有104.976ms偶发尾值，不能用中位数承诺时限或排除GC/调度/内存影响。
- 新同initial无关prefix测试原版读取1000条不相关词、RED assert 1000 == 0，新版通过。实际补丁独立比较188组完整Candidate+frontier，复制策略再比较376组（baseline/context、epoch0/7、重复、64/65字符和非法输入），字段完全等价。Sol独立静态审核无阻断，边界是构造时公开词表必须静态。
- 精确stage完整607 passed in 27.70s，包含12项隔离native恢复测试，本轮无skip；Ruff check/format通过。只部署neural_candidate_pages_v2.py和neural_candidate_pages_scored.py，保留其它安装独有优化。备份 C:\Users\zhaoy\AppData\Local\NeuralWeasel\Experimental\backups\candidate-search-20261002-221835。8份安装/stage/manifest SHA一致、pending0，诊断PTH不存在。
- 正常worker25700（2026-10-02T23:18:42.749105+01:00），UI2332/MemReduct16068保持原身份。正常native客户端、无诊断/pyspy、50ms请求和原2500ms恢复窗口，公开12词×3：首发34/36，最终36/36，运输timeout0。shi本轮首发成功；de35.540ms先pending、同identity167.9643ms恢复；hywt10.141ms先pending、562.0965ms恢复。硬fault整批+32，未做逐请求或MemReduct因果归属。仍不能称根治或最新真人TSF验收。
- 证据均在 C:\Users\zhaoy\AppData\Local\Temp\nw-deploy-context-20261002：latin-prefix-final-comparison.json、latin-prefix-stage-tests.xml、latin-prefix-installed-state.json、latin-prefix-normal-outcome.json及first-requests/summary；源码原始参考单独留latin-prefix-reference-neural_candidate_pages_v2.py/scored.py。计数/公开样本允许存储，真实正文/候选不落盘。
- 继续拆de汉字根匹配：公开索引隔离cProfile首轮1775 plan、总约23ms（profiling开销，不当生产时延），其中neural_matches约16ms、cooperative_sorted约5ms，产生8998次IndexedPronunciation哈希。完整同步消费者把所有checkpoint丢弃，分块排序/堆合并可能是可减工作，待等价和性能验证，不先改生产。


### 23:32 BST — 同步拼音匹配稳定排序优化（已部署）

- 逻辑：neural_matches是完整tuple同步消费，会丢弃iter_neural_matches全部None检查点。为该路径用同一排序键的原生稳定sorted；iter_neural_matches默认仍为分块cooperative_sorted，遍历checkpoint、暂停/取消、finally清理和完整tuple缓存不变。共享缓存成立，因为旧排序用全局原始位置处理平局，与稳定排序等价。没有新持久化或上下文入口。
- 公开索引168组完整匹配序列旧/新一致，额外校验两种方向缓存复用及13952个默认后台检查点；de根构造中位9.9010→8.5258ms、shi8.88365→7.51265ms、h12.69225→10.34470ms。Sol独立静态核对无阻断，原生sorted不可中途取消，仍不承诺硬时限。
- 格式检查先发现一处生成器排版，修正时临时stage准备脚本仍要求stage==安装原版导致AssertionError（未动生产）；改为强校验原安装SHA、当前stage SHA以及剔除两方法后的AST，同时允许已准备的等价stage，未忽略断言。最终准确stage607 passed in15.41s（含12native、无skip）、Ruff通过，只部署pinyin_partial.py，8stage/安装/manifest一致pending0。SHA3b154e1e7c7e359a7a70084ec92457f9a604f9c029df4f00f9e51fbb1b723a73。
- 备份 C:\Users\zhaoy\AppData\Local\NeuralWeasel\Experimental\backups\candidate-search-20261002-223227。正常worker28964，创建2026-10-02T23:32:35.240785+01:00（NT134354539552407856）；UI2332/MemReduct16068仍原身份；诊断PTH不存在。
- 正常无诊断native 50ms+原2500ms恢复窗口公开36请求：首发34/36、最终36/36、运输timeout0。de首35.863ms、1retry后162.4759ms恢复；hywt首7.656ms、5retry后562.0915ms恢复。整批硬fault+89，不能逐请求归因或归咎MemReduct。首发计数未改善，不能宣称根治。
- 证据 C:\Users\zhaoy\AppData\Local\Temp\nw-deploy-context-20261002 下 synchronous-match-final-comparison.json、synchronous-match-stage-tests.xml、synchronous-match-installed-state.json、synchronous-match-normal-outcome.json及first-requests/summary。完整公开根构造profile显示de生成1078个候选、1685条frontier；profiled总59.5294ms（有cProfile开销，不当生产时延），Latin约23ms，Han约34ms。继续验证先按等价key挑出重复Latin根赢家再分配Candidate；不能删深根frontier或改变排序契约。

### 2026-10-03 00:27 BST — 重复模型状态恢复定位与最小修复（已部署，尚未根治）

- 23:54已部署Latin根预去重：按同文本的(-value, token_path)选赢家后才构造Candidate，全部原始frontier、首次出现顺序、平局和PRIVATE缓存边界保留。1092组完整字段/前沿等价，准确stage609项通过（含12项native，无skip），独立Sol审核通过；备份 C:\Users\zhaoy\AppData\Local\NeuralWeasel\Experimental\backups\candidate-search-20261002-225358。普通服务首发34/36、恢复36/36、运输超时0；de157.6538ms、hywt557.5387ms恢复，不能称首发改善。
- 隔离公开索引/合成分数冷根实验必须模拟真实启动的gc.collect()+gc.freeze()。漏freeze的186.95ms/GC178.57ms尾值不是生产因果；补freeze后48/48构造成功，35ms预算下de8/8、中位16.6555ms、最大18.6586ms。不能据漏freeze实验调整生产GC，也不能把合成分数结果当实机模型证明。
- 当前安装临时数值追踪精确复现：de处理37.0015ms，root_han_plan25.4446ms、Latin7.2355ms，new_session36.7647ms失败且sessions=0；重试重新构造并发布。hywt首10.663ms已有会话，后台续算569.9418ms，其中模型provider490.7965ms、两分支完成；诊断574是返回分数数量，**不是解码token数量**。该批硬缺页仅+5，未证明这两次由Mem Reduct造成。
- 普通服务15秒py-spy采样（无locals）中，含runtime的叶样本权重合计8.60秒，初始llama_state_seq_set_data4.32秒、最终恢复3.35秒，约89.2%停在状态恢复；这是多线程采样占比、覆盖整批及后续后台工作，不能当某条请求的精确墙钟分解。原生模型状态复制是真实慢路径，不能只继续削候选分配成本。安全证据均在 C:\Users\zhaoy\AppData\Local\Temp\nw-deploy-context-20261002：current-root-service-37876.jsonl、current-root-traced-first-requests.jsonl、current-root-normal-profile-stacks.json及对应outcome。临时钩子已移除并恢复普通服务；第一次添加脚本因默认GBK读UTF-8失败却执行了一次无追踪重启，随后显式UTF-8修正，不影响源码或前台。
- 最小运行时修复：持runtime锁、软件tokens/logits匹配、保存root buffer/tokens匹配且实际n_tokens相等时，跳过一次多余的初始根恢复；缓存缺失和不同根仍重放/恢复，分支间及最终恢复保留。为该缓存证明，continuation/legacy清理先撤销软件标记，private失效先撤销tokens/logits及保存root state，再清GPU。独立Sol发现private部分清理抛错会留旧标记，已用状态依赖fake做RED并修复；覆盖实际分支分数、不同上下文、n_tokens不符、清理异常和私密失效，不只数调用次数。
- 最终准确stage615 passed，17.270秒、含12项native、无skip；Ruff及独立Sol最终审核通过。只部署llama_runtime.py，SHA1cedecb0ecb33ce85d43a19fc8923ecdf6d6c7208fafb2ee8096032ac5037457；备份 C:\Users\zhaoy\AppData\Local\NeuralWeasel\Experimental\backups\candidate-search-20261002-232728。9项安装/stage/manifest SHA一致pending0，无诊断PTH。普通worker20732创建2026-10-03T00:27:35.741131+01:00（NT134354572557411314）；UI2332、MemReduct16068原创建身份保持，Q4/continuation及50/35/2500ms预算保持。
- 无诊断普通管道公开36请求：首发仍34/36、最终36/36、运输超时0；de36.758ms首发超时、161.8348ms恢复；hywt8.853ms先pending、第4次重试451.3490ms恢复，前版第5次557.5387ms。单批观察更快，不足以承诺普遍提升或根治；整批硬缺页+24，不逐请求归因。证据同绝对scratch目录live-root-reuse-stage-tests.xml、live-root-reuse-installed-state.json、live-root-reuse-normal-outcome.json及first-requests/summary。
- 后续：de首次根构造仍会丢弃未注册会话的工作；研究可续算构造边界，保持deadline和latest identity。公开matcher按canonical条目身份替代重复结构哈希仅为未部署原型：168组完整匹配及双向缓存一致、13952后台检查点，de中位7.6773→6.3090ms，shi反而7.5408→8.37045ms，存在未freeze旧尾217ms，需先冻结/交错对照，不能据此直接部署。保留直接Windows取文及公共热页白名单TODO；最新真人TSF回复、自然MemReduct回收及隔夜验收仍待，不宣称完成。

### 2026-10-03 01:11 BST — 长简拼首屏资格与完整token路径修复（已部署）

- 用户反馈九initial无法出任何字。只在内存中复现该请求：第一次26次、第二次60次请求（6.303秒）持续candidate_page_timeout；不将实际缩写、目标句或返回候选正文写入测试/证据。静态机制与实测一致：旧词法资格只接受完整拼音或至少两完整音节加末前缀，纯长简拼资格False；首屏又过滤所有未覆盖整串的Han，因此同identity重试不打开词法补全路线。不能据此宣称神经路线永远无法完成。
- 最小展示策略：保留原eligible=True输入；原False输入仅当合法解析的最少音节数严格大于4时开放词法首屏。DP包含完整音节、单initial、仅末组的合法多字母末前缀，并尊重显式分隔；公开nhrsh与n'h'r'sh仍按4处理，原短输入等待策略保持。阈值是产品策略，**不是NN token深度证明**。新候选仍须完整覆盖输入、tier50，模型分数候选优先及发布前session身份/取消检查保留。
- 混合索引是实际部署阻断，不能假设全single：53186条中49195条长度1，3991条multi（3712长度2、95长度3、184长度4），multi全部coverage=True/token_id=None。旧词法根会截断为首token，后缀会直接排除multi。修复两处根保留完整token_path；后缀与精确拼写分支按完整路径分组/拼接、真实总token数上限16及文本上限64；根首token仍取当前context分数，其余取永久baseline，词法续路径以baseline作排序估计，不冒充条件模型打分。单token coverage原排除行为保持。
- 独立Sol发现lazy后缀head的混合长度排序缺口，已修：归一化使用真实token数；父路径下异长种子重排；suffix key投影完整child优先级，补token长度、token总和、text/path平局顺序。新增15项公开用例覆盖baseline/context、multi根+suffix完整路径、5/6-token根对应完整路径16/17的预算边界、短suffix优先于高分长suffix、同长度sum/path平局及短输入不变。资格旧安装两个功能RED、仅guard版multi两个功能RED，最终15项GREEN。独立Sol最终无部署阻断，不能承诺checkpoint间单步或首屏硬时限。
- 准确stage只改v3两个方法及新策略常量，其余AST与旧安装一致，9项其他stage/installed SHA先验证相等；保留neural_candidate_pages.py安装独有优化。最终630 passed，25.420秒、0fail/error/skip；含3项Windows原生恢复探针与12项原生契约检查，不能把契约检查全当实际native执行。Ruff check/format--check通过151文件。stage证据 C:\Users\zhaoy\AppData\Local\Temp\nw-deploy-context-20261002\long-shorthand-stage-tests.xml、long-shorthand-index-scope.json；索引SHA d4c1873371c8f63c9dfdcbee1c723fa05d17f5b8f23092a1824aeeb5cc60bfae。
- 已部署v3一文件，SHA167a2a2fdf382c3a32466c42aea5d25c3f30fd2e6184e4352070a46a78ef8a75；备份 C:\Users\zhaoy\AppData\Local\NeuralWeasel\Experimental\backups\candidate-search-20261003-001120。独立验证9项stage/installed/manifest一致pending0，无diagnostic PTH。worker10808创建2026-10-03T01:11:28.240102+01:00（NT134354598882401022），UI2332/MemReduct16068原身份保持。Q4/continuation/client50ms/server35ms/recovery2500ms保持。
- 实际用户case用当前原生NamedPipeClient经stdin接收内存请求，50ms单请求、2500ms恢复；首发36.2848ms pending、一次100ms间隔重试0.4153ms成功，总141.4213ms，返回7 Han且7条completes_input=True，已从持续无字恢复；目标整句**未命中首屏**。只落盘数字/布尔 C:\Users\zhaoy\AppData\Local\Temp\nw-deploy-context-20261002\long-shorthand-reported-case-outcome.json，不含缩写、目标句、候选正文或candidate IDs；通用探针源/编译产物均同绝对scratch根stdin-candidate-probe.cc/.exe。
- 无hook/无profile普通公开36请求首发34/36、最终36/36、运输超时0；de35.463ms首发超时，164.2115ms恢复；hywt9.844ms pending，第4次451.5235ms恢复。整批硬缺页+55、WS120254464→228974592bytes，不能逐请求归因或声称全局卡顿根治。证据同scratch目录long-shorthand-normal-outcome.json、long-shorthand-normal-first-requests.jsonl及summary、long-shorthand-installed-state.json。metadata日志按白名单读，不保存正文；recent筛选已加当前tick上限以排除历史未来tick记录，日志邻近不等于当前请求因果。
- 未完成：最新真人原编辑器候选与数字提交验收（已询问，等待回复）；目标整句首屏排序/可达性未证明；de冷根构造丢工作与短简拼后台等待仍在；自然MemReduct回收/隔夜验收仍需做。保留直接Windows取文与公共热页白名单TODO，不改MemReduct、不移动前台、不把以上局部修复标为总目标完成。

### 2026-10-03 11:09 BST — Latin根显示分配上限（已部署）

- 用户明确继续修复，并说明Engram之前不要求目标整句命中；后续验收以稳定出候选、数字选字及响应时间为准。冷根profile仅用于定位分配成本：公开de每次约1071个Candidate与613个frontier；保留所有续写路径，只减少最终具备180项硬容量的生产pager中Latin根显示对象分配。legacy pager默认不启用上限，首屏混排规则不变。
- 独立Sol发现不能仅裁Candidate：旧seen会抑制低分根以高分续写重新进入及写入baseline记忆。修复为immutable末尾marker，保存省略的最终赢家的区分大小写NFKC(text)/consumed_keys身份；缓存强弱及平局仍按完整旧_latin_key决定赢家。Chinese-first注册时补回seen；Latin-first原5项、cached-Han旧180项分支保持原seen语义。预热/缓存命中/会话构造均O(1)剥离marker，两个缓存命中返回独立list，marker不进入实际搜索frontier；context/PRIVATE不进入baseline缓存，没有新增正文持久化。
- 11项新用例与旧Latin6项通过；204组公开prefix×baseline/context比较top180完整Candidate字段、全部frontier及seen一致。公开冷根隔离24样本de中位39.72355→35.9608ms、max47.3753→42.3485ms；shi中位25.25235→26.2794ms、hywt7.874→8.35445ms，不宣称全面提速。准确stage641 passed、39.526秒、0fail/error/skip；Ruff152文件通过。四文件最小部署保留安装pager独有优化，9项stage/installed/manifest SHA一致。备份 C:\Users\zhaoy\AppData\Local\NeuralWeasel\Experimental\backups\candidate-search-20261003-100859。
- 无profile/hook的实际公共36请求仍首发34/36、最终36/36、运输失败0；de36.517ms超时后167.9335ms恢复，hywt7.162ms pending后334.9436ms恢复。整批硬缺页+32不可逐请求归因。证据 C:\Users\zhaoy\AppData\Local\Temp\nw-deploy-context-20261002\root-display-bound-stage-tests.xml、root-display-bound-public-comparison.json、root-display-bound-normal-outcome.json及normal-first-requests/summary；此补丁没有单独解除de冷首发压线。

### 2026-10-03 11:22 BST — 固定公开拼音根启动预热与真人验收（已部署）

- 在原52单字母/语言模式预热之后，仅为固定公开de/shi构建静态Han匹配plan；不预存评分或整页。启动链已核查：service_factory.build_bilingual_engine调用initialize_neural_baseline，同步预热完成后CLI更新空context、GC freeze，再启动listener。plan只含词表匹配字段，后续仍按当次context评分；无索引返回空。原LRU上限256不变，不承诺淘汰后的任意冷输入或OS自然回收时延。
- 两项真实公开小索引测试用40ms匹配成本重现旧代码首次35ms建会话CandidatePageTimeout，新版通过并核对新context分数与epoch7；19项相关回归通过。准确stage643 passed、29.171秒、0fail/error/skip，Ruff153文件及diff检查通过。仅v3预热方法和常量增量，其余AST与旧安装一致；备份 C:\Users\zhaoy\AppData\Local\NeuralWeasel\Experimental\backups\candidate-search-20261003-102237。v3 SHA cbed69f104246c889a0b511a7ce955363dde52b7b2e6db92e45d7cd9bd83f39a，9项stage/installed/manifest一致pending0。
- 普通worker20076、parent32104，创建2026-10-03T11:22:46.132358+01:00；GetProcessTimes精确NT134354965661323584，CIM读回134354965661323580存在4个100ns刻度舍入，首次严格跨API比较被安全拒绝，尚未发请求。修正跨API比较允许不足10个刻度，后续每次NT采样仍要求精确creation身份相同。UI2332与MemReduct16068原创建身份不变，未切前台、未调整MemReduct；Q4/continuation/client50ms/server35ms/recovery2500ms不变，无diagnostic PTH。
- 无profile/hook实际公共36请求首发35/36、最终36/36、运输失败0。de三次全成功，10.101–15.382ms；shi三次全成功，1.580–5.286ms。hywt首次9.649ms pending、第4次重试451.8751ms恢复，后台条件评分仍是残余等待路径，不把该次恢复当普遍硬上限。整批硬缺页+33不可逐请求归因。证据 C:\Users\zhaoy\AppData\Local\Temp\nw-deploy-context-20261002\public-root-startup-stage-tests.xml、public-root-startup-installed-state.json、public-root-startup-normal-outcome.json及normal-first-requests/summary。
- 用户在最新部署后明确确认原编辑器候选正常显示且数字选字正常，补齐本版本真人TSF验收；不要求目标整句命中。仍未完成：短简拼后台条件评分首次等待、LRU淘汰后冷根构造可续算边界、自然MemReduct回收及隔夜鲁棒性。保留直接Windows取文与公共热页白名单TODO，不将当前正常交互或35/36首发标为总目标根治。


### 2026-10-03 — 任意冷根可续算、预算回归及后续页取消（已部署；长尾仍在）

- 用户否定固定de/shi预热，要求任意冷输入；已移除该两项特例，保留原单字母预热。静态根plan生成器可在绝对deadline后由单后台线程继续，准备队列上限4/plan缓存256，保留取消、身份及Thread.start失败回滚。187公开输入374组112886plan记录排序字段等价；独立review没有增量部署阻断，但不证明checkpoint之间硬时延。
- 初版额外5ms前台切片使真实38冷输入首发仅9/38、最终38/38，最慢1072.4193ms；新增10ms可完成首屏RED后去除该额外切片，恢复既有server35ms绝对预算，仍保留超时游标。准确stage651 tests全部通过，备份 C:\Users\zhaoy\AppData\Local\NeuralWeasel\Experimental\backups\candidate-search-20261003-112117；此版正常38首发31/38、最终38/38、最慢712.9239ms，并非根治。
- Sanitized GIL采样发现旧后续页词法扫描在被新composition替代后未读自身取消event。真实superseded取消路径旧首屏用例通过/旧后续页RED（本应停在1次advance，实际35次）；仅让词法fallback同时检查首屏与后续页两个event，不裁路径或换排序。27相关回归通过，准确stage653 tests、0fail/error/skip、26.471s；stage格式规范化前后AST相等，Ruff通过。备份 C:\Users\zhaoy\AppData\Local\NeuralWeasel\Experimental\backups\candidate-search-20261003-113212；v3 SHA22347b7509bec17ff8dbdacb7cd2194deda32428af9d5c93a008064dbc7e33d1；9安装/stage/manifest一致pending0。
- 普通pipe worker35612/creation134355007441901913，真实当前repo原生客户端50ms，重试100ms至2500ms。UI2332与MemReduct16068原创建身份不变；Q4/continuation/server35ms未改，无诊断PTH。真实editor正文/candidate IDs/locals均未落盘；所有测试输入来自公开词表，epoch0。
- 用户要求自行随机扩大覆盖：随机seed3484890427279954185，50单音节+75有边界多音节+75简拼，共200；首发192/200、最终200/200 Han页，首次p50 13.314ms/p95 32.029ms、最慢重试恢复484.7522ms；3次首发运输失败、5次含重试运输失败。随机seed13440911170861831599，150无分隔连续拼音+25词的242逐字prefix，共392；首发376/392、最终392/392 Han页，首次p50 14.8878ms/p95 33.9851ms、最慢352.7671ms，5次首发/7次全部运输失败。二者均无profile，worker身份始终相同；后者prefix等待当前请求恢复后再递增，故另做不等待旧prefix恢复的快速序列测试。
- 以上592最终页全部finite模型分数且score_source=baseline，证明模型基础分参与，不证明真实编辑器context评分或首字最优。完整日志只status/count/timing/public labels，位于 C:\Users\zhaoy\AppData\Local\Temp\nw-deploy-context-20261002\random-public-native-summary.json 与 C:\Users\zhaoy\AppData\Local\Temp\nw-deploy-context-20261002\random-typing-native-summary.json 及各requests.jsonl；随机工具和native源码均在同scratch目录。
- 未完成：运输长尾的同请求因果、零桶首屏提前冻结后高分同桶候选不可替换的质量窗口、真实context参与的最新证据、自然MemReduct回收/隔夜鲁棒性。不能删除现零桶freezability而重引完整候选被浅frontier隐藏的活性故障；不把随机最终成功当硬50ms或TSF验收。

- 后续无profile快速逐字序列：随机seed2842196753602608863，40公开连续拼音序列、346个中间prefix，仅一次50ms原生尝试后立即换下一revision，不给旧prefix重试；290/346首发成功，32/346运输timeout，24个其余pending；40个最终composition首发37/40、最终40/40 Han+finite baseline页，最慢最终重试226.521ms。证据 C:\Users\zhaoy\AppData\Local\Temp\nw-deploy-context-20261002\random-fast-typing-summary.json 与requests.jsonl；不像前两个cohort逐步等待，明确仍有快速输入压力下超时。
- Sanitized py-spy独立随机压力复验seed11012618442008853135：40序列360prefix，315首发成功、16运输timeout；最终composition首发38/40、最终40/40，最慢514.7386ms。182个sanitized样本只保存帧函数/文件/行、active/GIL与时间；GIL样本涉及ordered_suffixes、pinyin_partial、root_candidates、cooperative_sorted等，采样时间窗口不能当同请求精确因果，不能把instrumented结果混入无profile比例。证据 C:\Users\zhaoy\AppData\Local\Temp\nw-deploy-context-20261002\random-fast-typing-sampled-summary.json、stacks.jsonl及sampled-overlap.json；生产未插入任何hook/PTH或调整MemReduct。
- 栈与运输timeout相交窗口中包含前台query_page→_freeze_next_page→_lexical_completion_fallback→ordered_suffixes/matcher，不仅是后台线程；但dump窗口大于单请求时长，缺乏精确采样点，仍只能定位排查方向。下一步验证前台词法checkpoint间耗时及state_lock等待，不能据此直接减少路径或宣称已找到所有尾部根因。


### 2026-10-03 锁预算、基础 Latin 复用、底层匹配缓存限容及 GC 诊断

- 前台状态锁和响应 readiness 现在均使用既有绝对请求 deadline；真实争用 RED 后修复，准确 stage 664 passed（含 12 Windows native）。备份 `C:\Users\zhaoy\AppData\Local\NeuralWeasel\Experimental\backups\candidate-search-20261003-120506`。同 346-prefix 样本从 290 首发/32 运输超时改善为 340/3；另 335-prefix 随机样本 316/10，尚有残余。
- 基础 Latin 根只复用公开词表中 immutable _SearchPath，context 绕过，重新安装 baseline 清空；674 stage tests 和 645 组完整字段顺序等价通过。隔离 212 roots 新分配 214204→0。备份 `C:\Users\zhaoy\AppData\Local\NeuralWeasel\Experimental\backups\candidate-search-20261003-122646`。实机 346-prefix 为 338/4，同近期 335-prefix 为 306/15，没有稳定时延改善，不能宣称根治。
- 三种 neural 匹配缓存新增每缓存 256 keys/65536 records LRU 界；oversized 结果完整返回但不缓存。1902 比较、706929 记录完整等价；最终 stage 682 passed（含 native），仅 pinyin_partial.py 部署。备份 `C:\Users\zhaoy\AppData\Local\NeuralWeasel\Experimental\backups\candidate-search-20261003-134543`。安装/stage/manifest 九项一致，listener 32200/134355087548815577。新 160 组 1342-prefix 长压测 1246 首发、36 运输超时、60 pending；最终 160 全恢复，最慢 169.3593 ms，仍未根治。
- 公开隔离 1278-root 试验：旧底层匹配缓存 1282 keys/719695 records，gen2 最慢 257.233 ms；限容后 145 keys/65351 records，gen2 仍 101.2174 ms。试验每次 drain 静态根，未跳过已有 root plan，非生产请求因果。
- 实读 Mem Reduct 当前配置：AutoreductEnable=true、AutoreductIntervalEnable=false、AutoreductValue=95、ReductMask2=231；保留用户要求，未修改。UI 2332、MemReduct 16068 的创建身份均保持。
- 当前正在准确 stage 验证三文件数值 GC 诊断：只收集各 generation 完成次数、开始/结束 perf_counter_ns、耗时/最大耗时；不读取对象图/正文/IDs/locals，不改变自动 GC。原生探针新增 QPC 起止时钟以关联实际 50 ms 请求。尚未部署该诊断，不能报告已有实机 GC 因果证据。
- 未完成：剩余快速输入超时的同请求因果和修复、零桶首屏冻结质量窗口、真实最新 editor context 的评分参与、自然 MemReduct/隔夜验收。原生公共 epoch-0 请求只有 baseline 有限评分证明，不能代替 TSF/editor 可见验收。

### 2026-10-03 数值 GC 关联、根记录容量与原有启动计划复用

- 三文件数值 GC 诊断已部署，备份 `C:\Users\zhaoy\AppData\Local\NeuralWeasel\Experimental\backups\candidate-search-20261003-140841`。初全量 685 tests 中 684 pass、1 次 native lost_ack-False 失败（`single_update_published=0 epoch=0`、returncode 21），两套 Windows native 随后 12 pass；不把初全量记为全绿。游戏期间守护按 fullscreen 暂停，退出后自然恢复，未绕过守护或触动前台。
- 实际 worker 37764/134355104661165514 的 QPC 请求时钟与 Python perf_counter_ns 通过跨进程区间断言；160 组、1342 prefixes 的关联试验中 27 次运输失败有 21 次与已捕获 GC 区间相交，1 次整个请求等待区间位于 GC 内，gen2 最大 165.6253 ms。最终 160 全部首发 Han+finite baseline。此试验每失败/每词查询数值诊断，不与无诊断普通 benchmark 的比例混算。证据 `C:\Users\zhaoy\AppData\Local\Temp\nw-deploy-context-20261002\gc-correlated-fast-summary.json`、requests.jsonl、collections.json。
- 修复根缓存只按 keys 限制、首屏缓存继续保活已淘汰根计划的缺口。normal/exact/background 根计划、首屏 deferred plans 按 16384 records 整计划淘汰；保留单个 oversized 完整计划供重试；三种 matcher 记录预算同步从 65536 降至 16384。RED 包含首屏仍持有 4 条记录而预算 3；未裁合法结果。根计划 1128 比较/365107 records、matcher 1902 比较/706929 records、首屏 1128 比较/7896 candidates/656970 plan records，字段与顺序全部等价。准确 stage 692 tests 全通过，含 12 native。
- 三文件部署备份 `C:\Users\zhaoy\AppData\Local\NeuralWeasel\Experimental\backups\candidate-search-20261003-144801`，11 项 installed/stage/manifest 一致。无 profile 同旧 seed3394145303449908439 重放 1342 prefixes：1166 首发、12 运输失败、164 pending；160 最终完整输入均首发，最大 2.1841 ms。旧普通样本为 1246 首发、36 运输失败、60 pending，故不能只报运输失败下降：中间 prefix 首发率反而下降。新 seed1038487010221707703 的 1298 prefixes 为 1082 首发、43 运输失败，最终159首发/160恢复，最大162.3102 ms。分别记录 root-record-fast-matched/random 输出，未称根治。
- 同 1342 prefixes 的 root-record GC 关联复验：37 运输失败均与已捕获 GC 区间相交（完整等待区间在 GC 内为 0），gen2 累计96次、max143.3003 ms、last70.1665 ms；最终160首发。容量界仍未消除暂停。证据 `C:\Users\zhaoy\AppData\Local\Temp\nw-deploy-context-20261002\root-record-gc-correlated-fast-summary.json`。
- 新 RED 证明原有26字母启动 pass 的计划被 dynamic LRU 淘汰后，首字母又调用 `_iter_root_han_plan`。修复仅保留原 pass 已构造的公开、无评分计划，最多26 keys，后续普通输入不填入；失败启动 finally 撤销 retention 标记。cold preparation 识别已完成启动计划，真实 context 仍调用当前模型评分；exact 查询继续独立。未增加 de/shi 或其它词的预热扫描。694 准确 stage tests 全通过，含 native；1128 完整字段/排序等价，公开索引对应26 plans/53186 records，正常动态首屏仍有界16327 records。
- 两文件启动计划复用已部署，备份 `C:\Users\zhaoy\AppData\Local\NeuralWeasel\Experimental\backups\candidate-search-20261003-151643`，11 项一致，worker37592/134355142129648605。首轮1342重放1286首发/4运输失败/52pending，最终160首发、max2.1105 ms；该轮与低优先级隔离公开等价比较有并行时间，不作为独立正常基准，后续单独实机复验另记。UI2332/MemReduct16068、Q4/continuation/50ms/35ms/2500ms不变，真实正文/候选IDs/locals没有落盘。
- 待完成：启动计划复用的独立随机/GC关联实机复验及其残余；首屏零桶冻结质量窗口、真人最新context评分与TSF、自然隔夜/MemReduct验收依然未完成。

### 2026-10-04 重启随机复现、词法递归计数对象释放

- 补记前日无并行比较的独立重放：seed1038487010221707703，160组/1298 prefixes，1238首发、8运输失败、52 pending；160完整输入全部首发 Han+finite baseline，最大10.0581ms。另带诊断的1342-prefix关联重放1279首发、15运输失败，其中14与捕获GC区间相交，完整等待窗口在GC内为0；160完整输入全部首发，最大8.514ms，gen2最大65.6075ms。相交不证明每次完整因果，关联试验比例不与普通基准混算。证据：C:\Users\zhaoy\AppData\Local\Temp\nw-deploy-context-20261002\startup-retention-fast-matched-recent-summary.json、startup-retention-gc-correlated-fast-summary.json。
- 本机于2026-10-04 10:37:50.5+01重启。随后新独立160组排除旧完整输入，seed2119500101115627053，worker7404/134355803185492439；1372 prefixes首1248、运输失败44、pending80；完整输入156首发/160最终Han+finite baseline，最大178.1471ms。此结果仍否定已根治，也不证明最新editor context参与。证据：C:\Users\zhaoy\AppData\Local\Temp\nw-deploy-context-20261002\startup-retention-fast-independent-20261004-summary.json。UI12392和MemReduct34676是重启后的新进程，并非此前旧PID。
- 只读核验后，历史stage缺失的search_batches.py和neural_candidates.py按安装字节恢复，SHA与历史manifest一致；没有重跑prepare或覆盖旧reference。11项安装仍匹配，记录：C:\Users\zhaoy\AppData\Local\Temp\nw-deploy-context-20261002\post-reboot-installed-stage-audit-20261004.json。
- 公开隔离root-only试验（无模型/生产管道/editor）：同1372 prefixes后15880 PartialPinyinMatch和15880 _RootHanPlanEntry，普通根缓存24keys/15880records；全量GC释放0，耗时22.8049ms，1391次捕获GC最大22.7894ms。只在此隔离进程读取公开对象类型计数，没有读取生产对象图/locals；不足以解释生产65ms，不关闭自动GC或凭猜测降低覆盖。
- 新3项RED分别覆盖词法计数正常结束、2ms超时、异常退出，旧代码均报 `AssertionError: finished path checks must not retain the manager until GC`。根因是`_lexical_tail_may_fill_page.count_from`自递归闭包循环保留memo及manager。仅在finally执行`count_from = None`打断循环；排序、候选覆盖、2ms预算及生产GC策略不变，3项GREEN。源文件：src/neural_weasel/neural_candidate_pages_v3.py；测试：tests/test_lexical_path_count_cleanup.py。
- 新独立stage以安装完整source为基准，只更改v3目标方法，剔除该方法后AST相等，保留其它dirty及安装优化。685通过/12跳过（44.72s）；旧native探针缺失导致一次12项启动失败（FileNotFoundError/WinError2），已重新MSVC编译独立探针并补跑12项Windows真实测试管道恢复全部通过、无skip（XML2.557s）。不能把初次跳过或启动失败记为已通过。两个目标文件Ruff format/check及diff检查通过。证据：C:\Users\zhaoy\AppData\Local\Temp\nw-deploy-context-20261002\lexical-count-cleanup-stage-tests-20261004.xml、lexical-count-cleanup-native-tests-final-20261004.xml。
- 单文件补丁已带备份部署：C:\Users\zhaoy\AppData\Local\NeuralWeasel\Experimental\backups\candidate-search-20261004-101000。v3 SHA c5b5936ad089fb064ce19f540464e1d3829bae9043f310b4d6e3eff6b0069b39；11项installed/stage/manifest一致，pending=[]。独立stage：C:\Users\zhaoy\AppData\Local\Temp\nw-deploy-context-20261002\lexical-count-cleanup-stage-20261004。生产守护仍suspended-fullscreen/pid=null，UI12392、MemReduct34676创建身份不变，没有强启GPU模型或操作前台；尚无补丁后的实机请求/GC改善证明。
- 单initial原有策略按token ID的32个一桶稳定频次排名，static_rank先于model_score；模型分只在桶内排序。多字母无此单initial桶。本轮未改此策略；epoch0基准不证明真实context评分，非零epoch未就绪锁定baseline也是现有契约。质量窗口仍需独立证据，不能为抹除卡顿任意改变排序。
- 待完成：退出全屏保护后自然恢复时，对新worker精确创建身份核验，并重放1372-prefix独立样本及数值GC关联；补齐首屏质量/最新context评分和自然MemReduct/隔夜验收。当前补丁只证明消除一个循环保留缺口，不能宣称解释或消除全部运输长尾。
- 进一步公开隔离比较：160个随机完整输入加405个公开音节，共565项计数结果完全一致；但旧/新均0个遗留递归函数、manager均能立即释放，说明这些输入未进入计数分支。前两次比较工具错误地期待旧版必留循环而断言失败，已保留此限制；此565等价结果不支持该补丁解释1372-prefix样本的44次运输超时。对象释放修复仅有3项强制小根fixture的RED/GREEN证明，不把它升级为实机残余卡顿根因。证据：C:\Users\zhaoy\AppData\Local\Temp\nw-deploy-context-20261002\public-lexical-count-cleanup-equivalence-20261004.json。
- 补丁后原生诊断仍server_pid=0、Win32 error2（生产管道不存在），守护树只有wscript11940→powershell35420且仍报告suspended-fullscreen；没有Python模型worker。已准备但未执行同seed/词池1372-prefix复验工具：C:\Users\zhaoy\AppData\Local\Temp\nw-deploy-context-20261002\run-lexical-count-cleanup-fast-matched-20261004.py，独立输出拒绝覆盖旧证据，每个非零serverpid均核验精确创建时间。不安排后台强启模型或自动化任务；恢复后先检查安装SHA和新worker，再运行该工具。


### 2026-10-04 后台原生分阶段观测、GC实验回滚与运行状态漂移

- 用户要求真实后台测试、逐阶段定位，并严格不动前台。所有本段请求均为公开测试词池、epoch0、持久原生管道；不读取真实编辑器正文、候选正文/IDs、locals或生产对象图。没有激活共享TIP、导航窗口或启动GUI验收。Q4/continuation/client50ms/server35ms/recovery2500ms不变，UI12392及MemReduct34676的创建身份保持；不把管道成功当TSF/context证明。
- 原生探针将耗时拆为connect/send/header/body，连接、发送和响应体读取通常很短，响应头等待占主要时间。临时服务观察仅写数字阶段、revision、线程号和时钟；一次错误staticmethod包装的观测轮次作废，修正后才使用可核对的公开数据。全部临时observer .py/.pth已撤销，普通安装PTH保留。无需重新注入即可分析历史二进制数字事件。
- 公开全页隔离复现曾保留约47996匹配/32253根记录；排除已验证不可成环的公开叶记录能降低GC扫描，但这不能代替真实响应验收。未关闭生产自动GC、冻结PRIVATE或裁掉合法搜索结果。
- 第一版逐构造验证匹配/根记录，实机中位响应回退，已回滚。第二版仅在完成的matcher缓存发布前验证匹配记录、每32条检查取消/期限，根仍逐构造验证：`normal-cached-atomic-public-20261004-summary.json` fast1372=1281首发/4运输/87pending/header中位17.614ms；paced1372=1312/0/60/15.5452ms；gen2最大14.6405ms。该版也回滚。此目录下所有短证据文件名均相对于 C:\Users\zhaoy\AppData\Local\Temp\nw-deploy-context-20261002。
- 第三版根计划复用缓存匹配的原生字段验证证明，保留规范构造器、11字段、精确类型和两个计算字段原生int守卫；708准确stage tests通过，另11安全用例通过，1128计划/365107条记录字段及顺序相同。XML为 C:\Users\zhaoy\AppData\Local\Temp\nw-deploy-context-20261002\validated-root-record-stage-tests-final-20261004.xml。已部署3文件后继续真实验收，不能把stage通过当产品收益。
- 第三版最早paced测试跨full-screen暂停，数据作废；旧测试允许serverpid=0继续，491运输失败不能逐次归因于暂停。新守护测试逐请求前后核验running/supervisor身份，100ms监测worker退出，对非零native PID核验精确NT创建时间，暂停/换代立即停止。`normal-validated-root-guarded-retry-20261004-summary.json` worker17520/134356019817454985，fast1372=1283首发/15运输/74pending/header15.61045ms，160完整输入首发；paced1287/10/75/header17.2624ms，但末27秒与离线分析重叠，不用作独立paced基准。旧正常control fast1308/7/57/header14.10855ms、paced1304/12/56/header14.21025ms。
- 第三版未显示可靠净收益，生产已通过 C:\Users\zhaoy\AppData\Local\NeuralWeasel\Experimental\backups\candidate-search-20261004-152241\rollback-candidate-search.ps1 -Apply 回滚3文件及原manifest。只停模型任务自有、经创建身份/路径复核的进程树，UI/MemReduct未停。保留之前通用搜索及词法计数闭包释放修复。生产v3 SHA c5b5936ad089fb064ce19f540464e1d3829bae9043f310b4d6e3eff6b0069b39；pinyin SHA b6d51c928920d49d30ed7d2ae427daf398f31c3fd4360279c6c4d8378c7333a0；immutable_record_gc.py已从安装删除。Repo中的GC实现/测试保留为实验，不能据此推断生产版本。没有覆盖其它dirty。
- 无并行计算的回滚实机对照：`normal-rollback-guarded-20261004-summary.json` worker29676/134356030027572230、supervisor16380，completed=true/guard_events=[]。fast1372=1325首发/3运输/44pending/header中位13.19735/p95 33.0747ms；paced1372=1300/15/57/header15.00855/p95 35.5979ms；两组160最终均Han+finite baseline，fast0次完整输入重试、paced2组重试。gen2最大111.2147ms，仍未根治。
- 回滚及最终复核工具为 C:\Users\zhaoy\AppData\Local\Temp\nw-deploy-context-20261002\audit-rollback-installed-20261004.py；新增`--output`只允许本目录单个JSON文件且拒绝覆盖。最终输出 C:\Users\zhaoy\AppData\Local\Temp\nw-deploy-context-20261002\rollback-installed-state-after-counter-check-20261004.json：11项安装/manifest匹配、原manifest完全恢复、3个目标恢复/删除正确、temporary_observers=[]。第一次重复默认输出因文件已存在而拒写，未覆盖旧证据。
- 数字旧trace6612中rev2722客户端56.4913ms超时、handler119.4031ms才进入，等待前无GC；前一响应write结束后到下一read开始有约160ms缺观测。该旧trace没有after_response/Thread.start钩子，不能据此排除派发，也不能判为系统调度。Windows CPU时间粒度约15.625ms，`_read_message`含空闲阻塞，不当作处理CPU。只记录元数据重叠，不宣称排他因果。
- 带dispatch的旧trace22944无需重新注入：C:\Users\zhaoy\AppData\Local\Temp\nw-deploy-context-20261002\public-service-phases-20261004-22944.bin及observed-service-dispatch-public-20261004-requests.jsonl。14运输失败中12个handler在0.21ms内进入；11次客户端实际等待窗口与GC相交24.6982–52.6157ms，多在cold preparation、lexical fallback/freezing内。rev710另有前一revision词法后台及响应后Thread.start重叠；rev2591 handler118.0766ms才进入，客户端61.7277ms窗口GC仅14.5375ms，而直到handler才进入的更长窗口GC70.8864ms，不能把窗口外GC算进客户端超时。rev2633 root_candidates47.1747ms、GC仅0.3925ms；score_root0.1377、Han top-k0.4538、deferred frontier0.3224ms，剩余未细分，不能直接归因AI推理或锁等待。state-lock获取多为0.01–0.03ms，code31/32包含锁内处理或异常，不等于获取锁等待。结果为public-handler-wait-analysis-20261004.json、public-handler-extra-components-20261004.json、prehandler-wait-interval-analysis-observed-service-dispatch-public-20261004.json；嵌套区间不可相加。

### 2026-10-04 同worker优先级对照及缺页/CPU数字采样

- 模型任务和UI任务均Priority7，实际模型worker BelowNormal；源码/安装启动脚本未找到显式PriorityClass设置。临时只给已核对身份的worker设Normal，测试结束恢复BelowNormal，不改任务配置。`normal-worker-priority-guarded-20261004-summary.json`同worker29676：fast1372=1314首发/23运输/35pending/header12.30035ms，paced1306/50/16/header9.54145ms；两组160最终成功，重试3/5组。priority_restored=true，随后进程属性读回BelowNormal。
- 恢复优先级后的同worker A-B-A复验：`returned-worker-priority-guarded-20261004-summary.json` fast1372=1273/24/75/header16.0441ms，paced1258/47/67/header17.9469ms，160最终均成功。A返回仍比最初A差，说明运行状态/主机负载随时间漂移；不能把B的中位改善或运输恶化作优先级因果。未部署优先级调整。守护脚本增加OpenProcess后GetProcessTimes与native精确创建时间再核验，防止PID复用；恢复同时读回GetPriorityClass。
- 只读 C:\Program Files\Mem Reduct\memreduct.ini 当前AutoreductEnable=true、AutoreductIntervalEnable=false、AutoreductValue=95、ReductMask2=231、StatisticLastReduct=1791129421。这是保存配置，不能当作每次清理实时事件或沿用旧80%/10min说法。未停/修改/触发MemReduct。
- `run-guarded-public-phases-20261004.py --sample-worker-memory`在每个真实请求前后仅调用GetProcessMemoryInfo/GetProcessTimes/GlobalMemoryStatusEx/GetSystemTimes，保存数值。无生产对象遍历、观察器注入或正文采样。`numeric-worker-memory-guarded-20261004-summary.json`同worker/同创建身份，completed=true/guard_events=[]；fast1372=1253首发/37运输/82pending，paced1256/49/67。计数覆盖整个worker的并发工作，不是单请求排他成本，缺页合并软/硬缺页，CPU时间仍有量化误差。两侧采样开销中位263.3us，不能当作无采样性能基准。
- `numeric-worker-memory-guarded-20261004-counter-analysis.json`：fast37次运输失败中10次缺页计数零增量，paced49次中34次零增量；运输组CPU时间增量中位均62.5ms。fast成功组耗时中位15.3585ms/缺页中位7/CPU15.625ms，paced成功组16.9086ms/缺页7/CPU15.625ms。这否定将全部卡顿解释为内存回填，但不证明每次耗时都是GC。采样内存负载77–83%，WS17,166,336–297,414,656 bytes；不是硬缺页/自然MemReduct验收。gen2 lifetime max从137.9115增至168.5703ms。
- 仍开放：冷搜索/连续输入长尾；没有GC的root_candidates未细分时间与处理前等待；首字排序及真实context模型评分、最新TSF/显示/提交/分页验收；自然MemReduct与隔夜恢复。旧单initial的频率32桶优先、模型桶内排序未改。不得把epoch0 finite baseline评分称作真实context评分。下一步应优先量化未观测root-Latin materialization/cache eviction和公共不可成环记录优化本身的成本，不重复部署已无净收益的GC版本或无证据改优先级。真实UI验收需要安全隔离环境或用户操作，当前没有可安全自动激活的独立生产TIP环境。


### 2026-10-04 线程周期细分与公开原生完整复验

- 临时数值观察增加QueryThreadCycleTime，x64使用PyDLL保持GIL，避免观察API自己释放GIL污染服务调度。静态/类方法描述符、返回值与原异常保持；环形缓冲非阻塞锁，记录仅8列整数，不记录参数、返回值、正文、候选IDs或locals。新增root-Latin、root-Han、baseline-page缓存发布、root-plan缓存发布阶段。CPU cycles只是计数，不转换成毫秒；阶段包含子调用，不能相加作排他CPU成本。
- 测试控制工具遇到两项工具侧故障：重启后旧model-service.json仍显示旧父进程，首轮在候选请求前安全中止；Windows状态文件写入瞬间PermissionError也导致另一轮中止，均未计入时延证据。子PowerShell的Get-FileHash未自动加载，撤销尝试失败；随后直接撤销成功，并改用.NET SHA256消除该依赖、捕获控制脚本错误、对状态共享冲突/暂时JSON不完整有界重试。此类错误不是IME卡顿根因。再一轮触发全屏保护，未发候选；最终轮等待自然恢复，不强启GPU。所有临时.py/.pth最终撤销。
- 有效轮证据目录 C:\Users\zhaoy\AppData\Local\Temp\nw-deploy-context-20261002；cycle-root-v3-20261004-summary.json、cycle-root-v3-20261004-requests.jsonl、cycle-root-v3-20261004-lifecycle.json。worker28852/精确NT创建134356080729515277，父状态pid14680，completed=true/guard_events=[]，lifecycle.test_code=0/removed=true。fast1372=1314首发/7运输/51pending，160完整输入均首发Han+finite baseline，0组重试。响应头中位13.89265/p95 34.4257/max59.635ms，connect/send/body中位0.0004/0.0162/0.01045ms。属于带观察的公开epoch0样本，不作无观察性能基准、TSF或真实context证明。
- public-cycle-phases-20261004-28852.bin含81060条8列事件，序号/时序完整，dropped=0；cycle-root-v3-20261004-interval-analysis.json保存数值分段。模型根评分中位0.0589ms/max1.5183ms；root-Latin中位0.15385ms，baseline-page缓存发布中位0.1426/max0.988ms。本轮没有复现无GC的47ms root案例。rev556 root-Latin43.989ms，其中同线程gen2 GC40.837ms；rev893词法fallback70.9371ms，同线程gen2 GC48.6452ms，加若干gen0/gen1。不能将整个Latin/词法阶段称为模型推理。
- 七次运输失败的实际客户端窗口均与GC相交15.8867–51.3975ms；六次handler在0.1ms内进入，另rev893为8.6015ms。该相关性不证明全部历史故障都由GC解释，窗口外GC不能计入客户端窗口。响应后Thread.start最大27.803ms、after_response最大18.7677ms，尚无这些dispatch阶段的周期计数，不能声称排他CPU因果。旧无GC处理前等待及root延迟仍开放。没有重新部署已无净收益的GC实验，也没有改排序、deadline、量化或优先级。
- 最终安装核验保存于 C:\Users\zhaoy\AppData\Local\Temp\nw-deploy-context-20261002\cycle-profile-final-installed-state-20261004.json：checked=11/all_match=true/restored_original_manifest=true/temporary_observers=[]；UI12392/MemReduct34676创建身份不变。撤销后一次原生diagnostics运输超时63.8749ms/Win32 121，后一次成功3.5923ms，server31104/精确NT134356081248755566，保存cycle-profile-post-cleanup-health-20261004.json及cycle-profile-post-cleanup-health-retry-20261004.json；只证明恢复后的实际路由成功，仍有间歇长尾，不能称运行持续正常。没有操控窗口或激活生产TIP。静态确认production的context更新会使共享engine候选session失效，因此不得在前台工作期间借独立测试session往该服务注入合成context来宣称安全隔离；真实context/首字质量与隔离TSF/UI、自然MemReduct/隔夜仍需后续证据。

### 2026-10-04 公开索引校验证明复用：隔离收益门、711 回归及两轮生产复验

- 本阶段延续“先隔离证明净收益、有益才部署”要求。新 matcher 初始化只为 exact canonical IndexedPronunciation 的完整九字段原生 atomic/flat-tuple 内容建立 numeric id proof；entries/root 强持有阻止 id 复用。每个 exact canonical PartialPinyinMatch 的其余四字段仍逐次检查，未知、mutable、subclass 走完整校验或正常 GC。根记录沿用前版完整 proof。只排除已验证不可变、无环的公开搜索记录，不禁用自动 GC、不 freeze PRIVATE、不改变排序或模型评分。
- 准确 stage 为 C:\Users\zhaoy\AppData\Local\Temp\nw-deploy-context-20261002\index-proof-stage-20261004；非 GC matcher AST 与旧参考一致，九个非目标安装优化逐字节保留。公开真实索引 564 queries/1128 comparisons/365107 records 的完整字段和顺序相等。证据 index-proof-plan-equivalence-20261004.json。精确 stage imports 已断言；全量 711 用例中 699 pass/12 native skip，另用本阶段新 MSVC 编译的 index-proof-recovery-probe-20261004.exe 补跑 12 项 Windows 原生恢复/故障矩阵，全部 pass/0 skip，合计 711 unique 用例通过。XML 为 index-proof-stage-tests-20261004.xml 和 index-proof-native-tests-20261004.xml。新三源文件与安全测试 Ruff 通过。
- 六个全新独立 Windows 生产协议/搜索 server 按 original,old,new,new,old,original 交错；真实原生 SID/creation client，50ms，公开索引/synthetic baseline，无 GPU/editor。共 8232 prefixes/960 final。新方案两轮运输失败 7、原版两轮 22，p99 新约 39ms/原约 50ms，gen2 max 新 8.6678–12.7988ms/原 56.713–61.9722ms；中位耗时新约 13.93–14.02ms/原 12.81–13.52ms，存在约 0.8ms 代价，不能称全面提速。960 final 全 Han+finite baseline，自动 GC 保持。证据 index-proof-isolated-comparison-20261004.json。
- 部署前无临时 observer/profile 的真实 Q4 模型基线：worker31104/精确NT134356081248755566，supervisor17148，BelowNormal 不改；fast1372=1269首发/13运输/90pending，header中位15.9652/p95 35.5651ms；paced40ms1372=1265/43/64，header中位16.7805/p95 41.2575ms。两组160 final 全恢复 Han+finite baseline，3/4组重试，首个完整请求至恢复 max178.6206/238.8572ms；gen2 max107.7183ms。证据 index-proof-current-baseline-20261004-summary.json 与 requests.jsonl；未与本聊天的离线分析/测试计算并行。
- deploy-index-proof-model-only-20261004.ps1 先只读预检，后 -Apply，只替换 pinyin_partial.py/neural_candidate_pages_v3.py/immutable_record_gc.py，经完整 modeltask launcher 树身份复核停启，UI/MemReduct 不动。备份 C:\Users\zhaoy\AppData\Local\NeuralWeasel\Experimental\backups\candidate-search-20261004-181323，内含原 manifest/SHA、三目标备份/存在性、专用 rollback-candidate-search.ps1。安装 audit 12项 stage/installed/manifest 一致，无 observer/PTH。新 SHA：pinyin d4f1ff1f852c7003327d9b157a113c0c2bc506ab7b20a870b8c0c581bccb9bd2；v3 31fa24c37615382b286e765382a0f7281aa78ce238f92a652ce82c3039b0031c；immutable 4e5c85f3596a497e89e4ac77ae82228d179aa370c7461864d7d0dcf2be8d6bb6。
- 新普通生产 worker35844/精确NT134356112134774972，supervisor13404，两轮同一创建身份、无优先级变更/无观察器。首轮 fast1372=1304首发/5运输/63pending，header中位16.0473ms；paced1372=1279/12/81，中位18.33765ms。再次不重启复测 fast1372=1283/4/85，中位16.7166ms；paced1372=1289/17/66，中位16.5692ms。每轮320 final 均 Han+finite baseline，首轮1/0组重试、repeat1/1组重试；完整请求至恢复 max 首轮173.5936/52.6823ms、repeat167.368/187.2813ms。相同2744 prefix口径，运输失败56→17→21、首发2534→2583→2572，保留该增量，因为隔离和连续实机复验均显示长尾/首发收益；中位并非一致改善、仍有 pending/超时。新 worker gen2 lifetime max 首轮46.1903ms、第二轮60.9151ms（第二轮新增52次）；仍超过50ms，不能声称消除GC长尾。
- 生产证据同 scratch 根：index-proof-deployed-normal-20261004-summary.json、index-proof-deployed-repeat-20261004-summary.json及各 requests.jsonl；index-proof-live-comparison-20261004.json复核完整请求恢复时钟为(final.query_end_ns-first.query_start_ns)/1e6，不使用排除了首请求的旧 completion_recovery_ms 字段。UI12392 创建2026-10-04 10:38:10.448854+01、MemReduct34676 创建10:48:56.429083+01 保持。Q4/continuation/client50ms/server35ms/recovery2500ms 保持；真实context正文/candidate IDs/locals/生产对象图未读取或持久化，其它 dirty 保留。
- 未完成：残余运输超时/GC与dispatch长尾的进一步因果和修复、零桶首屏质量窗口、真实最新context评分及 TSF/editor 可见验收、自然 MemReduct/隔夜恢复。上述公开 epoch0 管道证据只证明 baseline 有限评分及请求恢复，不证明真人首字质量或隔夜鲁棒性。勿重跑旧已回滚 candidate-search-20261004-152241 的回滚；本阶段如有明确回退需要，使用专属 candidate-search-20261004-181323 备份并先核验安装是否有后续改动。

## 2026-10-04 性能归因补测：CPU/线程/GPU 与逐阶段边界

- 用户要求按真实耗时解释 CPU、GPU、缓存、线程及已做优化，功能架构说明不足。本轮不改生产服务/模型/优先级，不重启，不切前台；公开 epoch0 请求，PRIVATE/context/候选正文/ID/locals/生产对象图均未读取或持久化。外部 NtQuerySystemInformation 只读取内核进程/线程计数；不读取 Python 帧或对象。
- 模型仍 worker35844/精确NT134356112134774972、supervisor13404、Q4/continuation/BelowNormal。UI12392、MemReduct34676及启动时间不变。WPR 原先未记录；尝试 CPU.Light.File 被拒绝：Failed to enable the policy to profile system performance.，0xc5585011；后续仍未记录。没有系统线程调度/GIL 等待或 CUDA 内核/传输时间线。
- 两轮各1372 prefix/160 final，真实现有原生50ms/SID+creation 客户端。首轮 Toolhelp 外部采样调用墙钟中位80.57375ms；改用单次内核快照后中位8.7063ms/P9511.2656/max15.0004ms。此数包含采样线程自身等待，不等于模型被阻塞的时间；采样仍有扰动，不能视为无观察器 A/B 或净收益证明。
- 后轮28.3997082s，模型进程CPU25.15625s，相当于单核88.5793%（不是整机CPU89%）。线程累计24.75s，覆盖进程CPU98.3851%；654条已观察线程生命周期，短命线程仍可能漏采。最热TID28388用15.34375s/60.9938%，32328用3.3125s/13.1677%，36836用2.359375s/9.3789%，31104用2.109375s/8.3851%；没有函数事件绑定，不能从ID断定其职责或精确等待时间。
- 后轮1287首发/4运输/81pending，160 final全部Han+finite baseline，3组重试。连接/发送/响应正文中位0.0004/0.0127/0.0096ms，等待响应头16.26975ms/P9535.3856ms/max59.0962ms。响应头等待包含服务端处理/锁/调度，不是纯IPC传输。整卡GPU采样140点，中位/P95=0%，max17%；VRAM4676MiB稳定。整卡滚动利用率不能换算本请求GPU耗时；epoch0 baseline请求未覆盖真实context prefill与后续模型续推。
- 该轮请求前后进程缺页增量合计54099，混合软/硬缺页且可能含并发工作。四运输失败请求增量8/7/112/0，不能据此把超时归因为磁盘换页。尚无硬件L1/L2/cache-miss比率或应用缓存hit/miss比率。
- 重读旧28852 numeric-cycle v3完整事件：冷root准备中位7.0683/max64.2928ms；lexical补充4.14885/max70.9371；冻结0.53175/max71.438；读取根评分0.0589/max1.5183；thread.start0.36945/max27.803。这是GC index-proof部署前历史，各函数嵌套，不可相加。revision893的70.9371ms词法补全与71.438ms冻结为嵌套同线程9420区间，其中gen2为48.6452ms；Latin最慢43.989ms中同线程31580 gen2为40.837ms。旧另一次22944 trace请求在handler前34.2206ms，前revision词法背景区间覆盖33.649ms，属相关证据，精确GIL/调度因果未证明。cycle trace phase50无事件；0.06ms根评分是既有baseline/logits选取，不能当GPU推理速度。
- 仅缓存的最近runtime诊断：context_epoch/requested=0，无updating；单token refresh latency29.714ms、queue0.0082/compute29.7058ms；最近continuation2 branches/2完成、elapsed405.9231ms、queue0.0148ms。缺来源请求及QPC时间戳，可能来自启动/其它较早调用，不能归为本轮输入、真人context或CUDA内核时间；compute也包含eval之外的复制/capture。
- 证据全部在 C:\Users\zhaoy\AppData\Local\Temp\nw-deploy-context-20261002：performance-attribution-evidence-20261004.json（重新核验10phase/1372完整计数）、performance-attribution-counters-light-20261004.json、同tag summary.json/requests.jsonl、performance-attribution-latest-runtime-metadata-20261004.json。对应采样脚本 sample-worker-threads-20261004.py 和 sample-worker-threads-light-20261004.py 只在scratch，无观察器安装。解释缓存应分别指出公开root/matcher、logits/上下文前缀、模型序列状态；命中/失效的代价在CPU或GPU阶段中，不是额外可加总的一块时间。
- 仍未完成：精确线程/GIL等待和CUDA kernel/transfer归因、逐请求缓存miss计时、TSF按键至屏幕候选显示、真实最新context评分、自然MemReduct/隔夜恢复。总体卡顿根治不能据本轮宣称完成。


### 2026-10-04 卡顿因果复核与当前 CPU 搜索隔离诊断

- 用户要求回答卡时具体原因、并行组织和最差远超平均。本轮没有生产补丁或重启；UI12392 创建 2026-10-04T10:38:10.448854+01、MemReduct34676 创建 2026-10-04T10:48:56.429083+01、模型35844 创建 2026-10-04T19:13:33.477497+01 均复核不变。只对 scratch 内独立 pipe/server 安装 numeric observer，独立 server/client 已结束；不注入生产 context、不碰前台。
- 旧 GC 优化前完整 numeric trace 与客户端请求按 revision/QPC 复核：请求630 client54.6551ms，handler55.9685ms，cold-root55.8409ms，handler同线程gen2为52.5696ms。请求893 handler81.9122ms，lexical70.9371ms，冻结71.438ms（嵌套），全GC50.5026ms；下一请求894 handler前34.39ms+handler22.2785ms，前请求GC覆盖前等待33.3238ms。说明客户端超时不终止服务端工作，前请求影响下一请求。它是历史明确案例，不能当新部署后频率。C:\Users\zhaoy\AppData\Local\Temp\nw-deploy-context-20261002\tail-request-causality-20261004.json 保留具体请求/阶段/同线程GC/相邻重叠；inclusive阶段不可相加。
- 旧请求710 handler前36.8622ms，前请求后台lexical重叠36.3449ms，同管道线程after_response/Thread.start分别重叠15.122/15.1062ms。安装源码的 response_workers.after_response 在写响应后的finally依次worker.start，pipe_server._serve_connection随后才读下一请求；Thread.start握手确实在管道读循环关键路径，但不能将全部wall time算作创建线程CPU成本。共享CPython3.12 GIL使纯Python CPU搜索不获得多核隔离；GC、长CPU段与锁内工作可跨协作检查点。5ms slice/35ms预算不是可抢占硬上限。旧state-lock等待max0.8033ms，不是该组最大直接等待；GIL与OS ready/wait逐段份额仍无系统调度时间线。
- 当前安装CPU搜索的四个全新独立真实生产协议管道试验，normal/hold/hold/normal，每轮1372公开prefix，共5488请求。公开真实索引、seed42 synthetic baseline、空Latin constraint、stateNone，自动GC开启，无真实模型/GPU/编辑器。两个normal首发1371/1372、运输0；mean6.56254/6.59946ms，max29.3285/28.7729ms，gen2max4.6671/5.3557ms。revision709/892/893在两轮都出现26.7467–29.159ms handler，lexical19.2269–21.6382ms、cold-root5.3191–6.8081ms，handler同线程GC0.6228–1.0266ms、handler前0.0333–0.0939ms；大量同线程CPU cycles支持搜索计算本身是当前残余热点。两normal heartbeat最大25.9571/30.047ms表示其它Python线程也有延迟，不能独自区分GIL和OS。证据 C:\Users\zhaoy\AppData\Local\Temp\nw-deploy-context-20261002\parallel-tail-diagnosis-20261004.json；原始observer dropped=0，全部客户端/phase PID及计数已复核。
- hold只停止后台启动，不是公平等工作量调度A/B；它保留1166/1368未启动thread jobs，改变session清理、缓存推进、任务/对象生命周期和候选完成性。GC长尾反而变大，不能据此宣称后台无竞争、不能部署这种方案、不能直接把GC变大归因于保留对象图（需另证）。原始四轮证据 C:\Users\zhaoy\AppData\Local\Temp\nw-deploy-context-20261002\parallel-ablation-comparison-20261004.json；harness C:\Users\zhaoy\AppData\Local\Temp\nw-deploy-context-20261002\isolated-parallel-ablation-20261004.py。
- 另两个独立公开server只增加cursor next次数/root-plan长度数字计数，共2744请求。607/608个cursor的next次数中位43/43.5；上述复杂revision为584–1120次（大部分None检查点），root-plan长度800/801。这是cursor整个生命周期含后台续算，不等于首屏工作；验证计数相加和请求总数。新增计数轮mean15.8120/16.4614ms显著高于此前6.56/6.60ms，计数改变分配/调度且各轮外部负载未控制，不能拿其延迟判净收益或当生产测量。证据 C:\Users\zhaoy\AppData\Local\Temp\nw-deploy-context-20261002\lexical-work-count-findings-20261004.json；harness C:\Users\zhaoy\AppData\Local\Temp\nw-deploy-context-20261002\isolated-lexical-work-count-20261004.py。只用于补充搜索工作量不均匀的证据。
- 当前结论：明确卡点包括输入依赖的CPU词法搜索、历史同线程GC停顿，以及超时后继续工作/响应后启动握手对下个请求的牵连。缓存/分支工作量、GC阈值与跨请求排队使尾部不能由平均推导。读取现有根logits均值约0.06ms、top-k均值约0.61ms，不支持将该组卡顿归为一次GPU推理；真实最新context模型路径、CUDA kernel/transfer、TSF按键到屏幕、自然MemReduct/隔夜仍未完成逐请求证明。修复优先候选：更细CPU工作上界、管道读循环外调度且有界取消/清理、继续降低可扫描瞬时对象；需先公平隔离证明收益再部署，不直接增加线程。

### 2026-10-04 有界候选展示、首屏退休与独立取消（21:21 BST 部署）

- 用户要求每页7个、约35个总容量，避免展示根全量分配，并立即淘汰过期请求。两条 deferred-root 展示路径（prepare/prune）改为精确 top35；完整 continuation seeds 仍以32条批次恢复，不能截掉低根分但续推可能最优的路径。首屏至少7个真正 freezable 候选或已冻结时停止初始后台扩展；翻页另行推进。不是任何任意时刻强制冻结7个，不改词桶或模型评分。
- query admission 独立于状态锁；每个 session 固定 generation Event，A→B→A 不复活旧 A。旧 query/page 不重新 admit；navigation 只 touch 相同 Event 的 LRU。clear/evict/expire、provider 返回、scratch merge、cached return 和 session 创建窗口均检查取消。空闲等待每5ms检查组合取消，并 finally 注销；已进入 GPU 的调用不做硬抢占，只拒绝旧结果及后续工作。保持 Q4/continuation、client50ms/server35ms/recovery2500ms及前台。
- 新增13项 synthetic RED→GREEN，包括6/7首屏仅1/0批、展示≤35且完整 seeds、锁忙先取消、ABA/旧query/page/clear/LRU、provider/scratch过期不merge、idle无wake取消；修正首屏 fixture 固定未取消 token 后13项再次通过。旧4项回归中1项揭示 admission LRU 需要 navigation touch，已最小修复；其余3项适配刻意的首屏退休/展示 hook，低分root目标仍在 later preparation 保留。Sol xhigh只读终审无新增阻断；未跑生产或测试。
- 安装基础 stage/ref 在 C:\Users\zhaoy\AppData\Local\Temp\nw-deploy-context-20261002\bounded-search-stage-20261004 和 bounded-search-reference-20261004；59 modules仅3源码变化，保留 installed-only `_is_exact_single_syllable_input` 和其它56字节。精确 stage 724 passed/0skip/0fail/0error，28.354s，含12 Windows-native；XML bounded-search-stage-tests-20261004.xml。同目录 candidate-stage-sha256.json 为59SHA；6源码/tests Ruff格式/check通过。
- 同 scratch 根 instrumented 4×1372prefix/160final old/new/new/old 无明确整体净收益；撤阶段观察器仍有 heartbeat/GC数值回调，4轮 old首1301/1292、运输4/5、mean15.7694/16.4031ms；new首1295/1296、运输5/3、mean16.5657/16.4574ms。640 final全Han+finite baseline。broad随机整体没有明确提速，不能用来声称全输入改善。证据 bounded-search-ab-comparison-20261004.json、bounded-unobserved-ab-comparison-20261004.json；空context/synthetic seed42 logits，非实际GPU/editor。
- 目标根路径真实公开索引（最多1775条plan）、8输入×prepare/prune×16测量×4交错进程，共1024测量：old两轮case平均7.1331/6.4241ms，新3.8051/3.7759ms，约44%下降；准备显示池180→35。耗时排除plan构造及首次selection-cache构造，不是整条管道耗时。证据 bounded-root-work-comparison-20261004.json；保留完整frontier的synthetic回归单独证明覆盖。
- 可控独立 pipe 占锁故障注入（不是生产GPU测量），旧registered cancel的后台任务占锁最多80ms、每1ms检查；原生SID/creation客户端，响应后才启动该job。old两轮新请求0/24首发，运输4/5、其余pending，平均47.7026/47.2670ms，旧job0取消/24、平均80.8611/80.7002ms，24final全恢复各24重试。new两轮24/24首发、0运输/0pending/0重试，平均1.2137/1.3720ms，旧job24取消/24、平均5.7840/5.9829ms（含客户端刻意5ms间隔）。证明无需先拿状态锁就能淘汰旧工作，不证明GPU硬抢占。证据 bounded-cancel-contention-comparison-20261004.json。两次harness前置启动时机/PID0断言失败在隔离进程，已修为 after_response start_worker及运输无PID可重试；失败进程均结束，无生产改动。
- 目标路径收益/独立取消及724门通过后，只部署 neural_candidate_pages.py、neural_candidate_pages_scored.py、neural_candidate_pages_v3.py，备份 C:\Users\zhaoy\AppData\Local\NeuralWeasel\Experimental\backups\candidate-search-20261004-202147；专属 rollback-candidate-search.ps1 只读预检通过，未执行回滚。59 stage/installed/manifest一致，无observer/PTH；UI12392及MemReduct34676创建身份不变，二taskRunning。worker28900/精确NT134356189176287094；真实管道正常与重复轮结果待下文追加。生产原版刚测baseline tag bounded-search-current-baseline-20261004：2744 prefix首2498、运输39、pending207；320final全Han+finite，2/2组重试；header中位17.4381/18.4836ms，gen2 lifetime max83.9182ms。启动探测先63.2305ms运输timeout后2.7776ms成功，不能只以PID/state称ready。
- 本阶段未宣称根治：总体随机收益、真人TSF显示/数字提交、最新真实context模型评分、自然MemReduct/隔夜恢复及CUDA/GIL精确归因仍需实际证据。未读取或落盘真实上下文、候选正文/ID或生产对象图；其它dirty保留。
- 生产正常及不重启重复轮已完成，均 worker28900/NT134356189176287094、BelowNormal不变，无guard事件/observer：每轮2744prefix+320final。baseline→new normal→new repeat 首发2498→2564→2558、运输39→12→12、pending207→168→174；全部960final Han+finite baseline，重试组4→0→3。fast/paced header中位17.4381/18.4836→16.5479/17.6945→16.8495/17.9387ms；完整输入从首次发送到最终恢复max144.3751/219.3604→48.6676/52.3297→168.5245/212.3669ms，第二轮尾部仍会出现，不把首轮零重试当保证。gen2 lifetime max83.9182→68.9752→68.9752ms，新两轮分别27/46次gen2；旧/新生命周期长度不同，不能把lifetime max直接作同窗口GC因果。实际新prefix最慢62.6724ms仍超client50ms。基于目标路径隔离收益、724门及两轮实机均较baseline降低运输失败而保留补丁，未声称排除宿主外部负载、完全消除卡顿或隔夜已过。数字证据及聚合脚本位于 C:\Users\zhaoy\AppData\Local\Temp\nw-deploy-context-20261002\bounded-search-live-comparison-20261004.json 和 summarize-bounded-search-live-20261004.py；恢复计算 `(final.query_end_ns - initial.query_start_ns) / 1e6`，不要用旧只排首请求的字段。所有独立bounded隔离server均已退出，生产验证客户端完成；最终readback确认59SHA一致、UI/Mem创建身份不变、无临时observer；模型/UI任务均Running（仅为状态，不替代上述真实请求证明）。
