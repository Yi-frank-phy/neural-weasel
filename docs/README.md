# 文档入口

2026-10-05 整理。当前开发分支为 `main`；源码实现、自动测试记录与目标机真人验收
是三类证据，不能相互替代。远端源码也不等于本机已安装版本。

## 当前指引

- [项目介绍与启动命令](../README.md)
- [仓库规则与安全边界](../AGENTS.md)
- [实现状态及未闭合验收](STATUS.md)
- [Windows 安装与手工验收](manual/windows-install-smoke-test.md)
- [原生组件与构建](../native/README.md)
- [TSF 捕获、服务器代理与模型通信](architecture/native-integration.md)
- [服务器上下文桥与断线恢复](architecture/context-bridge.md)
- [continuation/FIM 评分与实验限制](architecture/context-prompt.md)
- [当前 CI 与发布流程](ci.md)
- [未来 Windows UIA 取文路线](https://github.com/Yi-frank-phy/neural-weasel/issues/43)

## 固定版本证据

- [发布说明：上下文实验前基线](releases/v0.1.0-experimental.20261001.md)
- [FIM 合成 A/B](experiments/context-fim-ab-20261002.json)
- [上下文引擎自动测试](experiments/context-engine-full-test-20261002.md)
- [断线恢复修复](experiments/context-update-recovery-20261002.md)
- [本机长期实验、部署与回滚交接](handoff/codex-neural-weasel-debug.md)
- [2026-09-06 PR36 历史交接](handoff/pr36-cloud-local-sol-20260906.md)
- [分支整理、未确认整合的历史改动与恢复备份](experiments/branch-route-audit-20261005.md)
- [本次远端文件及文档审计](experiments/repository-file-audit-20261005.md)

实验和交接文件以各段日期、提交与证据范围为准；原分支名和进程身份不代表当前状态。
它们保留失败路线、原始结果及回滚依据，未因整理而删除。

## 历史设计与独立兼容路线

- [v0.2 设计规格](specs/v0.2-testable-bilingual-ime.md)及[验收追踪](specs/v0.2-acceptance-tests.md)
- [统一约束引擎设计基线](architecture/unified-constraint-engine.md)
- [QwenIME 独立兼容范围](specs/qwenime-0.5.0.7-compat.md)

v0.2 中部分旧评分公式、运行时和功能范围已被后续实现替代；优先读当前指引。
QwenIME 静态兼容验证不证明 Neural TSF 安装或真实编辑器交互通过。
