#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Weirdhost Auto Renew
- Cookie 模式登录（无需账号密码）
- 多账号支持（WEIRDH0ST_COOKIE_1, _2, _3, ...）
- Telegram Bot + Webhook 双通道通知
- Cloudflare 拦截检测
- 完整异常处理 + finally 资源回收
"""

import os
import sys
import json
import re
import time
import traceback
from typing import Optional

import requests
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeoutError, Error as PWError

# ===== 配置 =====
BASE_URL = "https://hub.weirdhost.xyz"
DASHBOARD_URL = f"{BASE_URL}/dashboard"
LOGIN_URL = f"{BASE_URL}/login"

# 最大 Cookie 数量（自动扫描 WEIRDH0ST_COOKIE_1 ~ WEIRDH0ST_COOKIE_50）
MAX_COOKIE_SLOTS = 50

# ===== 通知配置 =====
TG_BOT_TOKEN = os.getenv("TG_BOT_TOKEN", "").strip()
TG_CHAT_ID = os.getenv("TG_CHAT_ID", "").strip()
WEBHOOK_URL = os.getenv("WEBHOOK_URL", "").strip()  # 可选，与 TG 并存

# Cloudflare 拦截检测关键词
CF_SIGNALS = [
    "just a moment",
    "checking your browser",
    "verifying you are human",
    "cf-challenge",
    "cf_chl_opt",
    "attention required",
]


def notify(title: str, content: str) -> None:
    """发送通知到 Telegram + Webhook（任一配置即生效，互不影响）"""
    payload = {"title": title, "content": content, "ts": int(time.time())}

    # --- Telegram ---
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
                # Markdown 转义失败时降级为纯文本
                requests.post(
                    f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage",
                    json={"chat_id": TG_CHAT_ID, "text": f"[{title}]\n\n{content}"},
                    timeout=15,
                )
        except Exception as e:
            print(f"[notify] Telegram 发送失败: {e}", file=sys.stderr)

    # --- Webhook ---
    if WEBHOOK_URL:
        try:
            requests.post(WEBHOOK_URL, json=payload, timeout=15)
        except Exception as e:
            print(f"[notify] Webhook 发送失败: {e}", file=sys.stderr)


def load_cookies() -> list[str]:
    """读取所有 WEIRDH0ST_COOKIE_N 环境变量"""
    cookies = []
    for i in range(1, MAX_COOKIE_SLOTS + 1):
        v = os.getenv(f"WEIRDH0ST_COOKIE_{i}", "").strip()
        if v:
            cookies.append(v)
    return cookies


def parse_cookie_string(cookie_str: str) -> list[dict]:
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
    return any(sig in content for sig in CF_SIGNALS)


def is_logged_in(page) -> bool:
    """通过 URL 判断是否已登录（被踢回 /login 即未登录）"""
    try:
        url = page.url.lower()
    except Exception:
        return False
    return "/login" not in url and "signin" not in url


def renew_one(cookie_str: str, index: int) -> tuple[bool, str]:
    """
    对单个账号执行续期，返回 (success, message)
    使用 try/finally 确保 browser 一定被关闭
    """
    cookies = parse_cookie_string(cookie_str)
    if not cookies:
        return False, "Cookie 格式无效（未找到 key=value 对）"

    print(f"\n=== 账号 {index} 开始续期 ===")
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
            ],
        )
        try:
            context = browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
                viewport={"width": 1366, "height": 768},
            )
            context.add_cookies(cookies)
            page = context.new_page()

            # 1. 访问首页，检测 CF
            try:
                page.goto(BASE_URL, wait_until="domcontentloaded", timeout=30000)
            except PWTimeoutError:
                return False, "访问首页超时（30s）"

            try:
                page.wait_for_load_state("networkidle", timeout=15000)
            except PWTimeoutError:
                pass  # CF 拦截页通常不会进入 networkidle

            if is_cf_blocked(page):
                return False, "Cloudflare 拦截，请稍后重试或更换网络环境"

            # 2. 访问 dashboard 验证 Cookie 有效性
            try:
                page.goto(DASHBOARD_URL, wait_until="domcontentloaded", timeout=30000)
            except PWTimeoutError:
                return False, "访问 dashboard 超时"

            # 等待可能的跳转
            page.wait_for_timeout(3000)

            if not is_logged_in(page):
                return False, "Cookie 已失效，请重新登录获取最新 Cookie"

            # 3. 查找并点击 Renew 按钮
            renew_selectors = [
                "button:has-text('Renew')",
                "button:has-text('renew')",
                "button:has-text('续期')",
                "a:has-text('Renew')",
                "a:has-text('续期')",
                "[class*='renew']:not([disabled])",
                "[data-action='renew']",
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
                # 可能 dashboard 上没有 Renew 按钮（已自动续期？）
                return True, "未发现 Renew 按钮，可能本周期已自动续期"

            # 4. 等待续期结果（监测成功/失败提示）
            page.wait_for_timeout(3000)

            # 检测页面是否出现成功提示
            try:
                body_text = page.inner_text("body", timeout=5000).lower()
            except Exception:
                body_text = ""

            success_signals = ["success", "renewed", "续期成功", "已续期", "updated"]
            fail_signals = ["failed", "error", "失败", "expired", "forbidden"]

            if any(s in body_text for s in fail_signals):
                return False, f"续期失败，页面反馈: {body_text[:200]}"
            if any(s in body_text for s in success_signals):
                return True, "续期成功"

            # 默认认为点击成功即续期成功
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
    print("  Weirdhost Auto Renew")
    print("=" * 60)

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

    # 汇总通知
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

    # 失败时非零退出，便于 Actions 标红
    sys.exit(0 if fail_count == 0 else 1)


if __name__ == "__main__":
    main()
