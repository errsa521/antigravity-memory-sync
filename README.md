# Antigravity Rolling Memory Sync

参考 **HanaAgent** 滚动漏斗架构实现的 **Google Antigravity → Basic Memory** 自动长期记忆同步工具。

零外部第三方依赖（仅使用 Python 3 标准库），支持**工作区隔离与子代理过滤**、**发往外部 API 前本地正则脱敏**、**反流水账高信噪比编译**、**带 `trigger: always_on` 的新会话热记忆预热**，以及 Windows 计划任务离线自动补跑。

## 核心架构

```text
Antigravity brain/*/transcript.jsonl  (+ conversations/*.db 只读提取工作区路径)
   │  1) 自动跳过子代理内部调用与系统启动报错会话
   │  2) 可选 WORKSPACE_FILTER 按工作区隔离，防跨项目串台
   │  3) 本地正则脱敏 (sanitizer.py)：发往外部 API 前自动打码密钥/Token/手机号/邮箱
   ▼
state/raw-daily/YYYY-MM-DD.jsonl
   ├── 当日增量轮次 ──► basic-memory/daily/agy-today.md          (行号游标滚动合并，低价值闲聊返回 NONE 跳过)
   ├── 历史分块归档 ──► basic-memory/daily/agy-week.md           (最近 6 天滚动窗口，硬上限 2500 字，超长分块不砍中段)
   ├── 超出 6 天 ─────► basic-memory/profile/agy-longterm.md     (旧长期核心 + 过期日报覆盖式折叠压缩)
   └── 热记忆预热 ────► ~/.gemini/config/rules/agy-hot-memory.md (含 trigger: always_on，分段保底预算，绝不截丢长期核心)
```

### 关键特性
- **反流水账高信噪比 Prompt**：内置 `prompts.py` 明确要求合并往返、用新结论取代旧说法，只留「未完待办 / 已拍板决策与关键参数 / 踩坑结论 / 稳定偏好」，过滤寒暄和试探；低价值片段返回 `NONE` 不污染记忆。支持在 `prompts/` 下放 `today.md` / `daily.md` / `longterm.md` 自由覆盖提示词。
- **分段保底热记忆**：`hot_memory.py` 生成标准 `trigger: always_on` 规则文件，总字数锁死在 `1800` 字以内，优先为「长期沉淀核心 (`500` 字)」和「今日重点 (`600` 字)」保留保底预算，剩余预算分配给最近 `3` 天动态。
- **跨工作区隔离**：通过只读读取 `conversations/*.db` 解析每个会话绑定的工作区路径，配合 `.env` 的 `WORKSPACE_FILTER` 与 `HOT_MEMORY_PATH`（可写到单个项目的 `.agents/rules/`），支持全局模式或严格单工作区隔离模式。
- **本地前置脱敏**：`sanitizer.py` 在写盘与请求外部 API 之前，自动将 `sk-...`、`ghp_...`、`AIza...`、`AKIA...`、`Authorization: Bearer ...`、`password=...`、常见邮箱与 11 位手机号替换为 `[REDACTED_*]`。
- **离线自动补跑**：`daily-state.json` 记录逻辑日期与断点，配合 Windows 任务计划程序 `StartWhenAvailable=True` 实现关机错过后上线自动补跑；无新消息时零 API 调用秒退。

## 完整闭环部署指南

本工具负责**采集、脱敏、压缩与热记忆预热**，深度检索历史档案需配合 [Basic Memory](https://github.com/basicmachines-co/basic-memory) MCP 使用。

### 1. 安装并挂载 Basic Memory MCP（形成检索闭环）

```powershell
# 推荐使用 uv / pip 在独立环境安装 basic-memory
uv tool install basic-memory
# 或：pip install basic-memory
```

在 `~/.gemini/config/mcp_config.json` 中挂载（路径按本机实际 `basic-memory` 可执行文件位置调整）：

```json
{
  "mcpServers": {
    "basic-memory": {
      "command": "basic-memory",
      "args": ["mcp"],
      "env": {
        "PYTHONUTF8": "1",
        "PYTHONIOENCODING": "utf-8"
      }
    }
  }
}
```

### 2. 配置 `.env`

```powershell
Copy-Item .env.example .env
```

编辑 `.env`（已被 `.gitignore` 严格忽略）：

```ini
API_BASE_URL=https://your-relay-domain.com/v1
API_KEY=sk-your-api-key-here
API_MODEL=gemini-2.5-flash

# 若只想同步特定工作区，避免跨区串台（留空=全量）
# WORKSPACE_FILTER=E:/MyProject
# 若想把热记忆限定在单个工作区内，可改写为项目级规则路径：
# HOT_MEMORY_PATH=E:/MyProject/.agents/rules/agy-hot-memory.md
```

### 3. 验证与运行

```powershell
# 1. 不调 API，预览过滤统计与待编译消息量
python sync.py --dry-run

# 2. 测试 API 连通性
python sync.py --test-api

# 3. 执行同步（写入 Basic Memory 与热记忆规则文件）
python sync.py

# 4. 如修改了提示词想重编今日记忆
python sync.py --force-today
```

### 4. 注册 Windows 后台自动同步（可选）

```powershell
# 开机登录 + 每 60 分钟静默检查（pythonw.exe 无黑框，日志自动保留最近 500 行）
powershell -ExecutionPolicy Bypass -File .\install_task.ps1

# 卸载定时任务
powershell -ExecutionPolicy Bypass -File .\install_task.ps1 -Uninstall
```

## 目录结构

| 文件 | 职责 |
| :--- | :--- |
| `config.py` | 加载 `.env`、工作区过滤规则、热记忆预算与路径配置 |
| `sanitizer.py` | 发往外部 API 前的本地正则密钥与隐私脱敏器 |
| `extractor.py` | 读取 `conversations/*.db` 识别工作区、过滤子代理、游标增量抽取与顺序分块 |
| `prompts.py` | 内置反流水账高信噪比 Prompt，支持 `prompts/*.md` 外部覆写 |
| `llm_client.py` | 纯标准库 OpenAI 兼容 HTTP 客户端（带指数退避重试） |
| `compiler.py` | 状态机、分块滚动归档、6 天窗口与超期折叠引擎 |
| `hot_memory.py` | 带 `trigger: always_on` 与分段保底预算的热记忆规则生成器 |
| `sync.py` | CLI 主入口（`--dry-run`、`--test-api`、`--force-today`） |
| `install_task.ps1` | Windows 任务计划程序注册与卸载脚本 |

## License

MIT
