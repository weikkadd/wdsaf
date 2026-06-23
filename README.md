<div align="center">

# 🚀 Weirdhost Auto Renew

> 基于 GitHub Actions 的 Weirdhost 账号自动续期工具 · Cookie 模式 · 多账号 · Cloudflare 反检测 · 代理支持

![License](https://img.shields.io/badge/License-MIT-blue?style=flat-square)
![Platform](https://img.shields.io/badge/Platform-GitHub%20Actions-2088FF?style=flat-square&logo=githubactions&logoColor=white)
![Language](https://img.shields.io/badge/Made%20with-Python-3776AB?style=flat-square&logo=python&logoColor=white)
![Status](https://img.shields.io/badge/Status-Stable-brightgreen?style=flat-square)
![Proxy](https://img.shields.io/badge/Proxy-VLESS%7CVMess%7CTrojan%7CSS%7CSOCKS5-9C27B0?style=flat-square)
![Schedule](https://img.shields.io/badge/Runs%20Daily-UTC%2000%3A00-FF6F00?style=flat-square)
![Last Commit](https://img.shields.io/github/last-commit/weikkadd/wdsaf?style=flat-square)

</div>

---

## ✨ 项目特点速览

| # | 特性 | 说明 |
|:---:|:---|:---|
| 🧩 | **无需抓包接口** | 直接复用浏览器 Cookie，绕过接口逆向 |
| 🍪 | **Cookie 模式稳定** | 与 Weirdhost 官方登录态一致，长期可用 |
| 👥 | **支持多账号** | 通过 `WEIRDH0ST_COOKIE_N` 扩展至 N 个账号 |
| ⏰ | **GitHub Actions 自动运行** | 默认每天 UTC 00:00 定时触发 |
| 🔔 | **双通道通知** | Telegram Bot + Webhook 推送 |
| 🛡️ | **Cloudflare 反检测** | playwright-stealth + 真实浏览器指纹 + JS Challenge 等待 |
| 🌐 | **代理支持** | VLESS / VMess / Trojan / Shadowsocks / SOCKS5，绕过 GitHub IP 风控 |
| 🔒 | **安全存储** | 所有敏感凭据通过 GitHub Secrets 加密存储 |
| 🐛 | **调试友好** | 失败时自动保存截图 + HTML 作为 artifact |

---

## 📌 1. 项目用途

本项目用于：

- 🔐 自动登录 Weirdhost（基于 Cookie，无需账号密码）
- ♻️ 自动保持 Cookie 有效
- ⏰ 定时执行续期任务
- 👥 支持多账号（最多 50 个，按 `WEIRDH0ST_COOKIE_N` 顺序读取）
- 📢 支持通知推送（Telegram + Webhook 双通道）
- 🌐 支持代理出网（绕过 Cloudflare 对 GitHub IP 的风控）

---

## 🌐 2. 环境准备

| # | 资源 | 用途 |
|:---:|:---|:---|
| 1️⃣ | **GitHub 账号** | 托管代码与运行 GitHub Actions |
| 2️⃣ | **Weirdhost 账号** | 续期目标账号 |
| 3️⃣ | **浏览器** | 用于获取 Cookie（Chrome / Edge / Firefox 均可） |
| 4️⃣ | **代理节点**（可选但推荐） | 用于绕过 Cloudflare 对 GitHub Actions IP 的风控 |

---

## 📦 3. 一键部署流程

### ✅ Step 1：Fork 仓库

点击右上角 [**Fork**](https://github.com/weikkadd/wdsaf/fork)

### 🔐 Step 2：配置 Secrets

进入 **Settings** → **Secrets and variables** → **Actions** → **New repository secret**

### 🍪 Step 3：添加 Cookie（必须）

| Name | Value | 必填 |
|:---|:---|:---:|
| `WEIRDH0ST_COOKIE_1` | 账号 1 Cookie | ✅ |
| `WEIRDH0ST_COOKIE_2` | 账号 2 Cookie | 可选 |
| `WEIRDH0ST_COOKIE_3` | 账号 3 Cookie | 可选 |
| ... | 最多支持 50 个账号 | 可选 |

> ⚠️ 变量名大小写敏感：必须是 `WEIRDH0ST`（中间是数字 `0`，不是字母 `O`）

#### 📍 Cookie 获取方法

1. 打开 👉 <https://hub.weirdhost.xyz>
2. 登录账号
3. 按 `F12` → **Application** → **Cookies** → `https://hub.weirdhost.xyz`
4. 找到 `remember_web_xxx` 等关键 Cookie
5. 复制完整 Cookie 值

正确格式：`remember_web_xxx=eyJpdiI6...; session=abc123; XSRF-TOKEN=xyz789`

> 💡 建议直接从浏览器 **Network** 标签 → 任一请求的 **Request Headers** → `Cookie:` 字段复制完整值

### 🌐 Step 4：配置代理（**强烈推荐**）

由于 Cloudflare 对 GitHub Actions 的 Azure IP 段进行严格风控，**强烈建议配置代理节点**。配置后流量会通过你的代理出网，绕过 CF 拦截。

| Name | Value |
|:---|:---|
| `PROXY_NODE` | 代理节点的完整分享链接 |

支持以下任意一种协议：

#### 📋 支持的代理协议格式

<details>
<summary><b>VLESS（推荐 Reality）</b></summary>

```
vless://uuid@server:port?encryption=none&security=reality&sni=www.microsoft.com&fp=chrome&pbk=PUBLIC_KEY&sid=SHORT_ID&type=tcp#name
```

参数说明：
- `security=reality` - 使用 Reality 协议（最隐蔽）
- `sni` - 伪装 SNI
- `fp=chrome` - 浏览器指纹
- `pbk` - Reality 公钥
- `sid` - short ID
- `type=tcp` - 传输协议（支持 tcp / ws / grpc / httpupgrade）

</details>

<details>
<summary><b>VMess</b></summary>

```
vmess://base64(json)
```

JSON 内容：
```json
{
  "v": "2",
  "add": "server.com",
  "port": "443",
  "id": "uuid",
  "aid": "0",
  "net": "ws",
  "type": "none",
  "host": "server.com",
  "path": "/path",
  "tls": "tls",
  "sni": "server.com",
  "scy": "auto"
}
```

</details>

<details>
<summary><b>Trojan</b></summary>

```
trojan://password@server:port?sni=server.com&type=ws&path=/ws&host=server.com#name
```

</details>

<details>
<summary><b>Shadowsocks (SIP002)</b></summary>

```
ss://base64(method:password)@server:port#name
```

示例：`ss://YWVzLTI1Ni1nY206cGFzc3dvcmQ=@server.com:8388#myss`
（其中 `YWVzLTI1Ni1nY206cGFzc3dvcmQ=` 是 `aes-256-gcm:password` 的 base64）

</details>

<details>
<summary><b>SOCKS5</b></summary>

```
socks5://user:pass@server:port
socks5://server:port
```

</details>

> 💡 **代理节点推荐**：
> - **VPS 自建 VLESS+Reality** 最稳定（搬瓦工/Vultr/Hetzner）
> - **住宅代理**（如 Bright Data）成功率最高但成本高
> - **机场节点** 可能多人共享导致 IP 被风控
> - 不配置 `PROXY_NODE` 则直连，**很可能被 Cloudflare 拦截**

### 📢 Step 5：通知配置（可选但推荐）

#### 方式 A：Telegram 通知

| Name | Value |
|:---|:---|
| `TG_BOT_TOKEN` | 从 [@BotFather](https://t.me/BotFather) 获取 |
| `TG_CHAT_ID` | 从 [@userinfobot](https://t.me/userinfobot) 获取 |

#### 方式 B：Webhook 通知

| Name | Value |
|:---|:---|
| `WEBHOOK_URL` | 接收 POST JSON 的接口地址 |

### ▶️ Step 6：首次运行（必须）

进入 **Actions** → 找到 `auto-renew` → **Run workflow**

> ⚠️ Fork 仓库的 Actions 默认禁用，需手动启用 + 手动 Run 一次才能激活定时任务

---

## ⏰ 4. 自动运行时间

| 时区 | 触发时间 |
|:---|:---|
| 🌍 UTC | 每天 `00:00` |
| 🇨🇳 北京时间（UTC+8） | 每天 `08:00 AM` |

> 💡 GitHub 60 天无活动会自动禁用 schedule，建议偶尔手动 Run 一次

---

## 🔄 5. 运行流程说明

```mermaid
flowchart TD
    A[GitHub Actions 触发] --> B{配置了 PROXY_NODE?}
    B -->|是| C[下载 Xray-core]
    C --> D[解析代理协议<br/>生成 Xray 配置]
    D --> E[启动 Xray<br/>本地 1080 SOCKS5]
    E --> F[验证代理出网<br/>ipinfo.io]
    F --> G[启动 Playwright<br/>走 SOCKS5 代理]
    B -->|否| G2[启动 Playwright<br/>直连]
    G --> H[读取所有 Cookie]
    G2 --> H
    H --> I[遍历每个账号]
    I --> J[注入 Cookie + stealth]
    J --> K[访问首页 + CF 等待]
    K --> L[访问 dashboard 验证]
    L --> M[查找 Renew 按钮]
    M --> N{找到?}
    N -->|是| O[点击 Renew + 等待结果]
    N -->|否| P[保存调试截图<br/>返回失败]
    O --> Q[记录结果]
    P --> Q
    Q --> R{还有账号?}
    R -->|是| I
    R -->|否| S[发送汇总通知]
    S --> T[上传调试 artifact]
    T --> U[清理 Xray 进程]

    style A fill:#2088FF,stroke:#0F4C81,color:#fff
    style E fill:#9C27B0,stroke:#6A1B9A,color:#fff
    style O fill:#2EA043,stroke:#1A7F37,color:#fff
    style S fill:#FF6F00,stroke:#BF360C,color:#fff
```

---

## ⚠️ 6. 常见问题

<details>
<summary><b>❌ Cloudflare 拦截</b></summary>

**症状**：日志显示 `Cloudflare 拦截，未能通过验证`

**解决**：
1. **配置 `PROXY_NODE`**（最重要）—— 走代理出网绕过 GitHub IP 风控
2. 更换代理节点（机场节点可能被多人共享风控）
3. 用住宅代理（成功率最高）
4. 改用本地运行（家里电脑 + cron）
</details>

<details>
<summary><b>❌ 代理出网失败</b></summary>

**症状**：日志显示 `代理出网失败` 或 `本地 SOCKS5 代理未启动`

**排查**：
1. 查看 Actions artifacts 中的 `xray.log` —— Xray 启动错误会在这里
2. 检查 `PROXY_NODE` 格式是否正确（参考上方协议格式说明）
3. 在本地用 `xray run -config config.json` 测试代理是否能启动
4. 确认代理节点本身可用（用 v2rayN / Clash 客户端测试）
</details>

<details>
<summary><b>❌ Cookie 失效</b></summary>

**症状**：`Cookie 已失效，请重新登录获取最新 Cookie`

**解决**：重新登录 <https://hub.weirdhost.xyz> 获取最新 Cookie，更新 Secrets
</details>

<details>
<summary><b>❌ Actions 不执行</b></summary>

- ✅ 是否在 Actions 页面启用 workflows
- ✅ 是否手动 Run 过一次（Fork 仓库 schedule 需先手动激活）
- ✅ 60 天内是否有活动（push / manual run）
</details>

<details>
<summary><b>❌ 多账号不生效</b></summary>

- ✅ 变量名严格为 `WEIRDH0ST_COOKIE_1`、`WEIRDH0ST_COOKIE_2`...
- ✅ 中间是数字 `0`，不是字母 `O`
- ✅ 数字必须从 1 开始连续（如缺 `_2` 则只读 `_1`）
</details>

<details>
<summary><b>❌ 找不到 Renew 按钮</b></summary>

**症状**：`未发现 Renew 按钮`

**说明**：Weirdhost 的续期按钮通常只在临近到期时出现。如果当前账号未到续期窗口，dashboard 上不会有按钮。

**排查**：下载 Actions artifacts 中的 `no_renew_button.png` 截图查看 dashboard 实际内容
</details>

---

## 📊 7. 项目特点

✔ 无需抓包接口
✔ Cookie 模式稳定
✔ 支持多账号（最多 50 个）
✔ GitHub Actions 自动运行
✔ Telegram + Webhook 双通道通知
✔ Cloudflare 拦截检测 + 反检测
✔ **5 种代理协议支持（VLESS/VMess/Trojan/SS/SOCKS5）**
✔ 完整异常处理与资源回收
✔ 调试 artifact 自动上传

---

## 🧠 8. 架构说明

```
┌─────────────────────────────────────────┐
│         GitHub Actions Runner           │
│                                         │
│  ┌─────────────────────────────────┐   │
│  │  1. checkout 代码               │   │
│  │  2. 安装 Python + Playwright    │   │
│  │  3. 下载 Xray-core 二进制       │   │
│  │  4. 解析 PROXY_NODE 协议        │   │
│  │  5. 生成 Xray client 配置       │   │
│  │  6. 启动 Xray (本地 SOCKS5)     │   │
│  │  7. 验证代理出网 (ipinfo.io)    │   │
│  │  8. 运行 renew.py               │   │
│  │  9. 上传调试 artifact            │   │
│  │ 10. 清理 Xray 进程              │   │
│  └─────────────────────────────────┘   │
└─────────────────────────────────────────┘
                  │
                  │ SOCKS5 127.0.0.1:1080
                  ▼
         ┌────────────────┐
         │   Xray-core    │
         │ (VLESS/VMess/  │
         │  Trojan/SS/    │
         │  SOCKS5)       │
         └────────────────┘
                  │
                  │ TLS/Reality/WebSocket
                  ▼
         ┌────────────────┐
         │  你的代理服务器 │
         │  (干净 IP)     │
         └────────────────┘
                  │
                  ▼
         ┌────────────────┐
         │ hub.weirdhost  │
         │    .xyz        │
         │  (CF 不拦)     │
         └────────────────┘
```

---

## 📁 9. 项目结构

```
weirdhost-auto-renew/
├── .github/
│   └── workflows/
│       └── renew.yml          # GitHub Actions 定时任务（含 Xray 启动）
├── proxy_parser.py            # 代理协议解析器（VLESS/VMess/Trojan/SS/SOCKS5）
├── renew.py                   # 续期主脚本（含 stealth + CF 等待 + 代理）
├── requirements.txt           # Python 依赖
└── README.md                  # 项目文档
```

---

## 🔧 10. 本地调试

```bash
# 1. 安装依赖
pip install -r requirements.txt
playwright install chromium

# 2. (可选) 本地启动 Xray
# 下载 Xray-core: https://github.com/XTLS/Xray-core/releases
# 用 proxy_parser.py 生成配置:
python -c "
from proxy_parser import parse_proxy, build_xray_config
import json
outbound = parse_proxy('vless://...你的节点...')
cfg = build_xray_config(outbound)
open('/tmp/xray.json','w').write(json.dumps(cfg, indent=2))
"
xray run -config /tmp/xray.json &

# 3. 设置环境变量
export WEIRDH0ST_COOKIE_1="your_cookie_here"
export PROXY_NODE="vless://..."   # 可选
export TG_BOT_TOKEN="..."          # 可选
export TG_CHAT_ID="..."            # 可选

# 4. 运行
python renew.py
```

---

## 📌 11. 安全提示

> ⚠️ **Cookie / PROXY_NODE 属于敏感信息，请勿泄露**
> 任何持有你 Cookie 的人都可以完全控制你的 Weirdhost 账号

> 🔒 **建议使用 Private 仓库**

> 🔄 **定期更新 Cookie**（建议每 30 天）

> 🚫 **不要在 Issues / PR 中粘贴 Cookie 或代理链接**

---

## 📄 License

本项目基于 **MIT License** 开源。

---

<div align="center">

**如果这个项目对你有帮助，请点一个 ⭐ Star 支持一下！**

Made with ❤️ for the open-source community

</div>
