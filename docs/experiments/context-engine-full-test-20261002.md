# 2026-10-02 上下文引擎完整自动测试记录

后续修复记录：本文保留修复前的固定版本证据。断线恢复缺口已在本地源码修复，
本机真实 Win32 管道三项故障注入、578 项 Python 回归和 10 项原生 CTest 通过；
见 [断线恢复修复报告](context-update-recovery-20261002.md)。尚未部署或完成真人编辑器验收。

本轮自动回归、原生模型正确性验证和已发布分支 CI 均通过，但额外隔离复现确认了一项断线恢复缺口，不能判为完整验收通过。真实编辑器手工验收尚未执行，不能据此宣称目标机输入法已健康或 FIM 可以切为生产默认。

## 范围与版本

- 分支：`codex/context-engine-fim-phase1`。
- 被测运行时代码：`ff8cfcf6d614a61ad240a2932f5069f0f2881857`。
- 本地工作区另有守护句柄修复、对应测试与 handoff 更新；均保留，570 项本地回归包含这些改动。远端 CI 与原生模型验证针对上述固定提交，不包含这些工作区改动。
- 本轮仅新增原生验证脚本和测试记录，没有改动运行时、安装、重启服务或操作前台。默认仍为 continuation。

## 结果

| 验证 | 结果 | 证据范围 |
| --- | --- | --- |
| 本地 Python 3.12 全套回归 | 570 passed，27.14 s | 当前工作区，包含真实 Windows 守护句柄回归 |
| Ruff 0.16.0 | check、format 均通过 | 最终 format 检查共 170 个文件 |
| Git whitespace 检查 | 通过 | 只有现有 PowerShell 文件 LF/CRLF 提醒 |
| Windows 原生 CTest | 9/9 passed，0.14 s | 固定提交的 GitHub CI |
| Windows TSF/server 构建、隔离资源、打包、安装/卸载与启动器 dry run | 通过 | CI；未做实际全局注册 |
| 真实 Q8 多 token 分支、恢复、缓存与清理 | 6 组、16 个续评分分支通过 | 临时 Colab，CPU 两线程；中文、英文、代码 × 两种上下文模式 |
| 断线后首条上下文自动恢复 | 未通过预期行为 | 现存隔离原生复现：首条更新丢失，下一条才发布 |

本地回归命令：

```powershell
$env:PYTHONPATH="$PWD\src;C:\Users\zhaoy\AppData\Local\NeuralWeasel\Experimental\experimental-profile\python-service\.venv\Lib\site-packages"
uv run --no-project --python 3.12 --with pytest --with pywin32 python -m pytest -o addopts='' -q -p no:cacheprovider
```

[CI run 36942375620](https://github.com/Yi-frank-phy/neural-weasel/actions/runs/36942375620)：Python job `110637045826`、Windows job `110637045386` 均 success；仅 tag 发布 job skipped，符合分支触发规则。已通过 connector 读取原生 job 日志，确认各 CTest、安装安全检查与 dry-run 成功。

GitHub artifact `11201366287`，`neural-weasel-experimental-x64`，35817296 bytes；GitHub 报告 digest `sha256:c84704b77ba97435c9e099c0c1fbe805fa6d7c2d9afb68f1e460217bfae2bb7f`，其 source SHA 为上述固定提交。此处没有另行下载并重算 ZIP hash。

## 原生模型验证方法与限制

用 `scripts/verify-fim-native.py` 执行：

```powershell
colab-job highram scripts/verify-fim-native.py --source-commit ff8cfcf6d614a61ad240a2932f5069f0f2881857
```

只获取公开固定提交与公开 Q8 模型，用合成上下文测试。llama-cpp-python `0.3.23`、`n_ctx=256`、CPU 两线程。模型 revision `d1238424e1efe0c4389935d7bd03853378d5c9e1`；GGUF SHA-256 `2a5266475777daf23e21991592a006f6dbc0be4620ac6a12d7d57240b0027711`。完整逐组结果在 [原生验证 JSON](context-engine-native-test-20261002.json)。

CPU 测试明确跳过生产 GPU-only 构造器，将真实 llama.cpp context 接到现有 runtime 方法，没有伪造 GPU 探针。验证 snapshot、native sequence capture/restore、完整 root 重放、旧 root 计算后的新缓存丢弃、suffix-only 更新、private invalidation 与旧 snapshot 拒绝。每组 100 次 immutable 候选评分，共 600 次查询，均没有新增 eval；相同上下文再次查询也没有新增 eval。

每个续评分分支均与独立冷启动参考对照：直接清空模型，分别 eval 完整 root 和候选 token path，再从全词表 logits 独立计算 log-softmax，不调用 runtime 恢复或缓存辅助方法。最大绝对 log-probability 误差 `8.831282087840009e-7`，通过原定 `rtol=0, atol=2e-4`。每个候选为 2 或 3 token，未使用单 token 样例替代多 token 验证。

首轮驱动因 Colab notebook 没有 `__file__` 而退出，尚未进入模型检查；改为生成独立 worker。下一轮发现整段一次 eval 与 root/path 分段 eval 的差异 `0.0454514`，原严格容差判为失败。最终同时运行这两种冷启动计算：相同分段参考与 runtime 恢复吻合；整段与分段差值单独保留在 JSON，最大 `0.0454512805769447`。没有放宽恢复正确性的容差，也没有修改生产 runtime。此结果支持差异来自计算分批方式，但未进一步定位具体量化内核。

三次临时 Colab 运行均报告 `Session terminated`，由 colab-job finally 释放；未保留常驻运行时。

## 额外发现：首次上下文更新的断线恢复缺口

收尾时并行诊断向现有 handoff 追加了该问题。本轮读取隔离复现源码后，复跑其现存 `bridge-drop-once.exe`；没有另行重编译，所以该二进制的构建来源依据现有 handoff，而不是本轮独立构建证明。当前源码 `native/context/context_update_bridge.cc:467` 的发送路径也显示，transport error 后直接返回，不会恢复同一最新更新。

复现使用合成 DropOnce transport，首调用返回 `kDisconnected/ERROR_NO_DATA`，不连接生产管道。进程的 LOCALAPPDATA 指向新的临时测试目录。输出：

```text
single_update_abandoned=1 calls=1 epoch=0
next_update_published=1 calls=3 epoch=1
```

首条更新等待 300 ms 后仍未再次发送；提交下一条更新后才能发布。程序 exit 0 表示它成功重现缺陷，不能记为输入法恢复能力通过。期望首条最新上下文在断线后自行有界恢复，这个行为目前未满足。本轮没有修改桥接层，也不能据此认定它独自造成真人持续无候选。

修复需覆盖 superseded、secure cleanup 和 ack 丢失，不能复活过期上下文或扩大 TSF 同步等待。这项缺口没有被当前 570 项回归或 9 项 CTest 捕获，应作为后续回归补齐。

## 尚未证明

真实目标 Windows 的 Win+Space 可见性、真人编辑器候选显示/选择/提交、真实 surrounding context、密码/PIN 捕获边界、服务故障下真人体验、实际卸载以及 GPU/按键延迟，仍需遵循 `docs/manual/windows-install-smoke-test.md`。本轮保持前台限制，没有执行这些手工步骤。

这次原生实验验证数值与生命周期正确性，不是代表性候选质量 A/B，也不覆盖生产长上下文窗口。此前六个合成样例的排名结果不能替代这些验收。FIM 保持 opt-in，UIA high-context 与默认模式切换仍后置。
