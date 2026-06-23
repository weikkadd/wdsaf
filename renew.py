#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Weirdhost Auto Renew (v3 - Better CF Detection + Debug Screenshot)
"""

import os
import sys
import json
import time
import traceback

import requests
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeoutError, Error as PWError

try:
    from playwright_stealth import stealth_sync
    HAS_STEALTH = True
except ImportError:
    try:
        from playwright_stealth import Stealth
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
DEBUG_DIR = "/tmp/weirdhost_debug"

# ===== 通知配置 =====
TG_BOT_TOKEN = os.getenv("TG_BOT_TOKEN", "").strip()
TG_CHAT_ID = os.getenv("TG_CHAT_ID", "").strip()
WEBHOOK_URL = os.getenv("WEBHOOK_URL", "").strip()

# Cloudflare 拦截检测关键词（只要命中任一即视为被拦）
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
    "ray id",  # 仅在短页面时算
]

# Cloudflare JS Challenge 等待时间
CF_WAIT_MAX_SEC = 45
CF_WAIT_INTERVAL = 3

# Renew 按钮可能的所有选择器（更全面）
RENEW_SELECTORS = [
    # 文字匹配
    "button:has-text('Renew')",
    "button:has-text('renew')",
    "button:has-text('RENEW')",
    "button:has-text('续期')",
    "button:has-text('续')",
    "button:has-text('Renew Now')",
    "button:has-text('Renewal')",
    "button:has-text('Extend')",
    "button:has-text('延期')",
    "button:has-text('激活')",
    "button:has-text('Activate')",
    "a:has-text('Renew')",
    "a:has-text('续期')",
    "a:has-text('Renew Now')",
    "a:has-text('Extend')",
    "input[type='submit'][value*='Renew']",
    "input[type='submit'][value*='续期']",
    "input[type='button'][value*='Renew']",
    # class / data 属性
    "[class*='renew']:not([disabled])",
    "[class*='Renew']:not([disabled])",
    "[data-action='renew']",
    "[data-action='Renew']",
    "[id*='renew']:not([disabled])",
    "[id*='Renew']:not([disabled])",
    # 通用 button type=submit
    "button[type='submit']:has-text('Renew')",
    "button[type='submit']:has-text('续期')",
]


def apply_stealth(page):
    if not HAS_STEALTH:
        return
    try:
        try:
            stealth_sync(page)
        except (NameError, TypeError):
            _stealth.apply_stealth_sync(page)
    except Exception as e:
        print(f"  [stealth] 应用失败: {e}", file=sys.stderr)


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
    """
    检测当前页面是否被 Cloudflare 拦截
    严格检测：只要命中关键词即视为被拦（不再要求页面短）
    """
    try:
        content = page.content().lower()
    except Exception:
        return False

    # 高置信度信号（单独命中即视为拦截）
    strong_signals = [
        "just a moment",
        "checking your browser",
        "verifying you are human",
        "cf-challenge",
        "cf_chl_opt",
        "正在进行安全验证",
        "请启用 javascript",
        "enable javascript and cookies",
    ]
    for sig in strong_signals:
        if sig.lower() in content:
            return True

    # 弱信号（需多个同时命中才算）
    weak_signals = ["cloudflare", "ray id", "attention required"]
    weak_count = sum(1 for sig in weak_signals if sig in content)
    if weak_count >= 2:
        # 还需要看页面是否短（CF 错误页通常很短）
        if len(content) < 8000:
            return True

    return False


def wait_for_cf_clearance(page, max_sec: int = CF_WAIT_MAX_SEC, stage: str = "") -> bool:
    """等待 CF Challenge 自动放行"""
    if not is_cf_blocked(page):
        return True

    print(f"  [cf{stage}] 检测到 Cloudflare 挑战，等待自动放行（最多 {max_sec}s）...")
    elapsed = 0
    while elapsed < max_sec:
        time.sleep(CF_WAIT_INTERVAL)
        elapsed += CF_WAIT_INTERVAL
        try:
            page.wait_for_load_state("networkidle", timeout=3000)
        except PWTimeoutError:
            pass
        if not is_cf_blocked(page):
            print(f"  [cf{stage}] ✓ Cloudflare 已放行（耗时 {elapsed}s）")
            return True
        print(f"  [cf{stage}] 等待中... ({elapsed}s)")
    print(f"  [cf{stage}] ✗ Cloudflare 等待超时（{max_sec}s）")
    return False


def is_logged_in(page) -> bool:
    try:
        url = page.url.lower()
    except Exception:
        return False
    # 排除明显的登录页 URL
    if "/login" in url or "signin" in url:
        return False
    # 排除 CF 拦截页 URL（含 __cf_chl_rt_tk 参数）
    if "__cf_chl" in url:
        return False
    return True


def save_debug_screenshot(page, name: str, account_idx: int):
    """保存调试截图，便于排查问题"""
    try:
        os.makedirs(DEBUG_DIR, exist_ok=True)
        path = f"{DEBUG_DIR}/account{account_idx}_{name}.png"
        page.screenshot(path=path, full_page=True)
        print(f"  [debug] 截图已保存: {path}")
        # 也保存 HTML
        html_path = f"{DEBUG_DIR}/account{account_idx}_{name}.html"
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(page.content())
        print(f"  [debug] HTML 已保存: {html_path}")
    except Exception as e:
        print(f"  [debug] 保存截图失败: {e}")


def dump_page_elements(page, account_idx: int):
    """打印页面上所有按钮和链接（用于调试 Renew 按钮找不到的情况）"""
    try:
        print(f"  [debug] === 页面元素 dump ===")
        print(f"  [debug] URL: {page.url}")
        print(f"  [debug] Title: {page.title()}")

        buttons = page.query_selector_all("button")
        print(f"  [debug] buttons ({len(buttons)}):")
        for i, b in enumerate(buttons[:30]):
            try:
                txt = (b.inner_text() or "").strip()[:50]
                cls = (b.get_attribute("class") or "")[:60]
                tid = b.get_attribute("id") or ""
                disabled = b.get_attribute("disabled")
                print(f"    [{i}] text='{txt}' id='{tid}' class='{cls}' disabled={disabled}")
            except Exception:
                pass

        links = page.query_selector_all("a")
        print(f"  [debug] links ({len(links)}):")
        for i, a in enumerate(links[:30]):
            try:
                txt = (a.inner_text() or "").strip()[:50]
                href = (a.get_attribute("href") or "")[:80]
                print(f"    [{i}] text='{txt}' href='{href}'")
            except Exception:
                pass

        # 任何含 renew 字样的元素
        renew_elems = page.query_selector_all("[class*='renew'], [id*='renew'], [data-action*='renew']")
        print(f"  [debug] renew-ish elements ({len(renew_elems)}):")
        for i, e in enumerate(renew_elems[:10]):
            try:
                tag = page.evaluate("(el) => el.tagName", e)
                txt = (e.inner_text() or "").strip()[:50]
                print(f"    [{i}] <{tag}> '{txt}'")
            except Exception:
                pass

        # body 文本前 500 字
        body_text = page.inner_text("body")[:500]
        print(f"  [debug] body preview:\n{body_text}")
        print(f"  [debug] === dump end ===")
    except Exception as e:
        print(f"  [debug] dump 失败: {e}")


def make_browser(p):
    launch_args = [
        "--no-sandbox",
        "--disable-blink-features=AutomationControlled",
        "--disable-dev-shm-usage",
        "--disable-features=IsolateOrigins,site-per-process",
        "--disable-infobars",
        "--window-size=1920,1080",
        "--disable-extensions",
        "--use-gl=swiftshader",
        "--enable-webgl",
        "--ignore-certificate-errors",
    ]
    browser = p.chromium.launch(headless=True, args=launch_args)
    return browser


def make_context(browser):
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
    context.add_init_script("""
        Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
        Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
        Object.defineProperty(navigator, 'languages', { get: () => ['zh-CN', 'zh', 'en'] });
        Object.defineProperty(navigator, 'platform', { get: () => 'Win32' });
        Object.defineProperty(navigator, 'hardwareConcurrency', { get: () => 8 });
        Object.defineProperty(navigator, 'deviceMemory', { get: () => 8 });
        const getParameter = WebGLRenderingContext.prototype.getParameter;
        WebGLRenderingContext.prototype.getParameter = function (parameter) {
            if (parameter === 37445) return 'Intel Inc.';
            if (parameter === 37446) return 'Intel Iris OpenGL Engine';
            return getParameter.call(this, parameter);
        };
        const originalQuery = window.navigator.permissions.query;
        window.navigator.permissions.query = (parameters) =>
            parameters.name === 'notifications'
                ? Promise.resolve({ state: Notification.permission })
                : originalQuery(parameters);
        window.chrome = { runtime: {} };
    """)
    return context


def renew_one(cookie_str: str, index: int) -> tuple:
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

            # 多等一会让 CF challenge 自动放行
            page.wait_for_timeout(2000)

            if not wait_for_cf_clearance(page, max_sec=CF_WAIT_MAX_SEC, stage="homepage"):
                save_debug_screenshot(page, "homepage_cf_blocked", index)
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

            page.wait_for_timeout(2000)

            if not wait_for_cf_clearance(page, max_sec=CF_WAIT_MAX_SEC, stage="dashboard"):
                save_debug_screenshot(page, "dashboard_cf_blocked", index)
                return False, "Dashboard 也被 Cloudflare 拦截"

            if not is_logged_in(page):
                save_debug_screenshot(page, "not_logged_in", index)
                return False, "Cookie 已失效，请重新登录获取最新 Cookie"

            # ===== 阶段 3: 查找并点击 Renew 按钮 =====
            print("  [3/4] 查找 Renew 按钮...")
            # 等待 dashboard 完全加载
            try:
                page.wait_for_load_state("networkidle", timeout=10000)
            except PWTimeoutError:
                pass
            page.wait_for_timeout(2000)

            clicked = False
            matched_selector = None
            for sel in RENEW_SELECTORS:
                try:
                    page.wait_for_selector(sel, timeout=2000)
                    page.click(sel, timeout=5000)
                    clicked = True
                    matched_selector = sel
                    print(f"  ✓ 命中选择器: {sel}")
                    break
                except (PWTimeoutError, PWError):
                    continue

            if not clicked:
                # 调试：截图 + dump 页面元素
                save_debug_screenshot(page, "no_renew_button", index)
                dump_page_elements(page, index)
                return False, (
                    "未发现 Renew 按钮（dashboard 上没有任何匹配的按钮）。"
                    "请查看 Actions artifacts 中的调试截图 (/tmp/weirdhost_debug/)。"
                    "可能原因：1) 账号未到期，dashboard 不显示 Renew 按钮  "
                    "2) 按钮文字/选择器未覆盖  3) 页面结构变化"
                )

            # ===== 阶段 4: 等待结果 =====
            print("  [4/4] 等待续期结果...")
            page.wait_for_timeout(3000)

            # 检测成功/失败提示
            try:
                body_text = page.inner_text("body", timeout=5000).lower()
            except Exception:
                body_text = ""

            success_signals = ["success", "renewed", "续期成功", "已续期", "updated", "完成", "成功"]
            fail_signals = ["failed", "error", "失败", "expired", "forbidden", "denied"]

            if any(s in body_text for s in fail_signals):
                save_debug_screenshot(page, "renew_failed", index)
                return False, f"续期失败，页面反馈: {body_text[:200]}"
            if any(s in body_text for s in success_signals):
                save_debug_screenshot(page, "renew_success", index)
                return True, f"续期成功（选择器: {matched_selector}）"

            # 默认认为点击成功即续期
            save_debug_screenshot(page, "renew_clicked", index)
            return True, f"续期请求已发送（选择器: {matched_selector}）"

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
    print("  Weirdhost Auto Renew (v3 - Better CF Detection)")
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
