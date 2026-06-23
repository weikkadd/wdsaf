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
DASHBOARD_URL = f"{BASE_URL}/dashboard"
LOGIN_URL = f"{BASE_URL}/login"

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

# Renew 按钮选择器（保留全面）
RENEW_SELECTORS_TEXT = [
    "Renew",
    "renew",
    "RENEW",
    "续期",
    "续",
    "Renew Now",
    "Renewal",
    "Extend",
    "延期",
    "激活",
    "Activate",
]


def notify(title: str, content: str) -> None:
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

        # 用 JS 提取所有按钮和链接
        elements = await page.evaluate("""
            () => {
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
                return {buttons, links, renewElems, bodyText};
            }
        """)
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
    # 方式 1: 用 nodriver 的 find_element_by_text
    for text in RENEW_SELECTORS_TEXT:
        try:
            elem = await page.find_element_by_text(text, best_match=True)
            if elem:
                await elem.mouse_click()
                return f"text='{text}'"
        except Exception:
            continue

    # 方式 2: 用 JS 查找 button/a/input 含特定文字
    clicked = await page.evaluate("""
        async (texts) => {
            const allElements = [
                ...document.querySelectorAll('button:not([disabled]), a:not([disabled]), input[type="submit"], input[type="button"]'),
            ];
            for (const el of allElements) {
                const elText = (el.innerText || el.value || '').trim();
                for (const t of texts) {
                    if (elText.toLowerCase().includes(t.toLowerCase())) {
                        el.click();
                        return elText;
                    }
                }
            }
            // 也尝试 class/id 含 renew
            for (const el of document.querySelectorAll('[class*="renew"]:not([disabled]), [id*="renew"]:not([disabled]), [data-action*="renew"]')) {
                el.click();
                return '[class/id*="renew"]: ' + (el.innerText || '').trim().slice(0, 30);
            }
            return null;
        }
    """, RENEW_SELECTORS_TEXT)
    if clicked:
        return f"js-click: {clicked}"

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

        # ===== 阶段 2: 访问 dashboard 验证登录态 =====
        print("  [2/4] 访问 dashboard 验证 Cookie...")
        page = await browser.get(DASHBOARD_URL)
        await asyncio.sleep(3)

        if not await wait_for_cf_clearance(page, max_sec=CF_WAIT_MAX_SEC, stage="dashboard"):
            await save_debug_screenshot(page, "dashboard_cf_blocked", index)
            return False, "Dashboard 也被 Cloudflare 拦截"

        if not await is_logged_in(page):
            await save_debug_screenshot(page, "not_logged_in", index)
            return False, "Cookie 已失效，请重新登录获取最新 Cookie"

        # ===== 阶段 3: 查找并点击 Renew 按钮 =====
        print("  [3/4] 查找 Renew 按钮...")
        await asyncio.sleep(2)  # 等 dashboard 完全加载

        matched = await find_and_click_renew(page)
        if not matched:
            await save_debug_screenshot(page, "no_renew_button", index)
            await dump_page_elements(page, index)
            return False, (
                "未发现 Renew 按钮（dashboard 上没有任何匹配的按钮）。"
                "请查看 Actions artifacts 中的调试截图。"
                "可能原因：1) 账号未到期  2) 按钮文字未覆盖  3) 页面结构变化"
            )
        print(f"  ✓ 命中: {matched}")

        # ===== 阶段 4: 等待结果 =====
        print("  [4/4] 等待续期结果...")
        await asyncio.sleep(3)

        try:
            body_text = (await page.evaluate("document.body.innerText") or "").lower()
        except Exception:
            body_text = ""

        success_signals = ["success", "renewed", "续期成功", "已续期", "updated", "完成", "成功"]
        fail_signals = ["failed", "error", "失败", "expired", "forbidden", "denied"]

        if any(s in body_text for s in fail_signals):
            await save_debug_screenshot(page, "renew_failed", index)
            return False, f"续期失败，页面反馈: {body_text[:200]}"
        if any(s in body_text for s in success_signals):
            await save_debug_screenshot(page, "renew_success", index)
            return True, f"续期成功（{matched}）"

        await save_debug_screenshot(page, "renew_clicked", index)
        return True, f"续期请求已发送（{matched}）"

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
