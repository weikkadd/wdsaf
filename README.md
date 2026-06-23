📌 1. 项目用途

本项目用于：

自动登录 Weirdhost
自动保持 Cookie 有效
定时执行续期任务
支持多账号
支持通知推送（Telegram / Webhook）
🌐 2. 环境准备

你只需要：

GitHub 账号
Weirdhost 账号
浏览器（用于获取 Cookie）
📦 3. 一键部署流程
✅ Step 1：Fork 仓库

点击右上角：

👉 Fork

或创建自己的仓库并上传代码

🔐 Step 2：配置 Secrets（必须）

进入：

👉 Settings → Secrets and variables → Actions

点击：

👉 New repository secret

🍪 Step 3：添加 Cookie（核心）
Name	Value
WEIRDH0ST_COOKIE_1	账号1 Cookie
WEIRDH0ST_COOKIE_2	账号2 Cookie（可选）
WEIRDH0ST_COOKIE_3	账号3 Cookie（可选）
📍 Cookie 获取方法
打开：https://hub.weirdhost.xyz
登录账号
按 F12 → Application
找到：
remember_web_xxx
复制完整 Cookie 值
粘贴到 GitHub Secrets
📢 Step 4：通知配置（可选）
Telegram 通知
Name	Value
TG_BOT_TOKEN	Bot Token
TG_CHAT_ID	用户 Chat ID
▶️ Step 5：首次运行（必须）

进入：

👉 Actions

找到 workflow：

auto-renew

点击：

👉 Run workflow

⏰ 4. 自动运行时间

默认定时：

每天 UTC 00:00

换算北京时间：

每天 08:00 AM
🔄 5. 运行流程说明

系统运行逻辑如下：

GitHub Actions 触发
        ↓
读取 Cookie
        ↓
访问 Weirdhost
        ↓
执行续期操作
        ↓
发送通知（可选）
⚠️ 6. 常见问题
❌ Cookie 失效

👉 重新登录获取最新 Cookie

❌ Actions 不执行

检查：

是否启用 GitHub Actions
是否手动运行过一次 workflow
❌ 多账号不生效

确认：

WEIRDH0ST_COOKIE_1
WEIRDH0ST_COOKIE_2

是否正确填写

❌ 没有通知

检查：

TG_BOT_TOKEN 是否正确
TG_CHAT_ID 是否正确
📊 7. 项目特点

✔ 无需抓包接口
✔ Cookie 模式稳定
✔ 支持多账号
✔ GitHub Actions 自动运行
✔ 可扩展通知系统

🧠 8. 架构说明
Cookie 存储
    ↓
GitHub Actions 定时触发
    ↓
执行续期脚本
    ↓
更新状态 + 通知
📌 9. 安全提示
Cookie 属于敏感信息，请勿泄露
建议使用 Private 仓库
定期更新 Cookie
