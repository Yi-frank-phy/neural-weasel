# 上下文引擎：Windows 后台故障注入扩大验证

日期：2026-10-02。分支：`codex/context-engine-fim-phase1`。
源码基线：`ff8cfcf6d614a61ad240a2932f5069f0f2881857`，结果包含本地未提交修复。

## 结果与范围

真实 Windows 命名管道测试 12/12 通过，其中新增 9 项服务进程退出、
替换和修订/清理竞态测试。完整 Python 回归 587/587 通过，耗时 21.89 秒；
MSVC 原生干净重建后 CTest 10/10 通过，耗时 0.57 秒。原生恢复测试内含
12 个确定性边界用例，包括同 PID、不同创建时间的模拟复用。
Ruff 0.16.0 检查、格式检查（175 文件）和 `git diff --check` 通过。

使用本机实际 C++ bridge / NamedPipeClient、Python NamedPipeServer 和 Win32
管道。服务重启用例确实退出旧 Python 进程，再由不同 PID 的预热替代进程
接管相同的独立测试管道。模型接口仅使用计数 engine；本轮没有加载 GGUF、
测量 GPU 推理延迟或验证前台 TSF 候选显示。

## RED 与最小修复

扩大测试先得到 8 通过、1 失败，并再次重现同一失败：旧服务返回 epoch 101
的更新回执后，在 health 阶段退出；替代服务尚未接受这一更新，但自己的
epoch 恰好也是 101。桥接仅比较 epoch 数字，错误发布旧回执：

```text
restart_old_refused=0 old_epoch=101
probe exit=33
```

`native/pipe/named_pipe_client.h` 的 `QueryResult` 新增内存中的
`ServerProcessIdentity`；`native/pipe/named_pipe_client.cc` 使用
`GetNamedPipeServerProcessId` 和 `GetProcessTimes` 获取 PID、创建时间，
通过原有同用户 SID 检查后绑定到连接，成功响应携带该身份。断线清空身份，
重新连接重新验证；身份获取失败拒绝连接。

`native/context/context_update_bridge.cc` 在比较 ready epoch 前，要求 health
与更新回执来自同一进程生命周期。PID 改变，或 PID 被复用而创建时间改变，
均将旧回执标为 superseded。修复后上述场景输出：

```text
restart_old_refused=1 old_epoch=0
restart_new_published=1 epoch=102 revision=2
```

原有同步 TSF 路径、后台重试次数/期限和 private 原文不持久化边界保持不变。
没有新增原文日志、原文 hash、协议身份字段或模型按键调用。

## 真实故障矩阵

以下服务调用次数由各进程仅记录 PID、事件和计数的 JSONL 与 probe 输出关联：

| 场景 | 观测结果 |
| --- | --- |
| 旧进程处理更新前退出 | 旧/新调用 0/1；一次 Submit 发布新服务 epoch 1 |
| 旧进程接受更新但未回 ACK 就退出 | 旧/新调用 1/1；一次 Submit 在新服务重新建立 epoch 1 |
| ACK 后、health 阶段退出；新 epoch 从 0 开始 | 拒绝旧回执；新修订 2 发布新服务 epoch 1 |
| ACK 后退出；新服务 epoch 恰与旧 ACK 同号 | 拒绝旧回执；新修订 2 发布 epoch 102 |
| 旧响应挂起时提交新修订 | 只发布修订 2；服务观察修订顺序 1、2 |
| 旧响应挂起时转 secure | 发布 epoch 保持 0；一次清理；receipt 和绑定清空；保护文本未发送 |
| secure 后立即提交正常修订 | 服务观察 update、focus、update；只发布修订 3 |
| 旧响应挂起时 Invalidate | epoch 保持 0；无恢复查询或旧候选发布 |
| 旧响应挂起时 Stop | 线程退出；epoch 保持 0；无恢复查询 |

前一轮三项同进程断线测试也通过：处理前断线、ACK 丢失、health 断线。
模型计数每项均为 1；前两项各查询一次 receipt，health 断线无需 receipt。

修订/清理测试仅在第一次真实 OS 查询返回后挂起 worker，确定性地提交
新修订或清理，再释放真实响应；没有伪造协议响应。PID 复用使用原生模拟
transport 验证，未尝试在 Windows 上强制制造真实 PID 复用。

## 命令与产物

原生配置沿用独立临时构建目录，Release、native tests ON，profile tool
和 session activator OFF。干净重建命令为：

```text
cmake --build C:\Users\zhaoy\AppData\Local\Temp\nw-recovery-20261002\cmake-build --clean-first --parallel 2
ctest --test-dir C:\Users\zhaoy\AppData\Local\Temp\nw-recovery-20261002\cmake-build --output-on-failure
```

Python 使用 3.12、pytest、pywin32，`PYTHONPATH` 包含 checkout 的 `src` 和
已安装服务的 site-packages；设置 `NEURAL_WEASEL_RECOVERY_PROBE` 指向上述
构建目录的 `neural_weasel_context_recovery_test.exe`：

```text
uv run --no-project --python 3.12 --with pytest --with pywin32 python -m pytest -o addopts="" -q -p no:cacheprovider
uv run --no-project --python 3.12 --with pytest --with pywin32 python -m pytest tests/test_windows_context_fault_matrix.py tests/test_windows_native_context_recovery.py -o addopts="" -q -s -p no:cacheprovider
```

干净重建后二次定向验证：12 passed in 1.54s。临时证据目录
`C:\Users\zhaoy\AppData\Local\Temp\nw-recovery-20261002` 包含
`fault-matrix-green.log` 和 `expanded-clean-build.log`。最终 probe SHA-256：
`4F2ADFD5FE3A8B2643F5C37F01762DA82E765749F30DB863860A6937E3AA9A63`。

修改公共 `QueryResult` 后，两次增量构建中的旧 bridge 单测曾失败；重编译
该测试后通过。未单独定位增量构建失败的原因，最终验收采用全部原生产物
干净重建，并再次运行真实管道矩阵，不采用此前失败的增量产物。

## 隔离、清理和剩余边界

测试管道仅允许 `\\.\pipe\nw-recovery-test-*`，子进程使用
`CREATE_NO_WINDOW`，LOCALAPPDATA 指向 pytest 临时目录。故障退出只作用于
本测试启动的服务；finally 回收全部自建 Popen，另有 30 秒服务自限。
没有使用持久 PID 强杀、全局进程树终止、生产管道或生产诊断目录。
执行后检查无测试服务/测试 probe 遗留。

替代服务预先导入并等待 gate，测试证明的是在现有有界重试内的预热恢复。
未证明任意时长冷启动均可恢复。ACK 后服务更换会拒绝旧更新，下一条修订
重新建立上下文；本轮没有添加跨进程重放旧回执或无限等待。

未操作前台、切换输入法、注册 TSF、部署、重启生产服务、切分支、提交、
推送或改变共享 Git index。另一个聊天涉及的三个文件 SHA-256 与本轮开始
一致：

- `scripts/start-model-service-hidden.ps1`: `AA122F62767657CB337A72E16F545C8025B9C326CEF8FD5298751CD395A9CB2D`
- `tests/test_game_mode_guard.py`: `B60DA2FB629A5C911FE4D87936C3AC03C85FC72256AC21F0C83A5B78300B4AB2`
- `docs/handoff/codex-neural-weasel-debug.md`: `573B60BA4CA56B4BF95E320213129C9FE9D07C04B286959347819FAB09239CA3`

当前运行安装未包含本地修复，真人编辑器验收仍未执行。
