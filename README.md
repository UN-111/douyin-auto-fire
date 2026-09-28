# 抖音自动续火花

本仓库使用 [DouYinSparkFlow](https://github.com/2061360308/DouYinSparkFlow) 的
GitHub Actions 任务运行流，在固定的 CloakBrowser 指纹下向配置的抖音好友选择并发送抖音原生表情（默认“续火花”）。
迁移保留了 MIT 许可、CloakBrowser 和可选固定代理；旧的 `send.yml` 和邮件通知流已经移除，
仓库不包含阿里云函数部署入口。

## 工作流行为

工作流位于 `.github/workflows/schedule.yml`，每天北京时间 02:00 触发一次。为了避免
迁移后立即发送，定时任务只有在仓库 Actions 变量
`ENABLE_DOUYIN_SPARK_FLOW` **精确等于** `true` 时才会启动。变量未配置时，定时事件不会
运行任务。

手动运行时默认选择 `validate`。这个模式只检查仓库中的 `TASKS` 路由和对应 Cookie Secret 的 JSON、
指纹及目标列表，不安装浏览器，也不会发送消息。只有在已经配置好数据并明确选择 `send`，
同时 `ENABLE_DOUYIN_SPARK_FLOW=true` 时，工作流才会启动真实任务。

工作流固定使用以下 CloakBrowser 构建，并在下载后校验 SHA-256；升级时必须同时审查版本和
摘要：

```text
version: 146.0.7680.177.5
sha256: 4a12bcde95fa1bb1beef2b41ab5e5c27c36be78e3be3d0dac8c64d705216670e
```

## GitHub 配置

在仓库的 `Settings → Secrets and variables → Actions → Variables` 中配置可选的
`ENABLE_DOUYIN_SPARK_FLOW=true`；首次迁移请留空。这个仓库级变量在发送任务的门禁判断时可见。
然后在 `Settings → Environments` 创建环境 `user-data`，并在该环境的 `Variables` / `Secrets`
中配置下面的值。工作流会把两者合并到运行环境，Secret 的值不会写入仓库。

| 名称 | 类型 | 说明 |
| --- | --- | --- |
| `TASKS` | `config/github-actions.tasks.json` | 工作流加载的任务 JSON；包含固定指纹和目标，不包含 Cookie |
| `COOKIES_<unique_id>` | Secret | 必需；每个任务对应的 Cookie JSON 数组，名称按 `unique_id` 转大写 |
| `PROXY_ADDRESS` | Secret（可选） | 固定代理地址；留空时直连，配置后会传给 CloakBrowser |
| `MESSAGE_TEMPLATE` | Variable（可选） | 消息模板 |
| `DOUYIN_STREAK_STICKER` | 工作流固定值 | 原生表情名称：`续火花` |
| `INTER_RECIPIENT_DELAY_MIN_SECONDS` / `INTER_RECIPIENT_DELAY_MAX_SECONDS` | 工作流固定值 | 每个收件人后随机等待 3–8 秒 |
| `HITOKOTO_TYPES` | Variable（可选） | 一言类型 JSON 数组 |
| `DEBUG` / `LOG_LEVEL` | Variable（可选） | 调试和日志级别设置 |

已跟踪的任务路由位于 [`config/github-actions.tasks.json`](config/github-actions.tasks.json)。它使用
`unique_id` `601501187I3`，所以 Cookie Secret 必须名为 `COOKIES_601501187I3`，内容是
Cookie-Editor 导出的完整 JSON 数组。Cookie 只放在 GitHub Secret 中，不要写入任务配置、README、
Issue、日志或任何提交。
`unique_id` 应保持稳定，因为它决定 Cookie Secret 的名称；每个账号的 `fingerprint` 也应保持
稳定，以便后续运行复用同一浏览器指纹。

`PROXY_ADDRESS` 是可选的固定出口，例如由代理服务提供的完整地址。不要把代理用户名或密码
写进公开文件；如果代理地址包含凭据，也只放在 Secret 中，并让收集 Cookie 的浏览器使用同
一出口。

## 本地校验

不提供 Cookie 时可做无发送的导入和语法检查：

```bash
python -m py_compile main.py core/*.py utils/*.py
python -m pytest -q
```

真实任务入口是 `python main.py task`。它读取 `TASKS`、`COOKIES_<unique_id>`、
`PROXY_ADDRESS` 等环境变量；没有配置任务时不会启动浏览器。请只在确认账号和目标正确、
且明确需要发送时运行任务入口。

## 迁移说明

- 旧的 `.github/workflows/send.yml` 和仅用于旧工作流的 `notify-failure.yml` 已删除。
- 工作流任务路由由 `config/github-actions.tasks.json` 提供；Cookie 继续只由
  `COOKIES_601501187I3` Secret 提供。
- `test.yml` 保留，用于推送和 Pull Request 的现有测试。
- 新运行时位于 `core/` 和 `utils/`，入口为根目录 `main.py`；上游的函数计算部署文件
  没有迁入本仓库。
- 新运行时来自 [2061360308/DouYinSparkFlow](https://github.com/2061360308/DouYinSparkFlow)，
  其 MIT 版权归属已在 [LICENSE](LICENSE) 中保留。

## License

本项目采用 [MIT License](LICENSE)。
