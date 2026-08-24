# Neural Weasel ContextUpdateBridge 无候选修复与验收计划

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
