import os
import requests
from playwright.sync_api import sync_playwright

EMAIL = os.getenv("EMAIL")
PASSWORD = os.getenv("PASSWORD")
GT_URL = os.getenv("GT_URL")

BASE_URL = "https://hub.weirdhost.xyz"


def notify(title, content):
    if GT_URL:
        try:
            requests.post(GT_URL, json={"title": title, "content": content})
        except:
            pass


with sync_playwright() as p:
    browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
    page = browser.new_page()

    page.goto(BASE_URL)

    if "just a moment" in page.content().lower():
        notify("CF拦截", "Cloudflare拦截")
        browser.close()
        exit()

    try:
        page.fill('input[type="email"]', EMAIL)
        page.fill('input[type="password"]', PASSWORD)
        page.click('button[type="submit"]')
    except:
        notify("登录失败", "账号输入失败")
        browser.close()
        exit()

    page.wait_for_timeout(5000)
    page.goto(BASE_URL + "/dashboard")

    try:
        page.click("text=Renew")
        notify("续期成功", "OK")
    except Exception as e:
        notify("续期失败", str(e))

    browser.close()
