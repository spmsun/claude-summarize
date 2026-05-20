# Claude Norms Update

从 Claude Code 聊天记录中自动提取开发规范，比对并更新 CLAUDE.md。

## 原理

每次开发者与 Claude Code 对话时，会自然表达编码规范：纠正、原则、反馈、重复诉求。该工具从聊天记录 JSONL 中提取这些信号，聚类去重，交由 LLM 分析后追加到 CLAUDE.md，实现「跨会话学习」。

## 特性

- **自动提取** — 扫描 Claude Code JSONL 聊天日志，过滤系统噪音
- **语义聚类** — 基于 Jaccard 相似度（零外部依赖）聚合相近消息
- **信号评分** — 自动标注 CORRECTION / PRINCIPLE / FEEDBACK / YAGNI 等类型并打分
- **增量处理** — 状态文件追踪已处理会话，仅分析新增
- **跨会话跟踪** — 标记跨多个会话出现的重复诉求

## 快速开始

### 前提

- Claude Code 环境（聊天日志位于 `~/.claude/projects/`）
- Python 3.8+

### 用法

```bash
# 提取指定项目的聊天规范
python3 extract_chat_norms.py /path/to/your/project

# 调整聚类灵敏度
python3 extract_chat_norms.py /path/to/your/project --cluster-threshold 0.5

# 详细输出
python3 extract_chat_norms.py /path/to/your/project -v
```

输出文件：
- `.norm_digest.md` — 结构化的消息摘要（热点聚类 + 信号评分表 + 完整正文）
- `.norm_extractor_state.json` — 增量处理状态

### 完整流程（作为 Claude Code Skill）

1. 将本仓库作为 Skill 注册到 Claude Code
2. 在项目中运行 `/norms` 或「提炼规范」
3. LLM 自动分析 `.norm_digest.md`，比对现有 CLAUDE.md，追加新规范

## 输出示例

```
# Chat Norms Digest — 2026-05-20 07:04

**48** unique messages from **15** sessions

## 🔥 热点聚类

### 聚类 1（3 条，平均信号强度 2.8）
- 配置方面的问题… [横跨 2 个会话]
- 现在有重复的配置…

## 📋 消息明细（按信号强度排序）
| # | 信号 | 强度 | 消息 | 会话数 |
|---|------|------|------|--------|
| 1 | CORRECTION | 3.0 | 不要用位置索引取数据… | 1 |
```

## 参数说明

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `project_dir` | — | 项目目录路径 |
| `--days` | 0 | 仅处理最近 N 天内对话（0=不限制，定时运行建议 7/14/30） |
| `--cluster-threshold` | 0.45 | Jaccard 聚类阈值（0~1） |
| `--min-length` | 15 | 最小消息长度 |
| `--max-length` | 2000 | 最大消息长度 |
| `-v` | — | 详细输出 |

## 架构

```
聊天日志 JSONL          .norm_digest.md          CLAUDE.md
  │                          │                       ▲
  ▼                          ▼                       │
[提取脚本] ──▶ 聚类 + 评分 ──▶ [LLM 分析] ──▶ 去重写入
                无外部依赖       语义理解         追加到全局/项目
                增量追踪                           ↕ 无冗余
```

## License

MIT
