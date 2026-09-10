# Audit Agent Demo

面向 Report A/B 查看与问答、单帖采集审核、新报告生成与问答的演示项目。

> **当前是源码预览，不是已完成新机验收的安装发行版。** 主应用代码来自 `f6d19f8e82a9cf0944c7b11ffbcbfe8c15cc5bd8`。为避免分发真实账号数据，本仓库没有包含旧 SQL dump、历史账号语料、A/B 原始数据库、登录状态和运行日志。缺少这些历史资源时，A/B 展示及依赖其语料的功能不能直接运行；请勿将默认页面返回成功视为完整安装成功。

## 已包含

- React 前端 `Audit_assistant/`、Python 后端 `backend/`、Hermes 适配及业务模块 `hermes_m0/`。
- K 规则、完整 Prompt 及思考参数的固定 bundle；评论融合边界修复、视频规则短编号、失败响应留存和有界补试。
- 测试、现有启动脚本、依赖声明及发布整理说明。
- MediaCrawler 子模块，固定 `5f428d1071522ce1e011ceedeceffaa858404949`。

原始代码提交与排除内容见 [来源清单](docs/release/SOURCE_MANIFEST.json)。本仓库使用独立提交历史，原代码基线与本仓库 HEAD 是不同标识。

## 获取完整源码与采集器

```bash
git clone --recurse-submodules https://github.com/Vicenta-cc/audit-agent-demo.git
cd audit-agent-demo
git submodule update --init --recursive
```

已有克隆更新时也要执行 `git submodule update --init --recursive`，不要使用 `--remote` 自动升级依赖。使用 GitHub Download ZIP 不会自动包含采集器源码，需要另外下载固定版本并放到 `external/MediaCrawler`。

采集器安装与应用配置见其 [接入说明](https://github.com/Vicenta-cc/media-crawler/blob/5f428d1071522ce1e011ceedeceffaa858404949/docs/AUDIT_AGENT_INTEGRATION.md)。它使用独立虚拟环境，不应与审核应用混装依赖。

## 默认交付范围

目标为一套前端、一套后端、一个 worker、一个持久化数据目录；单次新任务最多采集和分析1个帖子。A/B 演示包需要包括可问答的结构化依据，不能只提供两张报告页面。

批量后台不是默认交付项，参见 [批量扩展与 Prompt 配置边界](docs/release/BATCH_EXTENSION.md)。单条/批量控制数量；任务选择的 RuleSet 控制审核配置。只有选择 K 专用版本才使用 K 的固定模板和思考参数，不会强制所有自然语言新生成规则使用 K。

## 依赖与首次安装状态

应用 Python 依赖见 `requirements.txt`；Hermes 0.20.4 的固定源码依赖见 `requirements-hermes.txt`。Hermes 当前安装产物尚需与官方指定源码完整核验，参见 [交付说明](docs/release/GITHUB_DELIVERY.md)。不要复制原开发者的虚拟环境。

前端使用 `Audit_assistant/package.json` 和 `pnpm-lock.yaml`。还需 FFmpeg/ffprobe、Node、浏览器、自己的模型服务及采集账号。按实际模式配置 ASR/OCR/翻译；源码不包含远端模型服务器。

当前 `scripts/collection_trial.py` 是历史双环境 macOS 启动器，依赖本机配置和已有数据，**不适合直接用于新机空库初始化**。旧启动命令保存在 [历史 README](docs/LEGACY_README.md)，仅供代码背景参考。

首次可安装版本还需完成：

1. 脱敏 A/B 报告、问答证据与账号语料包，并重新核验资源哈希及引用关系。
2. 幂等空库初始化、资源导入、账号加密密钥初始化和数据库升级入口。
3. 单环境单条任务的可移植启动配置、依赖预检和停止流程。
4. Hermes 固定源码核验，以及全新机器的安装、A/B 问答、单帖采集与新报告问答验收。

完整设计与当前差距见 [GitHub 交付说明](docs/release/GITHUB_DELIVERY.md)。未提供的数据不能用空文件或假成功状态替代，也不要把真实数据库、cookies 或密钥提交来消除报错。

## 验证范围

主应用原基线的回归和真实回放记录见 `docs/video-rule-codes.md`、`docs/ethnic-k-integration.md`。这些记录不等于本仓库删去私有资源后已通过全新安装验收。依赖真实语料的离线测试需要经过批准的独立数据包；不应宣称源码预览的全部测试已通过。

第三方组件保留各自许可与声明；本仓库不替第三方重新授权。
