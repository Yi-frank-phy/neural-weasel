# Neural Weasel ContextUpdateBridge 无候选修复与验收计划

## 2026-08-24 22:15 快速 handoff（当前事实，优先于下方旧计划）

### 当前结论

- 用户在目标机对 v3 做了真人测试，结果为“啥也没有”：候选窗仍不显示；本轮不得宣称修复或验收通过。
- 当前症状不是模型推理问题。低资源 stub 已能返回候选，独立 IPC probe 也确认实验 Server 的 Weasel IPC 会话可连接、Echo、读取完整响应。
- “透明主题”已排除；“强制实验 TSF 自己创建候选窗”也已失败。v2 强制 `_pbShow = TRUE`、直接 `_MakeUIWindow()` 后仍不可见；v3 恢复固定上游的 `BeginUIElement/EndUIElement` 后仍不可见。因此不能继续在这两个分支间盲改。
- TSF 切换卡顿是另一故障，已由有界重连修复并经用户确认“不再卡顿”。保留 `_nextReconnectTick`、`GetTickCount64()` 和 1000 ms 重试；不得恢复 `ShellExecuteW`、`Sleep` 或 detached reconnect thread。
- 下方旧的 ContextUpdateBridge stale-epoch 假设没有得到实机证据，不能作为当前候选窗故障的根因。当前优先级应改为 TSF 按键到候选 UI 的元数据生命周期采证。

### 已部署状态

- 当前注册 DLL：`C:\Users\zhaoy\AppData\Local\NeuralWeasel\Experimental\experimental-profile\ui-lifecycle-upstream-v3\NeuralWeaselExperimentalTSF.dll`
- SHA-256：`CB539DDF45DAFFF83C591DF6CCBA136C6E8C7C33BCEA4DDFCDAE37AF818A9F14`
- COM CLSID：`{8AA66261-ED5F-46B0-895D-339B42C3AE1B}`；Profile GUID：`{C9B3984E-A16C-4779-80E8-ACD988C57B0D}`；profile status 已确认 registered 且路径匹配 v3。
- 固定上游：`9cc96e20dc71b80876b12f689bb5863c76c2a7ed`。全新构建树：`C:\Users\zhaoy\Downloads\neural-weasel-repo\build\weasel-upstream-ui-lifecycle`。
- 实验 Server 仍运行；Q4 模型未启动。不要在 UI 路径未采证前启动 Q4，以免再次造成约 6 GB 私有内存和明显系统资源压力。
- 进程隔离测试宿主 `C:\Users\zhaoy\Downloads\neural-weasel-repo\build\candidate-ui-host.exe` 已关闭；它只对自身进程激活实验 profile，不改变系统默认输入法。

### 本轮源码与测试

- `scripts/prepare-weasel-overlay.ps1`：删除未经证据支持的 `CandidateList.cpp` 强制自绘改写；保留已经验证的有界重连。UTF-8 BOM 仍为 `EF BB BF`。
- `tests/test_experimental_tsf_ui_lifecycle.py`：回归契约改为保留固定上游 CandidateList UI 生命周期，并继续要求有界重连。
- RED 已观察：旧 overlay 因包含 `$CandidateListSource` 失败；最小改动后转绿。
- 相关 Python 回归：18 passed，包括 TSF lifecycle、candidate UI config、no editor-context disk bridge、privacy、session binding 和 architecture contract。
- xmake `WeaselTSF` Release 构建成功；产物 1,110,528 bytes。源码确认同时包含 `BeginUIElement/EndUIElement` 与有界重连，且无 `ShellExecuteW`/`Sleep`。

### 下一步（只做一个可证伪实验）

1. 给 v3 对应的 TSF 路径增加**仅元数据**诊断，不记录按键、预编辑、候选文本、编辑器文本、窗口标题或 capability：
   - DLL 激活/停用；
   - `_EnsureServerConnected` 的 Echo/Connect 布尔结果与耗时；
   - key test/handle 是否进入以及是否被消费（只记事件序号与布尔值）；
   - IPC response 是否到达、候选数量、是否请求显示；
   - `CCandidateList::StartUI` 是否进入、`BeginUIElement` HRESULT、`_pbShow`；
   - `_MakeUIWindow` 后 HWND 是否存在、`IsWindowVisible`、窗口矩形和线程/进程 ID。
2. 继续使用低资源 stub 和进程隔离宿主输入一次 `nihao`。根据日志锁定断点：
   - 无 key handle：查 profile 激活/TSF sink；
   - 有 key、无 response：查 IPC session/请求；
   - 有 response、无 StartUI：查 response 到 UI 状态转换；
   - 有 StartUI、无 HWND/不可见：查窗口创建、owner、坐标/DPI/Z-order；
   - HWND 可见但用户看不到：记录矩形后再查离屏/透明/合成，不先改主题。
3. 只修复被这轮日志证实的第一处断点，新增回归，再构建到新的版本目录；不要覆盖仍被进程加载的 DLL。

### 工作树约束

- 当前分支 `agent/q4-runtime-selector`，工作树有大量用户未提交改动；不得 reset、checkout 或清理无关文件。
- 旧 Lua 磁盘桥已禁用并删除遗留 request/response 文件；不得恢复任何原始上下文落盘路径。
- 当前仍未完成候选显示、提交、隐私、失败模式、卸载和延迟的整体验收。

## Summary

- 继续使用当前 `agent/q4-runtime-selector` 分支，保留全部未提交调试改动；不切换或清理工作树。
- 先恢复已停止的 Q4 服务和实验版 Server，再用隐私安全日志确认根因；禁止未经证据直接修改协议。
- 首要假设：长驻 Python 服务保留较大的 `_latest_client_context_epoch`，而重启后的 C++ Bridge 从 1 重新计数，导致更新被判为 `accepted:false, stale:true`。

## Implementation Changes

1. **采集桥接证据**
   - 在 `native/context/context_update_bridge.cc` 临时记录 Submit、请求构造、transport 状态、ack 安全字段、health epoch、Publish 和所有 `SetResult`。
   - 只记录序号、source revision、标签、文本长度、状态码、Win32 错误和耗时；不记录请求正文、编辑器文本、窗口标题或 capability 内容。
   - 启动 Q4 服务，确认 pipe health；重建并部署实验 Server，重新激活输入法，在全新记事本中输入 `nihao`，采集一轮 `bridge-debug.log`。

2. **按唯一证据分支定位**
   - 无 Submit：先核对部署二进制的时间戳/哈希和 Bridge 构造；已有 `FORWARD` 时不改 TSF 捕获逻辑。
   - transport 失败：修复模型服务存活、pipe 身份或连接问题，不改 ack/parser。
   - ack 为 stale：执行下述会话化去重修复。
   - ack 成功但 health 不到 assigned epoch：依据 `last_context_error` 追到模型 context worker；仅修复被证实的 readiness 故障。
   - 持续 `kSuperseded`：关联新 Submit/Clear 的 revision，区分正常 latest-wins 与错误焦点清理。
   - 已 Publish 但候选栅门仍无效：记录 Bridge 与 translator 侧 `EditorContextEpoch::Instance()` 地址，检查重复链接/重复单例。

3. **首要根因被确认时的最小修复**
   - 在 `src/neural_weasel/pipe_server.py` 中，对带 context binding 的更新按 `context_session + source_revision` 判断新旧，不再使用进程级 client epoch 跨会话拒绝。
   - 同一 capability 只接受更大的 source revision；新 capability 可从低 client epoch 开始。无 binding 的兼容请求继续沿用原有全局 client epoch 规则。
   - 用有界 LRU 保存最近 source revision，沿用现有上下文绑定容量；保持 context-update ack 的现有字段和线形不变。
   - 保留 `readiness_timeout=3000ms`，增加默认值回归检查，不扩大按键查询超时或让 TSF 等待模型。

4. **清理并固化可重建状态**
   - 将 deps 树中已验证的 librime 初始化修复正式加入 `scripts/prepare-weasel-overlay.ps1`：`initialize()` 接收含 `default`、`ai_translator` 模块及正确目录/app/log 字段的 `RimeTraits`。
   - 增加 overlay 可重复运行和初始化 seam 回归测试。
   - 根因验证后移除 Bridge、broker、translator 和 module 的临时文件日志/标记；不保留硬编码用户路径。
   - 根因修复与 overlay/清理分别形成可独立验证的提交。

## Interfaces and Tests

- 不新增公开 wire 字段；只调整已有绑定式 `context_update` 的 stale 判定语义。
- RED 测试覆盖：
  - 同一 capability 曾使用 client epoch 18，Bridge 重启后以 client epoch 1、较新 source revision 更新仍被接受。
  - 相同或更旧 source revision 被拒绝。
  - 新 capability 可从 client epoch 1 开始。
  - 无 binding 的旧客户端仍保持全局单调规则。
  - 旧 capability/revision 无法查询新模型 epoch；password/private 清理契约不回退。
- 运行 Python 协议、context binding、privacy、engine 测试，Native Bridge/IPC 测试，overlay/install safety 套件和完整项目测试。
- 重新执行 xmake 构建并检查新产物实际部署；不得仅凭源码或旧 artifact 宣称成功。
- 目标机验收依照 `docs/manual/windows-install-smoke-test.md`：
  - `Win+Space` 可见并能在记事本显示、选择和提交中文候选。
  - 实际 surrounding context 能改变候选，同时栅门具有正确 capability/revision。
  - Q4 服务停止或重启时输入不阻塞、不崩溃、可提交 literal。
  - 特别验证“Python 模型服务保持运行，仅重启实验 Server”后首个新上下文仍能发布。
  - password/protected 字段零捕获；本地搜索测试 sentinel 后只记录通过/失败，不保存 sentinel。
  - 记录 context publication 与候选查询延迟。
  - 卸载两次验证幂等、残留清除以及官方 Weasel/Microsoft Pinyin 不受影响。

## Assumptions

- 使用 handoff 已验证的 Q4 模型作为本轮目标机调试基线；功能修复保持量化无关。
- 不实现 Engram、英文扩展、模糊拼音或 typo correction。
- 当前服务已停止属于复现前置状态，不预判为 Bridge 根因。
- 不使用子代理；由当前会话顺序完成采证、RED、最小修复和最终验证。
