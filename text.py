from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.connect_over_cdp(
        "http://127.0.0.1:9222"
    )

    context = browser.contexts[0]
    page = context.new_page()

    page.goto("https://example.com")
    print(page.title())

    page.close()
    browser.close()