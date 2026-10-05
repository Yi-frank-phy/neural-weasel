# 上下文桥接断线恢复：修复与 Windows 隔离验证

日期：2026-10-02。源码基线：`ff8cfcf6d614a61ad240a2932f5069f0f2881857`，
分支：`codex/context-engine-fim-phase1`。本文记录基线上的本地修复，尚未部署。

## 结论与边界

首条上下文遇到管道断线后被直接丢弃的问题已修复，并通过本机 Windows
真实命名管道的故障注入验证。Python 完整回归 578/578 通过，原生 CTest
10/10 通过，Ruff 0.16.0 检查及格式检查通过，`git diff --check` 通过。

验证使用实际 C++ bridge / NamedPipeClient 与 Python NamedPipeServer handler，
并实际执行 Win32 管道断开和重新连接、同用户 SID 身份检查。模型接口使用
仅计数的合成 engine，因此这里证明的是传输恢复、回执与发布行为，未证明
生产 GGUF 推理、GPU 延迟、TSF 前台候选显示或真人输入体验。

未操作前台窗口，未切换输入法、注册 TSF、安装或重启生产服务，未联系或
终止另一个聊天及其进程。本机隔离测试足以验证此 Windows 管道缺陷，无需
使用 Colab。生产部署仍需桥接组件和 Python 服务配套更新；本轮未部署。

## 根因与修复

原 `ContextUpdateBridge::Process` 在首次 `TryQuery` 失败时直接返回。
`ERROR_NO_DATA`（232）既可能表示请求尚未被处理，也可能表示服务已接收
请求，但回执在读回前丢失。因此仅重连后重发会触发 stale 拒绝，或冒重复
计算的风险，不能把 stale 响应里的 epoch 当成可发布回执。

修复在后台 worker 中恢复，更新、回执、health 共用既有 readiness deadline，
最多两个恢复机会，每次等待 25 ms，可被停止或更新修订打断。只恢复断线、
超时和 busy；身份验证失败与协议错误保持失败关闭。TSF 同步路径不增加等待。

新增 `context_update_receipt`，仅保存最新一条身份元数据，不保存编辑原文或
其 hash。请求精确匹配 client epoch、request ID、session ID、source session、
source revision 和 security label 后，返回 assigned epoch。更新失败后先查
回执：已接收则直接等 health；明确不存在才重发。最终发布仍要求 exact
assigned epoch 和最新序列，重复更新仍被原有 stale 规则拒绝。

新更新、reset、secure cleanup 清除回执；清理与更新串行，避免旧更新在
清理后重新写回回执。secure cleanup 仍是不能被后续普通更新覆盖的 barrier。
旧 Python 服务不支持新消息时，桥接恢复失败关闭，不能单独部署新桥接组件。

## RED → GREEN

用 `git show HEAD:native/context/context_update_bridge.cc` 导出修复前源码，
与相同 probe / named-pipe client 编译为独立 `recovery-baseline.exe`。
三个真实管道测试全部失败：`single_update_published=0 epoch=0`，probe exit 21。
修复前原生 fake 测试也失败，exit 1。

修复后，本机 CMake 构建的 probe 对三个独立随机管道均成功；每个用例仅
调用一次 `Submit`，无需第二条上下文触发恢复：

| 断开位置 | 发布结果 | 模型更新次数 | 回执查询 | 总请求帧 |
| --- | --- | ---: | ---: | ---: |
| 读到更新后、处理前 | `single_update_published=1 epoch=1` | 1 | 1 | 4 |
| 更新已接受、回执发出前 | `single_update_published=1 epoch=1` | 1 | 1 | 3 |
| health 查询期间 | `single_update_published=1 epoch=1` | 1 | 0 | 3 |

三项单独取证运行：3 passed in 0.31s。完整 Python 回归：578 passed in 22.59s，
包含上述三项，未跳过。原生 CTest：10/10 passed in 0.47s，其中 recovery test
包含九个边界：上述三种恢复、永久断线次数上限、协议失败、身份失败、
新普通更新 supersede、secure 更新取消旧请求、Invalidate 取消旧请求。
Python 回执测试覆盖六个身份字段不匹配、正文拒绝、最新单条状态、secure
cleanup、reset、缺少 request ID、重复 stale 与丢回执不重复模型更新。

## 复现命令

MSVC Developer Environment 下，以临时目录隔离构建：

```powershell
cmake -S native -B C:/Users/zhaoy/AppData/Local/Temp/nw-recovery-20261002/cmake-build -G Ninja -DNEURAL_WEASEL_BUILD_NATIVE_TESTS=ON -DNEURAL_WEASEL_BUILD_PROFILE_TOOL=OFF -DNEURAL_WEASEL_BUILD_SESSION_ACTIVATOR=OFF -DCMAKE_BUILD_TYPE=Release
cmake --build C:/Users/zhaoy/AppData/Local/Temp/nw-recovery-20261002/cmake-build --parallel 2
$env:LOCALAPPDATA='C:/Users/zhaoy/AppData/Local/Temp/nw-recovery-20261002/isolated-appdata'
ctest --test-dir C:/Users/zhaoy/AppData/Local/Temp/nw-recovery-20261002/cmake-build --output-on-failure
```

从仓库根目录执行完整 Python 测试；环境变量只在当前测试进程及其子进程有效：

```powershell
$env:PYTHONPATH="$PWD\src;C:\Users\zhaoy\AppData\Local\NeuralWeasel\Experimental\experimental-profile\python-service\.venv\Lib\site-packages"
$env:NEURAL_WEASEL_RECOVERY_PROBE='C:/Users/zhaoy/AppData/Local/Temp/nw-recovery-20261002/cmake-build/neural_weasel_context_recovery_test.exe'
uv run --no-project --python 3.12 --with pytest --with pywin32 python -m pytest -o addopts='' -q -p no:cacheprovider
```

测试 probe 仅允许显式 `\\.\pipe\nw-recovery-test-*` 管道名；无参数只执行
fake 测试。真实管道测试对子进程使用 `CREATE_NO_WINDOW`，并将其 LOCALAPPDATA
置于 pytest 临时目录，避免写生产诊断目录。没有加载另一份模型或访问生产管道。

## 共享工作区保护

本轮未写另一个聊天已修改的三个文件，结束时 SHA-256 与开始时一致：

| 文件 | SHA-256 |
| --- | --- |
| `scripts/start-model-service-hidden.ps1` | `AA122F62767657CB337A72E16F545C8025B9C326CEF8FD5298751CD395A9CB2D` |
| `tests/test_game_mode_guard.py` | `B60DA2FB629A5C911FE4D87936C3AC03C85FC72256AC21F0C83A5B78300B4AB2` |
| `docs/handoff/codex-neural-weasel-debug.md` | `573B60BA4CA56B4BF95E320213129C9FE9D07C04B286959347819FAB09239CA3` |

修复涉及 `native/context/context_update_bridge.cc`、`src/neural_weasel/pipe_server.py`、
`native/CMakeLists.txt`、新增三份测试及桥接文档。本轮未切分支、提交、推送或
改共享 Git index；此前原生结果和验证脚本保持原状，完整测试报告只追加
后续修复链接，保留其修复前的固定版本证据。

## 后续扩大验证

同日扩大 Windows 后台故障注入，新增真实服务进程重启与清理竞态测试，
发现并修复跨进程 epoch 同号误发布。完整回归增至 587 项通过；详见
[扩大验证报告](context-fault-matrix-20261002.md)。上面的 578 项结果保留为
首轮断线恢复修复的历史证据。
