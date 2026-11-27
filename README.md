# Antigravity Rolling Memory Sync

参考 **HanaAgent** 记忆架构实现的 **Google Antigravity → Basic Memory** 自动滚动记忆同步工具。

零外部第三方依赖（仅使用 Python 3 标准库），支持增量解析 Antigravity 本地会话日志、任意 OpenAI 兼容 API（含中转站）摘要编译、固定容量滚动窗口防膨胀，以及 Windows 计划任务离线自动补跑。

## 核心机制（复刻 HanaAgent 四层防膨胀架构）

```text
Antigravity transcript.jsonl
   │  (行号游标增量抽取 + 清洗工具噪音与系统标签)
   ▼
state/raw-daily/YYYY-MM-DD.jsonl
   ├── 当日新增 ──► basic-memory/daily/agy-today.md       (SHA-256 指纹防重，跨日自动重置)
   ├── 历史归档 ──► basic-memory/daily/agy-week.md        (最近 6 天滚动窗口，硬上限 2500 字)
   └── 超出 6 天 ──► basic-memory/profile/agy-longterm.md (旧长期画像 + 过期日报折叠重写，定长防膨胀)
```

- **绝对防膨胀**：不是只追加不清理。`agy-today.md` 每日重置，`agy-week.md` 锁定最近 `6` 天且不超过 `2500` 字；超期日报自动折叠重写进 `agy-longterm.md`，仅在写盘成功后移入 `state/folded-archive/`，失败自动留待下轮重试。
- **离线自动补跑**：`daily-state.json` 按逻辑日期记录各步骤完成状态，配合 Windows 任务计划程序 `StartWhenAvailable=True` 与开机/每小时双触发器，关机错过后上线即自动补跑；已完成当日步骤则秒退不花额度。
- **独立文件防冲突**：默认写入 `agy-today.md`、`agy-week.md`、`agy-longterm.md`，不覆盖原有手动维护或从其他 Agent 同步来的 Basic Memory 笔记。

## 快速开始

### 1. 环境要求
- Python 3.10+（无需 `pip install` 任何第三方包）
- 本机已安装 Google Antigravity 与 [Basic Memory](https://github.com/basicmachines-co/basic-memory)
- 一个支持 OpenAI `/v1/chat/completions` 协议的 API 接口（官方或中转站均可，推荐轻量便宜模型）

### 2. 配置 `.env`
复制配置模板并填入你的 API 参数（`.env` 已被 `.gitignore` 严格忽略）：

```powershell
Copy-Item .env.example .env
```

编辑 `.env`：

```ini
API_BASE_URL=https://your-relay-domain.com/v1
API_KEY=sk-your-api-key-here
API_MODEL=gemini-2.5-flash

# 可选：自定义角色标签与关联笔记
USER_LABEL=User
ASSISTANT_LABEL=Assistant
PROFILE_NOTE_LINK=
```

### 3. 验证与首次运行

```powershell
# 1. 不调 API，预览本地扫描到的会话与每日消息量
python sync.py --dry-run

# 2. 测试 API 连通性
python sync.py --test-api

# 3. 执行同步并写入 Basic Memory
python sync.py
```

### 4. 注册 Windows 后台自动同步（可选）

```powershell
# 注册开机登录 + 每 60 分钟自动检查（使用 pythonw.exe 无黑框静默运行）
powershell -ExecutionPolicy Bypass -File .\install_task.ps1

# 卸载定时任务
powershell -ExecutionPolicy Bypass -File .\install_task.ps1 -Uninstall
```

运行日志位于 `state/sync.log`（自动滚动截断，最多保留最近 500 行）。

## 目录结构

| 文件 | 职责 |
| :--- | :--- |
| `config.py` | 读取 `.env` 与默认路径、滚动窗口阈值 |
| `extractor.py` | 游标增量扫描 `transcript.jsonl`，剥离系统标签并按日期分桶 |
| `llm_client.py` | 纯标准库 OpenAI 兼容 HTTP 客户端（带重试） |
| `prompts.py` | 今日动态 / 每日归档 / 长期折叠三套编译提示词 |
| `compiler.py` | 状态机、指纹去重、6 天窗口拼装与超期折叠引擎 |
| `sync.py` | CLI 主入口（`--dry-run`、`--test-api`、`--force-today`） |
| `install_task.ps1` | Windows 任务计划程序注册与卸载脚本 |

## License

MIT
