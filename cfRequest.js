import { newPage } from './browser.js';

export async function cfRequest(url) {
    const page = await newPage();

    try {
        console.log("[+] opening:", url);

        await page.goto(url, {
            waitUntil: 'domcontentloaded',
            timeout: 60000
        });

        // 等 Cloudflare 自动跳过
        await page.waitForTimeout(5000);

        const content = await page.content();

        // 简单检测 CF 是否还在
        if (content.includes("Checking your browser") ||
            content.includes("cf-browser-verification")) {
            throw new Error("Cloudflare still active");
        }

        return content;

    } catch (err) {
        console.log("[-] CF failed, retrying...");
        await page.close().catch(() => {});
        throw err;
    }
}
