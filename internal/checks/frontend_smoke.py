from playwright.sync_api import sync_playwright


def main():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, executable_path=r"C:\Program Files\Google\Chrome\Application\chrome.exe")
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.goto("http://127.0.0.1:8791", wait_until="networkidle")
        page.get_by_role("heading", name="订单提醒工作台").wait_for()
        page.locator("#import-file").set_input_files(r"C:\Users\Administrator.DESKTOP-OV0JDHK\Desktop\work\order-1787727280.xlsx")
        page.locator("#order-table-body tr").first.wait_for(timeout=30000)
        assert page.locator("#order-table-body tr").count() == 62
        page.locator("#order-table-body tr").first.click()
        page.locator("#order-drawer.open").wait_for()
        assert page.locator("#detail-date-explanation").inner_text()
        page.locator("#drawer-close").click()
        page.locator("#settings-trigger").click()
        page.locator("#settings-dialog").wait_for()
        page.locator("#settings-close").click()
        page.locator("#search-input").fill("114-5529401-5675431")
        page.locator("#search-input").press("Enter")
        page.screenshot(path="internal/checks/frontend-smoke.png", full_page=True)
        page.set_viewport_size({"width": 390, "height": 844})
        page.screenshot(path="internal/checks/frontend-smoke-mobile.png", full_page=True)
        browser.close()


if __name__ == "__main__":
    main()
