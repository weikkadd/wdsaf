#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Weirdhost Auto Renew (v6 - nodriver + CF Bypass)
- 用 nodriver（基于真实 Chrome，无 CDP 痕迹）替代 Playwright
- 支持 VLESS/VMess/Trojan/SS/SOCKS5/Hysteria2/TUIC 代理（通过 PROXY_NODE 环境变量）
- Cookie 模式登录 + 多账号 + TG/Webhook 通知
- Cloudflare 反检测：nodriver 默认 navigator.webdriver=False
"""

import os
import sys
import asyncio
import json
import time
import traceback
import socket

import requests
import nodriver as uc
import nodriver.cdp.network as network

from proxy_parser import parse_proxy, get_proxy_protocol, build_singbox_config

# ===== 配置 =====
BASE_URL = "https://hub.weirdhost.xyz"
LOGIN_URL = f"{BASE_URL}/login"

# 候选 dashboard 路径（weirdhost 不同语言版本可能不同）
DASHBOARD_CANDIDATES = [
    "/servers",         # 服务器列表（韩语「서버」标签）
    "/",                # 首页（Pterodactyl 默认就是服务器列表）
    "/index",
    "/dashboard",
    "/server",          # 单数形式
    "/home",
    "/panel",
    "/account",         # 账号设置页（最后兜底，从这里找服务器链接）
    "/kr/dashboard",
    "/ko/dashboard",
]

MAX_COOKIE_SLOTS = 50
DEBUG_DIR = "/tmp/weirdhost_debug"

LOCAL_SOCKS_PORT = int(os.getenv("LOCAL_SOCKS_PORT", "1080"))

# ===== 通知配置 =====
TG_BOT_TOKEN = os.getenv("TG_BOT_TOKEN", "").strip()
TG_CHAT_ID = os.getenv("TG_CHAT_ID", "").strip()
WEBHOOK_URL = os.getenv("WEBHOOK_URL", "").strip()

# ===== 代理配置 =====
PROXY_NODE = os.getenv("PROXY_NODE", "").strip()

# Cloudflare 拦截检测关键词
CF_SIGNALS = [
    "just a moment",
    "checking your browser",
    "verifying you are human",
    "cf-challenge",
    "cf_chl_opt",
    "attention required",
    "enable javascript and cookies",
    "正在进行安全验证",
    "请启用 javascript",
    "浏览器扩展或网络配置不兼容",
    "阻止",
    "安全验证过程",
]

# CF Challenge 等待时间（nodriver 应该比 Playwright 更容易过，给 90 秒）
CF_WAIT_MAX_SEC = 90
CF_WAIT_INTERVAL = 3

# Renew 按钮选择器（全面，含韩语/英语/中文/日语）
RENEW_SELECTORS_TEXT = [
    # 英语
    "Renew",
    "renew",
    "RENEW",
    "Renew Now",
    "Renewal",
    "Extend",
    "Activate",
    # 中文
    "续期",
    "续",
    "延期",
    "激活",
    # 韩语
    "연장하기",          # "续期"（最常用）
    "연장",              # "延长"
    "갱신",              # "更新"
    "갱신하기",          # "更新"
    "기간 연장",         # "期间延长"
    # 日语（以防万一）
    "更新",
    "延長",
    "延長する",
]


def notify(title: str, content: str) -> None:
    """旧版通用通知（保留兼容）"""
    payload = {"title": title, "content": content, "ts": int(time.time())}

    if TG_BOT_TOKEN and TG_CHAT_ID:
        try:
            text = f"*[{title}]*\n\n{content}"
            r = requests.post(
                f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage",
                json={
                    "chat_id": TG_CHAT_ID,
                    "text": text,
                    "parse_mode": "Markdown",
                    "disable_web_page_preview": True,
                },
                timeout=15,
            )
            if r.status_code != 200:
                requests.post(
                    f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage",
                    json={"chat_id": TG_CHAT_ID, "text": f"[{title}]\n\n{content}"},
                    timeout=15,
                )
        except Exception as e:
            print(f"[notify] Telegram 发送失败: {e}", file=sys.stderr)

    if WEBHOOK_URL:
        try:
            requests.post(WEBHOOK_URL, json=payload, timeout=15)
        except Exception as e:
            print(f"[notify] Webhook 发送失败: {e}", file=sys.stderr)


# ===== 新版简洁通知 =====
# 格式参考：
#   🎮 weirdhost.xyz 续期通知
#   🕐 运行时间: 2026-06-23 04:54:59
#   🖥 服务器: weirdhost.xyzKR
#   📅 利用期限: 2026-06-25
#   📊 续期结果: ✅ 续期成功！

# 续期窗口：剩余天数 ≤ 此值时才真正点续期按钮
RENEW_THRESHOLD_DAYS = int(os.getenv("RENEW_THRESHOLD_DAYS", "3"))


def notify_renew(server_id: str, server_name: str, expiry_date: str, result: str, account_idx: int = 1) -> None:
    """发送用户期望格式的简洁续期通知（每个服务器一条）

    标题用自动识别的服务器 ID（从 /account 页面扫描所有 /server/{id} 链接得到）。

    格式：
        🎮 9120ade0 续期通知
        🕐 运行时间: 2026-06-23 19:19:39
        🖥 服务器: Weirdhost|KR
        📅 利用期限: 2026-07-07 01:58:01
        📊 续期结果: ⏳ 还需 7 天才能续期
    """
    # 运行时间（北京时间）
    from datetime import datetime, timezone, timedelta
    tz_cn = timezone(timedelta(hours=8))
    run_time = datetime.now(tz_cn).strftime("%Y-%m-%d %H:%M:%S")

    text = (
        f"🎮 {server_id} 续期通知\n"
        f"🕐 运行时间: {run_time}\n"
        f"🖥 服务器: {server_name}\n"
        f"📅 利用期限: {expiry_date}\n"
        f"📊 续期结果: {result}"
    )

    if TG_BOT_TOKEN and TG_CHAT_ID:
        try:
            requests.post(
                f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage",
                json={
                    "chat_id": TG_CHAT_ID,
                    "text": text,
                    "disable_web_page_preview": True,
                },
                timeout=15,
            )
        except Exception as e:
            print(f"[notify_renew] Telegram 发送失败: {e}", file=sys.stderr)

    if WEBHOOK_URL:
        try:
            requests.post(WEBHOOK_URL, json={
                "text": text,
                "server_id": server_id,
                "server": server_name,
                "expiry_date": expiry_date,
                "result": result,
                "account_idx": account_idx,
                "ts": int(time.time()),
            }, timeout=15)
        except Exception as e:
            print(f"[notify_renew] Webhook 发送失败: {e}", file=sys.stderr)


def parse_expiry_date(date_str: str):
    """解析日期字符串，返回 datetime.date 对象；失败返回 None"""
    if not date_str:
        return None
    from datetime import datetime
    import re
    # 清理字符串
    s = date_str.strip()
    # 尝试常见格式
    formats = [
        "%Y-%m-%d",
        "%Y/%m/%d",
        "%Y.%m.%d",
        "%Y年%m月%d日",
        "%m/%d/%Y",
        "%d/%m/%Y",
        "%B %d, %Y",     # June 25, 2026
        "%b %d, %Y",     # Jun 25, 2026
    ]
    for fmt in formats:
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    # 尝试正则提取 YYYY-MM-DD
    m = re.search(r'(\d{4})[-./年](\d{1,2})[-./月](\d{1,2})', s)
    if m:
        try:
            return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3))).date()
        except ValueError:
            pass
    return None


def days_until_expiry(expiry_date_str: str):
    """计算距离到期还有多少天；解析失败返回 None"""
    from datetime import date
    d = parse_expiry_date(expiry_date_str)
    if not d:
        return None
    return (d - date.today()).days


async def extract_server_info(page):
    """从 dashboard 页面提取服务器名 + 利用期限 + 服务器详情页 URL + 名字映射（韩语/英语兼容）"""
    info = {
        "server_name": "Weirdhost",
        "expiry_date": "",
        "server_urls": [],   # 服务器详情页 URL 列表
        "server_names": {},  # {server_id: server_display_name} 映射
    }
    try:
        # 注意：不要用 f-string，否则 [href 会被误解析
        js = r"""
            (function() {
                const body = document.body.innerText || '';
                // 找服务器名（标题或品牌）
                let serverName = 'Weirdhost';
                const titleMatch = body.match(/(Weirdhost\s*\|\s*\w+)/i);
                if (titleMatch) serverName = titleMatch[1].replace(/\s+/g, '');
                // 找利用期限/到期日（韩语/英语/中文/日语）— 严格模式，排除 created_at/updated_at
                let expiry = '';
                const patterns = [
                    /(?:유통기한|유통\s*기한|예측기간|예측\s*기간|이용기한|이용\s*기한|만료일|만료\s*일)\s*[:：]?\s*(20\d{2}[-./](?:0?[1-9]|1[0-2])[-./](?:0?[1-9]|[12]\d|3[01]))/,
                    /(?:利用期限|利用\s*期限|到期日?|有効期限)\s*[:：]?\s*(20\d{2}[-./](?:0?[1-9]|1[0-2])[-./](?:0?[1-9]|[12]\d|3[01]))/,
                    /(?:expir(?:y|ation)(?:\s*date)?|expires\s*on)\s*[:：]?\s*(20\d{2}[-./](?:0?[1-9]|1[0-2])[-./](?:0?[1-9]|[12]\d|3[01]))/i,
                    /(20\d{2}[-./](?:0?[1-9]|1[0-2])[-./](?:0?[1-9]|[12]\d|3[01]))\s*(?:까지|만료|到期)/,
                ];
                for (const p of patterns) {
                    const m = body.match(p);
                    if (m) { expiry = m[1]; break; }
                }
                // 提取所有 /server/{id} 链接 + 名字
                // weirdhost 列表里：服务器名和 ID 通常在同一个 <tr> 或 <div> 内
                const serverUrls = [];
                const serverNames = {};
                // 方式 1: 找所有 /server/{id} 链接，从链接文字或父元素提取名字
                const links = Array.from(document.querySelectorAll('a[href*="/server/"]'));
                const seen = new Set();
                for (const a of links) {
                    const href = a.href;
                    const m = href.match(/\/server\/([a-f0-9]+)/i);
                    if (!m) continue;
                    const sid = m[1];
                    if (seen.has(sid)) continue;
                    seen.add(sid);
                    serverUrls.push(href);
                    // 提取名字：先看链接本身文字，再看父级 <tr> 或卡片内的名字
                    let name = (a.innerText || '').trim();
                    if (!name || name === sid) {
                        // 找最近的 tr 或 div 容器
                        let parent = a.closest('tr') || a.closest('[class*="card"]') || a.closest('div');
                        if (parent) {
                            // 在容器内找看起来像服务器名的元素（不含 ID 和数字）
                            const candidates = parent.querySelectorAll('td, [class*="name"], [class*="title"], span, div');
                            for (const c of candidates) {
                                const t = (c.innerText || '').trim();
                                // 排除：纯 ID、含数字百分比、空字符串
                                if (t && t !== sid && !/^[a-f0-9]{8,}$/i.test(t) && !/\d+\s*%/.test(t) && t.length < 50) {
                                    name = t;
                                    break;
                                }
                            }
                        }
                    }
                    serverNames[sid] = name || sid;
                }
                return JSON.stringify({serverName: serverName, expiry: expiry, serverUrls: serverUrls, serverNames: serverNames});
            })()
        """
        result = await page.evaluate(js, return_by_value=True)
        if isinstance(result, str):
            try:
                d = json.loads(result)
                if d.get("serverName"):
                    info["server_name"] = d["serverName"]
                if d.get("expiry"):
                    info["expiry_date"] = d["expiry"]
                if d.get("serverUrls"):
                    info["server_urls"] = d["serverUrls"]
                if d.get("serverNames"):
                    info["server_names"] = d["serverNames"]
            except Exception:
                pass
    except Exception as e:
        print(f"  [info] 提取服务器信息失败: {e}")
    return info


async def extract_server_detail_info(page):
    """从服务器详情页 /server/{id} 提取利用期限（韩语/英语/中文/日语）"""
    info = {"expiry_date": "", "renew_available": None, "renew_button_disabled": None}
    try:
        js = r"""
            (function() {
                const body = document.body.innerText || '';
                const html = document.documentElement.innerHTML || '';
                // 利用期限关键词（韩语/英语/中文/日语）
                // 注意：weirdhost 实际用的是「유통기한」（保质期）和「예측기간」（预测期间）
                let expiry = '';
                const patterns = [
                    // 1. 严格匹配：关键词 + 日期(可带时间)
                    /(?:유통기한|유통\s*기한|예측기간|예측\s*기간|이용기한|이용\s*기한|만료일|만료\s*일|연장\s*일|이용\s*기간)\s*[:：]?\s*(20\d{2}[-./](?:0?[1-9]|1[0-2])[-./](?:0?[1-9]|[12]\d|3[01])(?:\s+\d{1,2}:\d{2}(?::\d{2})?)?)/,
                    /(?:利用期限|利用\s*期限|到期日?|有効期限|延長\s*日|保质期|保存期)\s*[:：]?\s*(20\d{2}[-./](?:0?[1-9]|1[0-2])[-./](?:0?[1-9]|[12]\d|3[01])(?:\s+\d{1,2}:\d{2}(?::\d{2})?)?)/,
                    /(?:expir(?:y|ation)(?:\s*date)?|expires\s*on|valid\s*until)\s*[:：]?\s*(20\d{2}[-./](?:0?[1-9]|1[0-2])[-./](?:0?[1-9]|[12]\d|3[01])(?:\s+\d{1,2}:\d{2}(?::\d{2})?)?)/i,
                    // 2. 日期 + 까지/만료/到期 后缀
                    /(20\d{2}[-./](?:0?[1-9]|1[0-2])[-./](?:0?[1-9]|[12]\d|3[01])(?:\s+\d{1,2}:\d{2}(?::\d{2})?)?)\s*(?:까지|만료|到期)/,
                    // 3. 关键词后 40 字符内任意位置出现日期
                    /(?:유통기한|예측기간|이용기한|만료일|expir|到期|利用期限|有効期限|保质期)[^\d]{0,40}(20\d{2}[-./]\d{1,2}[-./]\d{1,2}(?:\s+\d{1,2}:\d{2}(?::\d{2})?)?)/i,
                ];
                for (const p of patterns) {
                    const m = body.match(p) || html.match(p);
                    if (m) { expiry = m[1]; break; }
                }

                // 检测续期按钮状态
                let renewAvailable = null;
                let renewDisabled = null;
                // 找含 '연장하기' / '연장' / 'renew' / 'extend' 文字的按钮
                const btns = Array.from(document.querySelectorAll('button, a, input[type="submit"], input[type="button"]'));
                for (const b of btns) {
                    const t = (b.innerText || b.value || '').trim().toLowerCase();
                    if (/연장|renew|extend|갱신/i.test(t)) {
                        renewAvailable = t;
                        renewDisabled = b.disabled === true || b.classList.contains('disabled') || b.getAttribute('aria-disabled') === 'true';
                        // 检查 CSS 是否禁用（cursor: not-allowed / opacity < 0.5）
                        const style = window.getComputedStyle(b);
                        if (style.cursor === 'not-allowed' || parseFloat(style.opacity) < 0.5) {
                            renewDisabled = true;
                        }
                        break;
                    }
                }

                // 检测「N일/N시간/N분 후에 연장할수있어요」/「N days until renewal」提示
                let waitingDays = null;
                let waitingHours = null;
                let waitingMinutes = null;
                const dayMatch = body.match(/(\d+)\s*일\s*후에\s*연장/) || body.match(/(\d+)\s*days?\s*(?:until|before)\s*(?:renew|extend)/i);
                if (dayMatch) waitingDays = parseInt(dayMatch[1]);
                const hourMatch = body.match(/(\d+)\s*시간\s*후에\s*연장/) || body.match(/(\d+)\s*hours?\s*(?:until|before)\s*(?:renew|extend)/i);
                if (hourMatch) waitingHours = parseInt(hourMatch[1]);
                const minMatch = body.match(/(\d+)\s*분\s*후에\s*연장/) || body.match(/(\d+)\s*minutes?\s*(?:until|before)\s*(?:renew|extend)/i);
                if (minMatch) waitingMinutes = parseInt(minMatch[1]);
                // 也匹配单纯出现 "15시간" / "3일" 这种简短形式（weirdhost 用法）
                if (waitingDays === null && waitingHours === null && waitingMinutes === null) {
                    const shortDay = body.match(/(\d+)\s*일\s*후/);
                    if (shortDay) waitingDays = parseInt(shortDay[1]);
                    const shortHour = body.match(/(\d+)\s*시간/);
                    if (shortHour) waitingHours = parseInt(shortHour[1]);
                    const shortMin = body.match(/(\d+)\s*분/);
                    if (shortMin) waitingMinutes = parseInt(shortMin[1]);
                }

                return JSON.stringify({
                    expiry: expiry,
                    renewAvailable: renewAvailable,
                    renewDisabled: renewDisabled,
                    waitingDays: waitingDays,
                    waitingHours: waitingHours,
                    waitingMinutes: waitingMinutes
                });
            })()
        """
        result = await page.evaluate(js, return_by_value=True)
        if isinstance(result, str):
            try:
                d = json.loads(result)
                if d.get("expiry"):
                    info["expiry_date"] = d["expiry"]
                info["renew_available"] = d.get("renewAvailable")
                info["renew_button_disabled"] = d.get("renewDisabled")
                info["waiting_days"] = d.get("waitingDays")
                info["waiting_hours"] = d.get("waitingHours")
                info["waiting_minutes"] = d.get("waitingMinutes")
            except Exception:
                pass
    except Exception as e:
        print(f"  [info] 提取服务器详情信息失败: {e}")
    return info


def load_cookies() -> list:
    cookies = []
    for i in range(1, MAX_COOKIE_SLOTS + 1):
        v = os.getenv(f"WEIRDH0ST_COOKIE_{i}", "").strip()
        if v:
            cookies.append(v)
    return cookies


def parse_cookie_string(cookie_str: str) -> list:
    """把 'a=b; c=d' 解析为 nodriver CookieParam 列表"""
    cookies = []
    for pair in cookie_str.split(";"):
        pair = pair.strip()
        if not pair or "=" not in pair:
            continue
        name, value = pair.split("=", 1)
        cookies.append({
            "name": name.strip(),
            "value": value.strip(),
        })
    return cookies


async def is_cf_blocked(page) -> bool:
    """检测 Cloudflare 拦截"""
    try:
        content = await page.get_content()
        content_lower = content.lower()
    except Exception:
        return False

    strong_signals = [
        "just a moment",
        "checking your browser",
        "verifying you are human",
        "cf-challenge",
        "cf_chl_opt",
        "正在进行安全验证",
        "请启用 javascript",
        "enable javascript and cookies",
        "浏览器扩展或网络配置不兼容",
        "安全验证过程",
    ]
    for sig in strong_signals:
        if sig.lower() in content_lower:
            return True

    weak_signals = ["cloudflare", "ray id", "attention required"]
    weak_count = sum(1 for sig in weak_signals if sig in content_lower)
    if weak_count >= 2 and len(content) < 8000:
        return True

    return False


async def wait_for_cf_clearance(page, max_sec: int = CF_WAIT_MAX_SEC, stage: str = "") -> bool:
    """等待 CF Challenge 自动放行"""
    if not await is_cf_blocked(page):
        return True

    print(f"  [cf{stage}] 检测到 Cloudflare 挑战，等待自动放行（最多 {max_sec}s）...")
    elapsed = 0
    while elapsed < max_sec:
        await asyncio.sleep(CF_WAIT_INTERVAL)
        elapsed += CF_WAIT_INTERVAL
        if not await is_cf_blocked(page):
            print(f"  [cf{stage}] ✓ Cloudflare 已放行（耗时 {elapsed}s）")
            return True
        print(f"  [cf{stage}] 等待中... ({elapsed}s)")
    print(f"  [cf{stage}] ✗ Cloudflare 等待超时（{max_sec}s）")
    return False


async def is_logged_in(page) -> bool:
    try:
        url = page.url.lower()
    except Exception:
        return False
    if "/login" in url or "signin" in url:
        return False
    if "__cf_chl" in url:
        return False
    return True


async def save_debug_screenshot(page, name: str, account_idx: int):
    try:
        os.makedirs(DEBUG_DIR, exist_ok=True)
        path = f"{DEBUG_DIR}/account{account_idx}_{name}.png"
        await page.save_screenshot(path)
        print(f"  [debug] 截图: {path}")
        html_path = f"{DEBUG_DIR}/account{account_idx}_{name}.html"
        content = await page.get_content()
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(content)
        print(f"  [debug] HTML: {html_path}")
    except Exception as e:
        print(f"  [debug] 保存截图失败: {e}")


async def dump_page_elements(page, account_idx: int):
    try:
        print(f"  [debug] === 页面元素 dump ===")
        print(f"  [debug] URL: {page.url}")
        title = await page.evaluate("document.title")
        print(f"  [debug] Title: {title}")

        # 用 JS 提取所有按钮和链接（IIFE 格式，nodriver 兼容）
        elements = await page.evaluate("""
            (function() {
                const buttons = Array.from(document.querySelectorAll('button')).slice(0, 30).map((b, i) => ({
                    idx: i,
                    text: (b.innerText || '').trim().slice(0, 50),
                    id: b.id || '',
                    cls: (b.className || '').slice(0, 60),
                    disabled: b.disabled,
                }));
                const links = Array.from(document.querySelectorAll('a')).slice(0, 30).map((a, i) => ({
                    idx: i,
                    text: (a.innerText || '').trim().slice(0, 50),
                    href: (a.getAttribute('href') || '').slice(0, 80),
                }));
                const renewElems = Array.from(document.querySelectorAll('[class*="renew"], [id*="renew"], [data-action*="renew"]')).slice(0, 10).map((e, i) => ({
                    idx: i,
                    tag: e.tagName,
                    text: (e.innerText || '').trim().slice(0, 50),
                }));
                const bodyText = (document.body.innerText || '').slice(0, 500);
                return JSON.stringify({buttons: buttons, links: links, renewElems: renewElems, bodyText: bodyText});
            })()
        """, return_by_value=True)
        # 解析 JSON 字符串
        if isinstance(elements, str):
            try:
                elements = json.loads(elements)
            except Exception:
                elements = None
        if elements:
            print(f"  [debug] buttons ({len(elements.get('buttons', []))}):")
            for b in elements.get("buttons", []):
                print(f"    [{b['idx']}] text='{b['text']}' id='{b['id']}' class='{b['cls']}' disabled={b['disabled']}")
            print(f"  [debug] links ({len(elements.get('links', []))}):")
            for a in elements.get("links", []):
                print(f"    [{a['idx']}] text='{a['text']}' href='{a['href']}'")
            print(f"  [debug] renew-ish elements ({len(elements.get('renewElems', []))}):")
            for e in elements.get("renewElems", []):
                print(f"    [{e['idx']}] <{e['tag']}> '{e['text']}'")
            print(f"  [debug] body preview:")
            print(f"  {elements.get('bodyText', '')}")
        print(f"  [debug] === dump end ===")
    except Exception as e:
        print(f"  [debug] dump 失败: {e}")


async def find_and_click_renew(page) -> str:
    """查找并点击 Renew 按钮，返回命中的选择器/文字；找不到返回空字符串"""
    # 方式 1: 用 nodriver 的 find_element_by_text（最可靠）
    for text in RENEW_SELECTORS_TEXT:
        try:
            elem = await page.find_element_by_text(text, best_match=True)
            if elem:
                await elem.mouse_click()
                return f"text='{text}'"
        except Exception as e:
            # 这个文字查找失败，继续下一个
            continue

    # 方式 2: 用 JS 查找 button/a/input 含特定文字（把列表嵌入 JS 字符串）
    import json as _json
    texts_json = _json.dumps(RENEW_SELECTORS_TEXT)
    js_code = f"""
        (function() {{
            const texts = {texts_json};
            const allElements = [
                ...document.querySelectorAll('button:not([disabled]), a:not([disabled]), input[type="submit"], input[type="button"]'),
            ];
            for (const el of allElements) {{
                const elText = (el.innerText || el.value || '').trim();
                for (const t of texts) {{
                    if (elText.toLowerCase().includes(t.toLowerCase())) {{
                        el.click();
                        return elText;
                    }}
                }}
            }}
            // 也尝试 class/id 含 renew
            for (const el of document.querySelectorAll('[class*="renew"]:not([disabled]), [id*="renew"]:not([disabled]), [data-action*="renew"]')) {{
                el.click();
                return '[class/id*="renew"]: ' + (el.innerText || '').trim().slice(0, 30);
            }}
            // 也尝试 class/id 含 extend / 연장 / 갱신
            for (const sel of ['[class*="extend"]', '[id*="extend"]', '[class*="연장"]', '[class*="갱신"]']) {{
                for (const el of document.querySelectorAll(sel)) {{
                    if (el.tagName === 'BUTTON' || el.tagName === 'A' || el.onclick) {{
                        el.click();
                        return sel + ': ' + (el.innerText || '').trim().slice(0, 30);
                    }}
                }}
            }}
            return null;
        }})()
    """
    try:
        clicked = await page.evaluate(js_code, await_promise=False, return_by_value=True)
        if clicked:
            return f"js-click: {clicked}"
    except Exception as e:
        print(f"  [renew] JS 查找失败: {e}")

    return ""


async def renew_one(cookie_str: str, index: int, use_proxy: bool) -> tuple:
    """对单个账号执行续期（nodriver async 版）"""
    cookies = parse_cookie_string(cookie_str)
    if not cookies:
        return False, "Cookie 格式无效（未找到 key=value 对）"

    print(f"\n=== 账号 {index} 开始续期 ===")

    browser = None
    try:
        # 启动 nodriver
        browser_args = [
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--disable-blink-features=AutomationControlled",
            "--window-size=1920,1080",
        ]
        if use_proxy:
            browser_args.append(f"--proxy-server=socks5://127.0.0.1:{LOCAL_SOCKS_PORT}")
            print(f"  [proxy] nodriver 走本地 SOCKS5 127.0.0.1:{LOCAL_SOCKS_PORT} → {get_proxy_protocol(PROXY_NODE)}")
        else:
            print(f"  [proxy] 未配置代理，直连访问")

        # 尝试常见 Chrome 路径
        chrome_paths = [
            "/usr/bin/google-chrome",
            "/usr/bin/google-chrome-stable",
            "/usr/bin/chromium",
            "/usr/bin/chromium-browser",
        ]
        chrome_path = None
        for p in chrome_paths:
            if os.path.exists(p):
                chrome_path = p
                break

        browser = await uc.start(
            headless=True,
            sandbox=False,
            browser_executable_path=chrome_path,
            browser_args=browser_args,
            lang="zh-CN",
        )
        print(f"  ✓ nodriver 浏览器已启动 (chrome: {chrome_path})")

        # 验证 webdriver 隐藏
        page = await browser.get("about:blank")
        await asyncio.sleep(1)
        webdriver_val = await page.evaluate("navigator.webdriver")
        print(f"  [stealth] navigator.webdriver = {webdriver_val}")

        # 注入 cookies
        # 先访问目标域名让浏览器知道这个域
        page = await browser.get(BASE_URL)
        await asyncio.sleep(2)

        # 设置 cookies
        cookie_params = []
        for c in cookies:
            cookie_params.append(network.CookieParam(
                name=c["name"],
                value=c["value"],
                domain=".weirdhost.xyz",
                path="/",
                secure=False,
                http_only=False,
                same_site=None,
                expires=None,
            ))
        await page.send(network.set_cookies(cookie_params))
        print(f"  ✓ 已注入 {len(cookie_params)} 个 cookies")

        # ===== 阶段 1: 访问首页，处理 CF =====
        print("  [1/4] 访问首页...")
        page = await browser.get(BASE_URL)
        await asyncio.sleep(3)

        if not await wait_for_cf_clearance(page, max_sec=CF_WAIT_MAX_SEC, stage="homepage"):
            await save_debug_screenshot(page, "homepage_cf_blocked", index)
            return False, (
                "Cloudflare 拦截，nodriver 未能通过验证。\n"
                "建议：1) 检查 PROXY_NODE 是否有效  2) 更换代理节点  "
                "3) 用住宅 IP 代理  4) 改用本地运行"
            )

        # ===== 阶段 2: 访问 dashboard / 服务器列表页 =====
        print("  [2/4] 查找 dashboard 页面...")

        # 先尝试从首页提取「서버」(服务器) 链接，而不是「계정」(账号)
        dashboard_url = None
        try:
            links_json = await page.evaluate("""
                (function() {
                    return JSON.stringify(Array.from(document.querySelectorAll('a[href]')).map(a => ({
                        text: (a.innerText || '').trim().slice(0, 30),
                        href: a.href,
                    })).filter(l => l.href));
                })()
            """, return_by_value=True)
            links = None
            if isinstance(links_json, str):
                try:
                    links = json.loads(links_json)
                except Exception:
                    links = None
            print(f"  [debug] 首页找到 {len(links) if links else 0} 个链接")
            # 优先找「서버」(韩语服务器) 链接，而不是「계정」(账号)
            # 必须排除 /deleted-servers / /server/{id}（这些不是入口）
            keywords_priority = [
                ("서버", ["servers", "/server", "서버"]),
                ("대시보드", ["dashboard", "대시보드"]),
                ("server", ["servers", "/server"]),
                ("dashboard", ["dashboard"]),
            ]
            for kw_name, kw_list in keywords_priority:
                for link in (links or []):
                    href = link.get("href", "")
                    text = link.get("text", "").strip()
                    # 必须文本或 href 包含关键词
                    if any(kw.lower() in text.lower() or kw.lower() in href.lower() for kw in kw_list):
                        # 排除具体服务器详情页（/server/{id}）
                        import re as _re
                        if _re.search(r'/server/[a-f0-9]{8,}', href, _re.I):
                            continue
                        # 排除已删除服务器
                        if "deleted" in href.lower():
                            continue
                        # 转 absolute URL
                        if href.startswith("/"):
                            dashboard_url = BASE_URL + href
                        elif href.startswith("http"):
                            dashboard_url = href
                        else:
                            continue
                        print(f"  [debug] 从首页提取到 '{kw_name}' 链接: text='{text}' href='{dashboard_url}'")
                        break
                if dashboard_url:
                    break
        except Exception as e:
            print(f"  [debug] 提取首页链接失败: {e}")

        # 如果首页没找到「서버」链接，尝试候选路径
        if not dashboard_url:
            print(f"  [debug] 首页未找到服务器列表链接，开始尝试候选路径...")
            for path in DASHBOARD_CANDIDATES:
                candidate = BASE_URL + path
                try:
                    page = await browser.get(candidate)
                    await asyncio.sleep(2)
                    # 检查是否 404 / 被重定向到 login
                    title = await page.evaluate("document.title")
                    body_text = (await page.evaluate("document.body.innerText") or "").lower()
                    url_now = page.url.lower()
                    if "404" in title or "not found" in body_text or "未找到" in body_text or "찾을 수 없" in body_text:
                        print(f"  [debug] {path} → 404，跳过")
                        continue
                    if "/login" in url_now or "signin" in url_now:
                        print(f"  [debug] {path} → 重定向到 login，跳过")
                        continue
                    # 检查这个页面是否包含 /server/{id} 链接（说明是服务器列表页）
                    has_server_links = await page.evaluate(r"""
                        (function() {
                            return document.querySelectorAll('a[href*="/server/"]').length > 0;
                        })()
                    """, return_by_value=True)
                    if has_server_links:
                        dashboard_url = candidate
                        print(f"  [debug] {path} → ✓ 找到服务器列表页（含 /server/{{id}} 链接）")
                        break
                    else:
                        print(f"  [debug] {path} → 有效页面但无服务器链接，跳过")
                        continue
                except Exception as e:
                    print(f"  [debug] {path} 异常: {e}")
                    continue

        if not dashboard_url:
            await save_debug_screenshot(page, "no_dashboard_found", index)
            return False, (
                "未找到有效的 dashboard 路径（所有候选都 404 或无服务器链接）。"
                "请查看 Actions artifacts 中的调试截图，确认 weirdhost 实际页面结构。"
            )

        # 现在真正访问 dashboard URL（之前可能停在首页）
        print(f"  [debug] 访问 dashboard URL: {dashboard_url}")
        page = await browser.get(dashboard_url)
        await asyncio.sleep(3)

        # 再次 CF 检测（dashboard 也可能被拦）
        if not await wait_for_cf_clearance(page, max_sec=CF_WAIT_MAX_SEC, stage="dashboard"):
            await save_debug_screenshot(page, "dashboard_cf_blocked", index)
            return False, "Dashboard 也被 Cloudflare 拦截"

        if not await is_logged_in(page):
            await save_debug_screenshot(page, "not_logged_in", index)
            return False, "Cookie 已失效，请重新登录获取最新 Cookie"

        # 等 Pterodactyl Vue 异步渲染服务器列表（最多等 15 秒）
        print(f"  [debug] 等待服务器列表渲染...")
        for wait_i in range(15):
            has_links = await page.evaluate(r"""
                (function() {
                    return document.querySelectorAll('a[href*="/server/"]').length;
                })()
            """, return_by_value=True)
            count = 0
            if isinstance(has_links, (int, float)):
                count = int(has_links)
            elif isinstance(has_links, str):
                try:
                    count = int(has_links)
                except Exception:
                    pass
            if count > 0:
                print(f"  [debug] 渲染完成，发现 {count} 个服务器链接（等待 {wait_i} 秒）")
                break
            await asyncio.sleep(1)
        else:
            print(f"  [debug] 等待 15 秒后仍无服务器链接，可能服务器列表为空或加载失败")

        # ===== 阶段 2.5: 提取服务器列表（自动识别所有 /server/{id}）=====
        print("  [2.5/4] 提取服务器列表（自动扫描 /account 页面所有 /server/{id} 链接）...")
        server_info = await extract_server_info(page)
        server_name = server_info["server_name"]
        server_urls = server_info.get("server_urls", [])
        print(f"  [info] 服务器名: {server_name}")
        print(f"  [info] 发现 {len(server_urls)} 个服务器（自动识别）: {server_urls}")

        # 如果没有服务器，直接结束
        if not server_urls:
            await save_debug_screenshot(page, "no_servers", index)
            return False, "未发现任何服务器，无法续期"

        # ===== 阶段 3 + 4: 遍历每个服务器详情页，独立处理 + 发独立通知 =====
        print(f"\n  [3/4] 遍历 {len(server_urls)} 个服务器...")
        success_count = 0
        fail_count = 0
        results_summary = []

        for srv_idx, srv_url in enumerate(server_urls, 1):
            # 从 URL 提取 server_id（自动识别），例如 https://hub.weirdhost.xyz/server/9120ade0 → 9120ade0
            server_id = srv_url.rstrip("/").split("/")[-1]
            print(f"\n  --- 服务器 {srv_idx}/{len(server_urls)}: {server_id} ({srv_url}) ---")

            try:
                page = await browser.get(srv_url)
                await asyncio.sleep(3)

                # CF 检测
                if not await wait_for_cf_clearance(page, max_sec=CF_WAIT_MAX_SEC, stage=f"-{server_id}"):
                    print(f"  [warn] {server_id} 详情页被 CF 拦截")
                    notify_renew(server_id, server_name, "未知", "❌ Cloudflare 拦截", index)
                    fail_count += 1
                    results_summary.append(f"{server_id}: ❌ CF 拦截")
                    continue

                if not await is_logged_in(page):
                    print(f"  [warn] {server_id} 详情页跳转到 login（Cookie 失效）")
                    notify_renew(server_id, server_name, "未知", "❌ Cookie 失效", index)
                    fail_count += 1
                    results_summary.append(f"{server_id}: ❌ Cookie 失效")
                    continue

                # 等待 '유통기한' / '연장하기' 出现（最多 20 秒）
                print(f"  [debug] 等待详情页渲染 '유통기한' / '연장하기' ...")
                for wait_i in range(20):
                    found_kw = await page.evaluate(r"""
                        (function() {
                            const html = document.documentElement.innerHTML || '';
                            return /유통기한|예측기간|이용기한|만료일|연장하기|expir/i.test(html);
                        })()
                    """, return_by_value=True)
                    if found_kw:
                        print(f"  [debug] 关键字已渲染（等待 {wait_i} 秒）")
                        break
                    await asyncio.sleep(1)
                else:
                    print(f"  [warn] 等待 20 秒后仍未出现关键字")

                # 提取详情页信息
                detail_info = await extract_server_detail_info(page)
                srv_expiry = detail_info.get("expiry_date") or "未知"
                waiting_days = detail_info.get("waiting_days")
                waiting_hours = detail_info.get("waiting_hours")
                waiting_minutes = detail_info.get("waiting_minutes")
                renew_disabled = detail_info.get("renew_button_disabled")
                print(f"  [info] 利用期限: {srv_expiry}")
                print(f"  [info] 续期按钮: {detail_info.get('renew_available') or '(未找到)'}")
                print(f"  [info] 按钮禁用: {renew_disabled}")
                if waiting_days is not None:
                    print(f"  [info] 距离可续期还有 {waiting_days} 天")
                elif waiting_hours is not None:
                    print(f"  [info] 距离可续期还有 {waiting_hours} 小时")
                elif waiting_minutes is not None:
                    print(f"  [info] 距离可续期还有 {waiting_minutes} 分钟")

                # 构造"还需 N 天/N 小时/N 分钟才能续期"消息
                def _build_waiting_msg(d, h, m):
                    parts = []
                    if d is not None:
                        parts.append(f"{d} 天")
                    if h is not None:
                        parts.append(f"{h} 小时")
                    if m is not None:
                        parts.append(f"{m} 分钟")
                    if parts:
                        return f"⏳ 还需 {' '.join(parts)} 才能续期"
                    return "⏳ 续期按钮当前禁用（未到续期时间）"

                # ===== 判断按钮状态 + 决定行为 =====
                has_waiting = (waiting_days is not None and waiting_days > 0) or \
                              (waiting_hours is not None and waiting_hours > 0) or \
                              (waiting_minutes is not None and waiting_minutes > 0)
                if renew_disabled or has_waiting:
                    msg_result = _build_waiting_msg(waiting_days, waiting_hours, waiting_minutes)
                    print(f"  {msg_result}")
                    notify_renew(server_id, server_name, srv_expiry, msg_result, index)
                    await save_debug_screenshot(page, f"{server_id}_waiting", index)
                    success_count += 1
                    results_summary.append(f"{server_id}: {msg_result}")
                    continue

                # 按钮可点击：执行续期
                print(f"  [debug] 按钮可点击，尝试点击 '연장하기' ...")
                await asyncio.sleep(2)
                matched = await find_and_click_renew(page)
                if not matched:
                    print(f"  [warn] 未找到可点击的续期按钮")
                    notify_renew(server_id, server_name, srv_expiry, "❌ 未找到 Renew 按钮（可能本周期已续期）", index)
                    await save_debug_screenshot(page, f"{server_id}_no_btn", index)
                    fail_count += 1
                    results_summary.append(f"{server_id}: ❌ 未找到按钮")
                    continue
                print(f"  ✓ 命中: {matched}")

                # 等待结果
                print(f"  [debug] 等待续期结果...")
                await asyncio.sleep(3)
                try:
                    body_text = (await page.evaluate("document.body.innerText") or "").lower()
                except Exception:
                    body_text = ""

                success_signals = ["success", "renewed", "续期成功", "已续期", "updated", "완료", "갱신됨", "연장됨", "성공"]
                fail_signals = ["failed", "error", "失败", "expired", "forbidden", "denied", "실패"]

                if any(s in body_text for s in fail_signals):
                    msg_result = f"❌ 续期失败（{body_text[:80]}）"
                    print(f"  {msg_result}")
                    notify_renew(server_id, server_name, srv_expiry, msg_result, index)
                    await save_debug_screenshot(page, f"{server_id}_failed", index)
                    fail_count += 1
                    results_summary.append(f"{server_id}: {msg_result}")
                    continue

                if any(s in body_text for s in success_signals):
                    msg_result = "✅ 续期成功！"
                    print(f"  {msg_result}")
                    notify_renew(server_id, server_name, srv_expiry, msg_result, index)
                    await save_debug_screenshot(page, f"{server_id}_success", index)
                    success_count += 1
                    results_summary.append(f"{server_id}: {msg_result}")
                    continue

                # 默认：点击成功但未检测到明确反馈
                msg_result = "✅ 续期请求已发送"
                print(f"  {msg_result}")
                notify_renew(server_id, server_name, srv_expiry, msg_result, index)
                await save_debug_screenshot(page, f"{server_id}_clicked", index)
                success_count += 1
                results_summary.append(f"{server_id}: {msg_result}")

            except Exception as e:
                print(f"  [warn] 处理 {server_id} 异常: {e}")
                notify_renew(server_id, server_name, "未知", f"❌ 异常: {str(e)[:80]}", index)
                fail_count += 1
                results_summary.append(f"{server_id}: ❌ 异常")

        # 汇总
        summary = f"共 {len(server_urls)} 个服务器: {success_count} 成功 / {fail_count} 失败\n" + "\n".join(results_summary)
        print(f"\n  === 汇总 ===\n  {summary}")
        return (fail_count == 0), summary

    except Exception as e:
        return False, f"异常: {e}\n{traceback.format_exc()}"
    finally:
        if browser:
            try:
                browser.stop()
            except Exception:
                pass


def test_proxy_alive(timeout: int = 5) -> bool:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(timeout)
        s.connect(("127.0.0.1", LOCAL_SOCKS_PORT))
        s.close()
        return True
    except Exception:
        return False


def test_proxy_works(timeout: int = 20) -> tuple:
    proxies = {
        "http":  f"socks5h://127.0.0.1:{LOCAL_SOCKS_PORT}",
        "https": f"socks5h://127.0.0.1:{LOCAL_SOCKS_PORT}",
    }
    try:
        r = requests.get("https://ipinfo.io/json", proxies=proxies, timeout=timeout)
        if r.status_code == 200:
            d = r.json()
            return True, f"出口 IP: {d.get('ip')} ({d.get('country')}, {d.get('city')}, {d.get('org', '')[:50]})"
        return False, f"ipinfo 返回 {r.status_code}"
    except Exception as e:
        return False, f"代理出网失败: {e}"


async def async_main():
    print("=" * 60)
    print("  Weirdhost Auto Renew (v6 - nodriver + CF Bypass)")
    print("=" * 60)
    print(f"  nodriver: ✓ 已加载")

    # ===== 代理检查 =====
    use_proxy = bool(PROXY_NODE)
    if use_proxy:
        proto = get_proxy_protocol(PROXY_NODE)
        print(f"  proxy:  ✓ 已配置 {proto} 协议")
        print(f"          (本地 SOCKS5 127.0.0.1:{LOCAL_SOCKS_PORT})")

        if not test_proxy_alive(timeout=5):
            msg = f"本地 SOCKS5 代理 127.0.0.1:{LOCAL_SOCKS_PORT} 未启动，请确认 sing-box 已运行"
            print(f"  [ERROR] {msg}")
            notify("代理错误", msg)
            sys.exit(1)
        print(f"  proxy:  ✓ 本地代理端口可达")

        ok, msg = test_proxy_works(timeout=20)
        if ok:
            print(f"  proxy:  ✓ 出网正常 - {msg}")
        else:
            print(f"  proxy:  ✗ {msg}")
            notify("代理错误", f"代理出网失败: {msg}")
            sys.exit(1)
    else:
        print(f"  proxy:  ✗ 未配置 PROXY_NODE（直连模式，可能被 CF 拦截）")

    cookies = load_cookies()
    if not cookies:
        msg = "未配置任何 Cookie，请添加 WEIRDH0ST_COOKIE_1 等 Secrets"
        print(f"[ERROR] {msg}")
        notify("配置错误", msg)
        sys.exit(1)

    print(f"\n共发现 {len(cookies)} 个账号待续期")

    results = []
    for i, ck in enumerate(cookies, 1):
        ok, msg = await renew_one(ck, i, use_proxy)
        results.append((i, ok, msg))
        status = "✅" if ok else "❌"
        print(f"  账号 {i}: {status} {msg}")

    success_count = sum(1 for _, ok, _ in results if ok)
    fail_count = len(results) - success_count

    summary_lines = [f"账号 {i}: {'✅' if ok else '❌'} {msg}" for i, ok, msg in results]
    summary = f"续期汇总: {success_count} 成功 / {fail_count} 失败 (共 {len(results)} 个)\n\n" + "\n".join(summary_lines)

    print("\n" + "=" * 60)
    print(summary)
    print("=" * 60)

    if fail_count > 0:
        notify(f"⚠️ Weirdhost 续期部分失败 ({success_count}/{len(results)})", summary)
    else:
        notify(f"✅ Weirdhost 续期全部成功 ({len(results)})", summary)

    return 0 if fail_count == 0 else 1


def main():
    exit_code = asyncio.run(async_main())
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
