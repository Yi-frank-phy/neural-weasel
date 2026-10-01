# Handoff: Neural Weasel target-machine candidate and latency RED

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
