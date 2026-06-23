#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Weirdhost Auto Renew (v5 - sing-box + 7 Protocols)
- 支持 VLESS/VMess/Trojan/SS/SOCKS5/Hysteria2/TUIC 代理（通过 PROXY_NODE 环境变量）
- Cookie 模式登录 + 多账号 + TG/Webhook 通知
- Cloudflare 反检测 (playwright-stealth + 反指纹)
"""

import os
import sys
import json
import time
import traceback
import socket

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

from proxy_parser import parse_proxy, get_proxy_protocol, build_singbox_config

# ===== 配置 =====
BASE_URL = "https://hub.weirdhost.xyz"
DASHBOARD_URL = f"{BASE_URL}/dashboard"
LOGIN_URL = f"{BASE_URL}/login"

MAX_COOKIE_SLOTS = 50
DEBUG_DIR = "/tmp/weirdhost_debug"

# 本地代理端口（Xray 会监听这些端口）
LOCAL_SOCKS_PORT = int(os.getenv("LOCAL_SOCKS_PORT", "1080"))
LOCAL_HTTP_PORT = int(os.getenv("LOCAL_HTTP_PORT", "1081"))

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

# Cloudflare 等待时间
CF_WAIT_MAX_SEC = 60
CF_WAIT_INTERVAL = 3

# Renew 按钮选择器（全面）
RENEW_SELECTORS = [
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
    "[class*='renew']:not([disabled])",
    "[class*='Renew']:not([disabled])",
    "[data-action='renew']",
    "[data-action='Renew']",
    "[id*='renew']:not([disabled])",
    "[id*='Renew']:not([disabled])",
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
    """检测 Cloudflare 拦截"""
    try:
        content = page.content().lower()
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
        "阻止",
        "安全验证过程",
    ]
    for sig in strong_signals:
        if sig.lower() in content:
            return True

    weak_signals = ["cloudflare", "ray id", "attention required"]
    weak_count = sum(1 for sig in weak_signals if sig in content)
    if weak_count >= 2 and len(content) < 8000:
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
    if "/login" in url or "signin" in url:
        return False
    if "__cf_chl" in url:
        return False
    return True


def save_debug_screenshot(page, name: str, account_idx: int):
    try:
        os.makedirs(DEBUG_DIR, exist_ok=True)
        path = f"{DEBUG_DIR}/account{account_idx}_{name}.png"
        page.screenshot(path=path, full_page=True)
        print(f"  [debug] 截图: {path}")
        html_path = f"{DEBUG_DIR}/account{account_idx}_{name}.html"
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(page.content())
        print(f"  [debug] HTML: {html_path}")
    except Exception as e:
        print(f"  [debug] 保存截图失败: {e}")


def dump_page_elements(page, account_idx: int):
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

        renew_elems = page.query_selector_all("[class*='renew'], [id*='renew'], [data-action*='renew']")
        print(f"  [debug] renew-ish elements ({len(renew_elems)}):")
        for i, e in enumerate(renew_elems[:10]):
            try:
                tag = page.evaluate("(el) => el.tagName", e)
                txt = (e.inner_text() or "").strip()[:50]
                print(f"    [{i}] <{tag}> '{txt}'")
            except Exception:
                pass

        body_text = page.inner_text("body")[:500]
        print(f"  [debug] body preview:\n{body_text}")
        print(f"  [debug] === dump end ===")
    except Exception as e:
        print(f"  [debug] dump 失败: {e}")


def make_browser(p, proxy_uri: str = ""):
    """启动带反检测特征的 Chromium，可选配置代理"""
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
    # 代理配置：如果配了 PROXY_NODE，走本地 Xray 的 SOCKS5
    launch_kwargs = {"headless": True, "args": launch_args}
    if proxy_uri:
        launch_kwargs["proxy"] = {
            "server": f"socks5://127.0.0.1:{LOCAL_SOCKS_PORT}",
        }
        print(f"  [proxy] Playwright 走本地 SOCKS5 127.0.0.1:{LOCAL_SOCKS_PORT} → Xray → {get_proxy_protocol(proxy_uri)}")
    else:
        print(f"  [proxy] 未配置代理，直连访问")
    return p.chromium.launch(**launch_kwargs)


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


def test_proxy_alive(timeout: int = 10) -> bool:
    """检测本地 SOCKS5 代理是否可用"""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(timeout)
        s.connect(("127.0.0.1", LOCAL_SOCKS_PORT))
        s.close()
        return True
    except Exception:
        return False


def test_proxy_works(timeout: int = 20) -> tuple:
    """通过代理实际访问一个网站，验证代理出网是否正常。返回 (success, message)"""
    proxies = {
        "http":  f"socks5h://127.0.0.1:{LOCAL_SOCKS_PORT}",
        "https": f"socks5h://127.0.0.1:{LOCAL_SOCKS_PORT}",
    }
    try:
        # 用 ipinfo.io 拿出口 IP
        r = requests.get("https://ipinfo.io/json", proxies=proxies, timeout=timeout)
        if r.status_code == 200:
            d = r.json()
            return True, f"出口 IP: {d.get('ip')} ({d.get('country')}, {d.get('city')}, {d.get('org', '')[:50]})"
        return False, f"ipinfo 返回 {r.status_code}"
    except Exception as e:
        return False, f"代理出网失败: {e}"


def renew_one(cookie_str: str, index: int, use_proxy: bool) -> tuple:
    cookies = parse_cookie_string(cookie_str)
    if not cookies:
        return False, "Cookie 格式无效（未找到 key=value 对）"

    print(f"\n=== 账号 {index} 开始续期 ===")
    with sync_playwright() as p:
        browser = make_browser(p, proxy_uri=PROXY_NODE if use_proxy else "")
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

            page.wait_for_timeout(2000)

            if not wait_for_cf_clearance(page, max_sec=CF_WAIT_MAX_SEC, stage="homepage"):
                save_debug_screenshot(page, "homepage_cf_blocked", index)
                return False, (
                    "Cloudflare 拦截，未能通过验证。\n"
                    "建议：1) 检查 PROXY_NODE 是否有效  2) 更换代理节点  "
                    "3) 用住宅 IP 代理  4) 改用本地运行"
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
                save_debug_screenshot(page, "no_renew_button", index)
                dump_page_elements(page, index)
                return False, (
                    "未发现 Renew 按钮（dashboard 上没有任何匹配的按钮）。"
                    "请查看 Actions artifacts 中的调试截图。"
                    "可能原因：1) 账号未到期  2) 按钮选择器未覆盖  3) 页面结构变化"
                )

            # ===== 阶段 4: 等待结果 =====
            print("  [4/4] 等待续期结果...")
            page.wait_for_timeout(3000)

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
    print("  Weirdhost Auto Renew (v5 - sing-box + 7 Protocols)")
    print("=" * 60)
    print(f"  stealth: {'✓ 已加载' if HAS_STEALTH else '✗ 未安装 playwright-stealth'}")

    # ===== 代理检查 =====
    use_proxy = bool(PROXY_NODE)
    if use_proxy:
        proto = get_proxy_protocol(PROXY_NODE)
        print(f"  proxy:  ✓ 已配置 {proto} 协议")
        print(f"          (本地 SOCKS5 127.0.0.1:{LOCAL_SOCKS_PORT})")

        # 检测本地代理是否启动
        if not test_proxy_alive(timeout=5):
            msg = f"本地 SOCKS5 代理 127.0.0.1:{LOCAL_SOCKS_PORT} 未启动，请确认 Xray 已运行"
            print(f"  [ERROR] {msg}")
            notify("代理错误", msg)
            sys.exit(1)
        print(f"  proxy:  ✓ 本地代理端口可达")

        # 测试代理出网
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
        ok, msg = renew_one(ck, i, use_proxy)
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
