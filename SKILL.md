---
name: claude-norms-update
description: >
  Extract development norms from Claude Code chat history, analyze against CLAUDE.md,
  auto-append new rules. Cross-session learning. Use when user says "提炼规范",
  "更新 CLAUDE.md", "回顾聊天记录", "总结开发规范", "auto-update claude.md",
  "从聊天中学习".
---

从 Claude Code 聊天记录中提取开发规范，比对更新 CLAUDE.md。

## 触发

用户说"提炼规范"、"更新 CLAUDE.md"、"回顾聊天"、"总结规范"、
"cross-session learning"、"/norms" 时触发。

## 流程

### Step 1: 提取

```
python3 {baseDir}/extract_chat_norms.py <project_dir>
```

可选参数：
- `--days 7` — 仅处理最近 N 天内对话（0=不限制，定时运行建议设 7/14/30）
- `--cluster-threshold 0.45` — Jaccard 相似度聚类阈值（0~1，默认 0.45）
- `--min-length 20` — 最小消息长度（默认 15）
- `--max-length 1000` — 最大消息长度（默认 2000）
- `-v` — 输出处理进度

提取结果 `<project>/.norm_digest.md` 包含：

- **热点聚类** — 语义相似的消息按组聚合，标注跨会话出现次数，优先分析
- **消息明细表** — 每条消息附带信号类型标签（CORRECTION / PRINCIPLE / FEEDBACK / TASK / QUESTION / YAGNI）和强度评分
- **完整正文** — 供 LLM 逐条分析

### Step 2: 分析

读取 `.norm_digest.md`，逐区分析：

**首先看热点聚类** — 同一聚类出现多条说明用户反复强调同一诉求，规范信号强：
- 聚类中所有消息提炼出一条通用原则
- 跨会话（标注"横跨 N 个会话"）的聚类优先处理

**然后过消息明细表** — 按信号类型分类处理：

| 类型 | 含义 | 处理方式 |
|---|---|---|
| `CORRECTION` | 用户纠正行为 | 最高优先级，提炼为"禁止/不要"规则 |
| `PRINCIPLE` | 用户陈述原则 | 直接提炼为规范条目 |
| `FEEDBACK` | 问题/错误报告 | 分析是否属于规范缺失 |
| `YAGNI` | 过度设计警告 | 提炼为简洁约束 |
| `REPETITION` | 同类问题反复出现 | 说明规范未落实，强化表述 |
| `TASK` | 任务指令 | 低信号，仅当含潜在原则时保留 |
| `QUESTION` | 疑问 | 最低信号，通常跳过 |

与现有 CLAUDE.md（先全局 `~/.claude/CLAUDE.md`，再项目 `<project>/CLAUDE.md`）逐条比对：

- 已有 → 跳过
- 缺失 → 纳入 Step 3
- 冲突 → 标注，询问用户

### Step 3: 精炼

写入 CLAUDE.md 前，对拟新增规范做精炼整理：

- **合并同类项**：多条相似规范合并为一条精炼表述，语义重叠的只留最概括的一条
- **去冗余**：去掉重复约束、多余修饰词，保持每条一行
- **抽象为原则**：具体案例提炼为通用原则，避免写入路径/文件名等易腐信息

### Step 4: 更新

精炼后的规范追加到对应级别的 CLAUDE.md：

- **全局通用**（命名原则、DRY/KISS、异常处理等）→ `~/.claude/CLAUDE.md`
- **项目专属**（ORM 约定、爬虫架构、具体表字段等）→ `<project>/CLAUDE.md`

无新规范时输出"本次无新增规范"。

## 规则

- 同一消息要求"全项目扫描同步"、"全量检查"等合并为一条，不拆分
- 提取脚本静默运行，仅在发现新规范时输出变更摘要
- 定时运行建议：`/loop 每天 7:17 提炼规范并更新 CLAUDE.md`

## 输出示例

生成的 `.norm_digest.md` 结构：

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

## 完整消息正文

**1.** `[CORRECTION]` `[强度 3.0]` 不要按位置取数据…
```
