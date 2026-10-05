# 2026-10-05 远端文件及文档审计

## 范围与结论

起点为 GitHub `main` 的 `8f493c85ac1b8f7fd5358a46469ca1e9ec889256`。
通过 GitHub connector 读取主线与完整递归树，再与同提交的本地文件和实现对照。
远端树未截断：285 个文件，共 2,283,237 字节。未发现模型权重、数据库、运行日志、
可执行文件或构建输出被跟踪。该检查不是逐行安全审计或完整功能验证。

根代理审计 README、文件树、重复内容和相对链接；Sol 独立只读审计架构、STATUS、
规格、CI 和手工验收。修改仅涉及文档和一个无内容文件，不部署、不重启服务、不操作前台。

## 已修正

- README 和手工验收的 `-Backend full/sparse` 已失效，改用实际支持的
  `-Quantization Q8_0/Q4_K_M`。Torch full/sparse 对比仅作为独立开发工具。
- README 的 `build-index/predict --model` 和 `replay --backend` 不符合当前 CLI；
  删除失效参数，明确 pinned 4B Q8 GGUF 与 CUDA llama.cpp 准备方式。
- STATUS、README 与手工验收不再把有界多 token 和简拼路径写成未实现；
  源码/合成回归支持与真人验收仍分开。依据为当前候选继承链、
  `tests/test_neural_latin_multitoken.py` 和 `tests/test_long_shorthand_publication.py`。
- 原生架构改为 TSF capture/send → 单向 capture pipe → server broker/bridge →
  Python model pipe；删去 TSF 直接拥有模型桥/等待 worker 的旧描述。
  当前捕获预算为 before 27648、after 4096 UTF-16 units，frame 上限 32768/4096，
  不能声称 adapter 已实现旧设计中的 idle 32768/32768 扩读。
- FIM 参数由 CLI 支持，打包 PowerShell 启动器不转发该参数；默认仍 continuation。
- README 不再将 PRIVATE 上下文列为本地存储内容，明确只允许临时使用，禁止持久化。
- 英文 Space 接受新鲜合法默认候选，否则 literal 加空格；Escape 取消 composition，
  不提交 literal/model text。手工编号验收以实际可见候选为准。
- CI 文档改为当前 `windows-vertical-slice` 完整 bundle 与 tag-gated release，
  删除把旧 149 项测试当现行状态的说明。
- 增加文档入口及旧规格/交接提示，保留历史数据、固定提交、失败路线和回滚信息。
- 删除根目录 `x24null`：跟踪内容为空，未发现引用，Git 历史可恢复。

## 保留及未闭合事项

- `neural_candidate_pages_v2.py`、`neural_candidate_pages_v3.py`、scored 和当前 pager
  构成实际继承链，不能仅因版本后缀就当重复文件删除。
- 中英文 `.cmd` 启动器内容相同，但有明确用户入口和打包引用，保留别名。
- 最大文档是长期交接记录（161,730 字节），包含独立实验与部署证据；本次只更新入口提示。
- QwenIME 文档属于独立兼容路线；保留它的静态验证限制，不宣称已部署完整替换。
- UIA provider 尚未实现，见 issue #43。完整真人安全/上下文/延迟/交互验收仍未闭合。

## 验证

Markdown 检查覆盖 29 个文件、44 个相对链接，未发现目标缺失；7 个 CLI 示例
通过实际 argparse 解析且未启动模型；PowerShell AST 确认启动器参数及 Q8 默认值。
`git diff --check` 通过。检查完整 diff 与文件范围，只有文档和空文件删除。
未执行模型、安装、实际服务启动或真人手工验收，未复跑整个运行时测试套件。
