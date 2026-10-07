# GitHub Actions 每日续火花

GitHub Actions 自行执行，不依赖 Codex 工作区、模型、定时对话或 computer use。

- 调度：`.github/workflows/schedule.yml`，`30 19 * * *`，即北京时间每日 03:30。GitHub 调度可能排队延迟，不承诺秒级准时。
- 每次检出仓库，安装 Python 3.12、固定依赖及 SHA-256 校验的 CloakBrowser 146.0.7680.177.5。
- 使用已有 `user-data` 环境中的 `COOKIES_601501187I3`。凭据为空、失效或要求身份验证时失败并通知，不绕过验证。
- 标准入口仍为 `main.py task`。配置的五名目标、续火花原生表情、发送间隔和回执校验均保持原样。
- `scripts/remote_workflow.py` 用远程 `douyin-daily-YYYY-MM-DD` 标签原子占用当日运行权。只在发送前创建；同一天不会重复发送。不要删除标签来重试结果不明的发送。
- 脱敏结果保存在 `.codex/results/`，便于跨全新 runner 检查状态。此仓库公开，所以只发布日期、计数、退出码和运行链接；不上传日志、聊天截图、Cookie 或消息正文。
- 失败邮件使用已有 QQ SMTP 凭据，发至 `x.rover.studio@gmail.com`，抄送 `xhy.09613@qq.com`。邮件不含凭据。
- 手动运行默认是 `probe`，不发送；只有明确选择 `send` 才发送。探针不会占用每日发送权。
- 旧 Codex 定时任务已暂停，旧 02:00 GitHub cron 已由本工作流替换，避免多路调度。

部署后必须完成 GitHub 上的 probe 实测。离线测试通过不代表远程 Cookie 有效。

现有标准回执校验曾出现未确认；保留严格判定，避免把 HTTP 200 或本地气泡当成可靠送达。若再次发生，邮件报告失败/未确认，不自动重发。
