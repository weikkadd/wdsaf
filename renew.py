#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Weirdhost Auto Renew (Cloudflare Bypass Edition)
- Cookie 模式登录（无需账号密码）
- 多账号支持（WEIRDH0ST_COOKIE_1, _2, _3, ...）
- Telegram Bot + Webhook 双通道通知
- Cloudflare 反检测：
  * playwright-stealth 隐藏自动化特征
  * JS Challenge 自动等待（最多 30 秒）
  * 真实浏览器指纹（UA/视口/语言/时区/WebGL）
- 完整异常处理 + finally 资源回收
"""

import os
import sys
import json
import time
import random
import traceback

import requests
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeoutError, Error as PWError

try:
    from playwright_stealth import stealth_sync  # v1
    HAS_STEALTH = True
except ImportError:
    try:
        from playwright_stealth import Stealth  # v2
        HAS_STEALTH = True
        _stealth = Stealth()
    except ImportError:
        HAS_STEALTH = False
        _stealth = None

# ===== 配置 =====
BASE_URL = "https://hub.weirdhost.xyz"
DASHBOARD_URL = f"{BASE_URL}/dashboard"
LOGIN_URL = f"{BASE_URL}/login"

MAX_COOKIE_SLOTS = 50

# ===== 通知配置 =====
TG_BOT_TOKEN = os.getenv("TG_BOT_TOKEN", "").strip()
TG_CHAT_ID = os.getenv("TG_CHAT_ID", "").strip()
WEBHOOK_URL = os.getenv("WEBHOOK_URL", "").strip()

# Cloudflare 拦截检测关键词
CF_SIGNALS = [
    "just a moment",
    "checking your browser",
    "verifying you are human",
    "cf-challenge",
    "cf_chl_opt",
    "attention required",
    "cloudflare",
    "ray id",  # CF 错误页通常有 Ray ID
    "enable javascript and cookies",
]

# Cloudflare JS Challenge 通常 5 秒后自动放行，最多等 30 秒
CF_WAIT_MAX_SEC = 30
CF_WAIT_INTERVAL = 2


def apply_stealth(page):
    """应用 stealth 反检测（兼容 v1/v2 API）"""
    if not HAS_STEALTH:
        return
    try:
        try:
            stealth_sync(page)  # v1
        except (NameError, TypeError):
            _stealth.apply_stealth_sync(page)  # v2
    except Exception as e:
        print(f"  [stealth] 应用失败: {e}", file=sys.stderr)


def notify(title: str, content: str) -> None:
    """发送通知到 Telegram + Webhook"""
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
    """读取所有 WEIRDH0ST_COOKIE_N 环境变量"""
    cookies = []
    for i in range(1, MAX_COOKIE_SLOTS + 1):
        v = os.getenv(f"WEIRDH0ST_COOKIE_{i}", "").strip()
        if v:
            cookies.append(v)
    return cookies


def parse_cookie_string(cookie_str: str) -> list:
    """把 'a=b; c=d' 解析为 Playwright 可用的 cookie 列表"""
    cookies = []
    for pair in cookie_str.split(";"):
        pair = pair.strip()
        if not pair or "=" not in pair:
            continue
        name, value = pair.split("=", 1)
        cookies.append({
            "name": name.strip(),
            "value": value.strip(),
            "domain": "hub.weirdhost.xyz",
            "path": "/",
        })
    return cookies


def is_cf_blocked(page) -> bool:
    """检测当前页面是否被 Cloudflare 拦截"""
    try:
        content = page.content().lower()
    except Exception:
        return False
    # 排除误判：页面正文太短通常意味着是挑战页
    is_short = len(content) < 5000
    has_signal = any(sig in content for sig in CF_SIGNALS)
    return has_signal and is_short


def wait_for_cf_clearance(page, max_sec: int = CF_WAIT_MAX_SEC) -> bool:
    """
    如果当前是 CF Challenge 页，等待 CF 自动放行
    返回 True 表示放行成功，False 表示超时仍被拦截
    """
    if not is_cf_blocked(page):
        return True

    print(f"  [cf] 检测到 Cloudflare 挑战，等待自动放行（最多 {max_sec}s）...")
    elapsed = 0
    while elapsed < max_sec:
        time.sleep(CF_WAIT_INTERVAL)
        elapsed += CF_WAIT_INTERVAL
        try:
            page.wait_for_load_state("networkidle", timeout=3000)
        except PWTimeoutError:
            pass
        if not is_cf_blocked(page):
            print(f"  [cf] ✓ Cloudflare 已放行（耗时 {elapsed}s）")
            return True
        print(f"  [cf] 等待中... ({elapsed}s)")
    print(f"  [cf] ✗ Cloudflare 等待超时")
    return False


def is_logged_in(page) -> bool:
    """通过 URL 判断是否已登录"""
    try:
        url = page.url.lower()
    except Exception:
        return False
    return "/login" not in url and "signin" not in url


def make_browser(p):
    """启动带反检测特征的 Chromium"""
    launch_args = [
        "--no-sandbox",
        "--disable-blink-features=AutomationControlled",
        "--disable-dev-shm-usage",
        "--disable-features=IsolateOrigins,site-per-process",
        "--disable-infobars",
        "--window-size=1920,1080",
        "--disable-extensions",
        "--disable-component-extensions-with-background-pages",
        # 反指纹
        "--use-gl=swiftshader",
        "--enable-webgl",
        "--ignore-certificate-errors",
    ]
    browser = p.chromium.launch(headless=True, args=launch_args)
    return browser


def make_context(browser):
    """创建带真实指纹的浏览器上下文"""
    context = browser.new_context(
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        viewport={"width": 1920, "height": 1080},
        screen={"width": 1920, "height": 1080},
        locale="zh-CN",
        timezone_id="Asia/Shanghai",
        extra_http_headers={
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Accept-Encoding": "gzip, deflate, br",
            "Sec-Ch-Ua": '"Not_A Brand";v="8", "Chromium";v="120", "Google Chrome";v="120"',
            "Sec-Ch-Ua-Mobile": "?0",
            "Sec-Ch-Ua-Platform": '"Windows"',
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Sec-Fetch-User": "?1",
            "Upgrade-Insecure-Requests": "1",
        },
    )

    # 注入反检测 JS
    context.add_init_script("""
        // 隐藏 webdriver
        Object.defineProperty(navigator, 'webdriver', { get: () => undefined });

        // 伪造 plugins
        Object.defineProperty(navigator, 'plugins', {
            get: () => [1, 2, 3, 4, 5],
        });

        // 伪造 languages
        Object.defineProperty(navigator, 'languages', {
            get: () => ['zh-CN', 'zh', 'en'],
        });

        // 伪造 platform
        Object.defineProperty(navigator, 'platform', { get: () => 'Win32' });

        // 伪造 hardwareConcurrency
        Object.defineProperty(navigator, 'hardwareConcurrency', { get: () => 8 });

        // 伪造 deviceMemory
        Object.defineProperty(navigator, 'deviceMemory', { get: () => 8 });

        // 伪造 WebGL
        const getParameter = WebGLRenderingContext.prototype.getParameter;
        WebGLRenderingContext.prototype.getParameter = function (parameter) {
            if (parameter === 37445) return 'Intel Inc.';  // UNMASKED_VENDOR_WEBGL
            if (parameter === 37446) return 'Intel Iris OpenGL Engine';  // UNMASKED_RENDERER_WEBGL
            return getParameter.call(this, parameter);
        };

        // 伪造 permissions
        const originalQuery = window.navigator.permissions.query;
        window.navigator.permissions.query = (parameters) =>
            parameters.name === 'notifications'
                ? Promise.resolve({ state: Notification.permission })
                : originalQuery(parameters);

        // 隐藏 chrome runtime
        window.chrome = { runtime: {} };
    """)

    return context


def renew_one(cookie_str: str, index: int) -> tuple:
    """对单个账号执行续期"""
    cookies = parse_cookie_string(cookie_str)
    if not cookies:
        return False, "Cookie 格式无效（未找到 key=value 对）"

    print(f"\n=== 账号 {index} 开始续期 ===")
    with sync_playwright() as p:
        browser = make_browser(p)
        try:
            context = make_context(browser)
            context.add_cookies(cookies)
            page = context.new_page()
            apply_stealth(page)

            # ===== 阶段 1: 访问首页，处理 CF =====
            print("  [1/4] 访问首页...")
            try:
                page.goto(BASE_URL, wait_until="domcontentloaded", timeout=30000)
            except PWTimeoutError:
                return False, "访问首页超时（30s）"

            # 等 CF 放行
            if not wait_for_cf_clearance(page, max_sec=CF_WAIT_MAX_SEC):
                return False, (
                    "Cloudflare 拦截，stealth 反检测也未能通过。\n"
                    "可能原因：GitHub Actions IP 被严格风控。\n"
                    "建议：1) 重试 2-3 次  2) 配置代理  3) 改用本地运行"
                )

            # ===== 阶段 2: 访问 dashboard 验证登录态 =====
            print("  [2/4] 访问 dashboard 验证 Cookie...")
            try:
                page.goto(DASHBOARD_URL, wait_until="domcontentloaded", timeout=30000)
            except PWTimeoutError:
                return False, "访问 dashboard 超时"

            # 二次 CF 检测（dashboard 也可能被拦）
            if not wait_for_cf_clearance(page, max_sec=CF_WAIT_MAX_SEC):
                return False, "Dashboard 也被 Cloudflare 拦截"

            page.wait_for_timeout(2000)

            if not is_logged_in(page):
                return False, "Cookie 已失效，请重新登录获取最新 Cookie"

            # ===== 阶段 3: 查找并点击 Renew 按钮 =====
            print("  [3/4] 查找 Renew 按钮...")
            renew_selectors = [
                "button:has-text('Renew')",
                "button:has-text('renew')",
                "button:has-text('续期')",
                "a:has-text('Renew')",
                "a:has-text('续期')",
                "[class*='renew']:not([disabled])",
                "[data-action='renew']",
                "button[type='submit']:has-text('Renew')",
            ]

            clicked = False
            for sel in renew_selectors:
                try:
                    page.wait_for_selector(sel, timeout=3000)
                    page.click(sel, timeout=5000)
                    clicked = True
                    print(f"  ✓ 命中选择器: {sel}")
                    break
                except (PWTimeoutError, PWError):
                    continue

            if not clicked:
                return True, "未发现 Renew 按钮，可能本周期已自动续期"

            # ===== 阶段 4: 等待结果 =====
            print("  [4/4] 等待续期结果...")
            page.wait_for_timeout(3000)

            try:
                body_text = page.inner_text("body", timeout=5000).lower()
            except Exception:
                body_text = ""

            success_signals = ["success", "renewed", "续期成功", "已续期", "updated", "完成"]
            fail_signals = ["failed", "error", "失败", "expired", "forbidden", "denied"]

            if any(s in body_text for s in fail_signals):
                return False, f"续期失败，页面反馈: {body_text[:200]}"
            if any(s in body_text for s in success_signals):
                return True, "续期成功"

            return True, "续期请求已发送（未检测到错误反馈）"

        except PWError as e:
            return False, f"Playwright 异常: {e}"
        except Exception as e:
            return False, f"未知异常: {e}\n{traceback.format_exc()}"
        finally:
            try:
                browser.close()
            except Exception:
                pass


def main():
    print("=" * 60)
    print("  Weirdhost Auto Renew (CF Bypass Edition)")
    print("=" * 60)
    print(f"  stealth: {'✓ 已加载' if HAS_STEALTH else '✗ 未安装 playwright-stealth'}")

    cookies = load_cookies()
    if not cookies:
        msg = "未配置任何 Cookie，请添加 WEIRDH0ST_COOKIE_1 等 Secrets"
        print(f"[ERROR] {msg}")
        notify("配置错误", msg)
        sys.exit(1)

    print(f"\n共发现 {len(cookies)} 个账号待续期")

    results = []
    for i, ck in enumerate(cookies, 1):
        ok, msg = renew_one(ck, i)
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

    sys.exit(0 if fail_count == 0 else 1)


if __name__ == "__main__":
    main()
