import { chromium } from 'playwright';

let browser;

export async function getBrowser() {
    if (!browser) {
        browser = await chromium.launch({
            headless: false, // 先用 false，稳定后可改 true
        });
    }
    return browser;
}

export async function newPage() {
    const b = await getBrowser();
    const page = await b.newPage();

    await page.setUserAgent(
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/122 Safari/537.36"
    );

    return page;
}
