# 抖音自动续火花

本仓库使用 [DouYinSparkFlow](https://github.com/2061360308/DouYinSparkFlow) 的当前运行时，
在固定的 CloakBrowser 指纹下向配置的抖音好友发送抖音原生表情“续火花”。入口是
`python main.py task`，当前实现位于 `core/` 和 `utils/`。

## 工作流行为

`.github/workflows/schedule.yml` 每天北京时间 02:00（`0 18 * * *` UTC）触发。定时任务只有在
仓库 Actions 变量 `ENABLE_DOUYIN_SPARK_FLOW` 精确等于 `true` 时才会发送；变量未配置或为其他值时，
发送作业保持跳过。

手动运行默认选择 `validate`。它只检查任务路由、动态 Cookie Secret、指纹和目标列表，不安装浏览器，
也不发送消息。选择 `send` 且门禁变量为 `true` 后才会运行真实任务。

当前路由位于 [`config/github-actions.tasks.json`](config/github-actions.tasks.json)：账号和唯一标识是
`601501187I3`，固定指纹是 `douyin-601501187I3`，目标为五位好友。运行时只发送原生“续火花”表情，
每位收件人之间随机等待 3–8 秒，并支持可选 `PROXY_ADDRESS`。

表情面板打开后等待3秒，切换分类后再次等待3秒，再精确匹配“续火花”并验证目标图片已加载。
笑脸形状的按钮只是打开表情面板的入口，发送目标是面板内的原生火花贴纸。未找到或图片未加载时失败退出。
`probe` 模式只检查，不点击贴纸、不发送；成功时保存面板及目标图片截图，失败时保存可用的阻塞页面截图。

## 固定出口代理

在仓库 `Settings → Environments → user-data → Environment secrets` 中新增 `PROXY_ADDRESS`。
例如 `http://用户名:密码@代理主机:端口`。登录并获取 Cookie 时也应使用同一固定出口，
再更新该环境的 `COOKIES_601501187I3`。代理凭据只放在 Secret 中。

当前未配置固定代理时使用 GitHub 托管运行器直连，出口 IP 会变化。固定 fingerprint、浏览器版本
和 `humanize=True` 不会固定 IP，也不保证抖音登录状态永久有效。当前上下文每次新建，注入 Cookie，
没有跨运行持久保存完整浏览器 profile 或 localStorage。

`.github/workflows/verify.yml` 提供不发送检查：比较相同 fingerprint 在两个新进程中的实际输出，
再用另一个种子作对照，并执行登录与已加载贴纸探针。不要将单元测试通过当作真实发送成功。

2026年10月1日的不发送实测中，`douyin-601501187I3` 在两个新进程的可测浏览器输出一致，
不同种子的 Canvas 哈希不同。该次任务仍被抖音登录弹窗挡住，未进入贴纸面板、未发送消息。
这只验证已测属性的稳定性，不代表所有指纹特征或账号风控均通过。

工作流固定使用以下 CloakBrowser 构建，并在下载后校验 SHA-256：

```text
version: 146.0.7680.177.5
sha256: 4a12bcde95fa1bb1beef2b41ab5e5c27c36be78e3be3d0dac8c64d705216670e
```

## GitHub 配置

在 `Settings → Secrets and variables → Actions → Variables` 中配置可选的
`ENABLE_DOUYIN_SPARK_FLOW=true`。在环境 `user-data` 中保留以下现有名称；只把值放进 GitHub 配置，
不要提交到仓库：

| 名称 | 类型 | 说明 |
| --- | --- | --- |
| `COOKIES_601501187I3` | Secret | 必需；Cookie-Editor 导出的 JSON 数组 |
| `PROXY_ADDRESS` | Secret（可选） | CloakBrowser 的固定代理地址 |
| `QQ_SMTP_USERNAME` | Secret | 失败通知的 QQ 邮箱账号 |
| `QQ_SMTP_AUTH_CODE` | Secret | 失败通知的 QQ 邮箱授权码 |
| `ALERT_EMAIL_TO` | Secret | 失败通知的收件人 |
| `ALERT_EMAIL_CC` | Secret（可选） | 失败通知的抄送人 |

`email-on-failure` 只在 `send` 作业结果为 `failure` 时运行。配置校验成功、门禁关闭导致发送跳过、
或其他跳过状态都不会发邮件。通知使用 Python 标准库通过 QQ Mail SMTP 发送，日志和邮件正文不会输出
Cookie 或 SMTP Secret 值。

## 保留文件与本地校验

仓库保留 `main.py`、`core/`、`utils/`、当前工作流、当前配置、`tests/`、许可证和依赖配置；旧的
多账号入口、部署脚本、旧配置样例与未使用的通知/消息模块已移除。

本地可运行：

```bash
python -m py_compile main.py core/*.py utils/*.py
python -m pytest -q
```

不提供 Cookie 时，入口不会启动浏览器。真实任务入口会读取 `TASKS`、
`COOKIES_<unique_id>` 和 `PROXY_ADDRESS` 环境变量。

## License

本项目采用 [MIT License](LICENSE)。
