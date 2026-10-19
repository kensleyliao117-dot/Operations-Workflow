"""准备私密演练的浏览器草稿并检查控件；不点击发布，不创建审核记录。"""
import argparse
import json
import re
from urllib.parse import urlsplit

import config
from collectors.chrome import ChromeBrowser
from database import Sessionlocal
from integrations.publishers.rednote import CREATOR_URL, PREVIEW_SELECTOR, read_account, _one, _set_visibility, _editor_text
from models.content import Content
from services.common import get_record
from utils.run_private_flow import REPORT, save


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("stage", choices=["upload", "inspect", "fill"])
    args = parser.parse_args()
    report = json.loads(REPORT.read_text(encoding="utf-8"))
    with Sessionlocal() as db:
        db.info["user_id"] = report["user_id"]
        content = get_record(db, Content, report["content_id"])
        title, body = content.title, content.body
        editor_body = body + "\n\n" + " ".join("#" + tag.lstrip("#") for tag in content.tags)
        paths = [(config.STORAGE_DIR / item["storage_key"]).resolve() for item in content.image_assets]
        for path in paths:
            if not path.is_relative_to(config.GENERATED_IMAGE_DIR.resolve()) or not path.is_file():
                raise RuntimeError("Invalid generated image path")
    with ChromeBrowser() as browser:
        pages = [p for p in browser.context.pages if urlsplit(p.url).hostname == "creator.xiaohongshu.com"
                 and urlsplit(p.url).path == "/publish/publish"]
        if not pages:
            if args.stage != "upload":
                raise RuntimeError("Open the creator image editor first")
            page = browser.context.new_page()
            page.goto(CREATOR_URL, wait_until="domcontentloaded")
            pages = [page]
        page = pages[-1]
        page.set_default_timeout(config.CHROME_PAGE_TIMEOUT_MS)
        page.bring_to_front()
        session = browser.context.new_cdp_session(page)
        session.send("Emulation.setFocusEmulationEnabled", {"enabled": True})
        window = session.send("Browser.getWindowForTarget")
        if args.stage == "fill":
            session.send("Browser.setWindowBounds", {"windowId": window["windowId"], "bounds": {"windowState": "normal"}})
        account = read_account(page)
        if report.get("browser_account_id", account) != account:
            raise RuntimeError("Browser account changed")
        report["browser_account_id"] = account
        save(report)
        if args.stage == "upload":
            if page.locator('[contenteditable="true"]').count():
                raise RuntimeError("Editor already contains a draft; inspect instead")
            upload = page.locator('input[type="file"][accept*="image"], input[type="file"][accept*=".png"]')
            upload.first.wait_for(state="attached")
            if upload.count() != 1:
                raise RuntimeError("Image upload input is not unique")
            upload.set_input_files([str(path) for path in paths])
            page.locator('[contenteditable="true"]').wait_for(timeout=90000)
        if args.stage == "fill":
            guide = page.locator("button.feature-guide__btn")
            if guide.count() == 1 and guide.is_visible():
                guide.click()
            _one(page.get_by_placeholder(re.compile("填写标题|输入标题|标题")), "Title input unavailable").fill(title)
            editor = _one(page.locator('[contenteditable="true"]'), "Body input unavailable")
            editor.fill(editor_body)
            editor.press("Escape")
            _set_visibility(page, "private")
            report["private_preview"] = {"content_id": report["content_id"], "account_id": account}
            save(report)
        result = page.evaluate("""() => ({
            visibility: document.visibilityState,
            permissionBounds: Array.from(document.querySelectorAll('div.permission-card-wrapper div.d-select-content')).map(e => ({rect:e.getBoundingClientRect().toJSON(),outer:e.outerHTML,parents:Array.from((function*(n){for(let i=0;n&&i<5;n=n.parentElement,i++)yield n})(e)).map(p=>({cls:p.className,overflow:getComputedStyle(p).overflow,rect:p.getBoundingClientRect().toJSON()}))})),
            inputs: Array.from(document.querySelectorAll('input')).map(e => ({type:e.type,placeholder:e.placeholder,cls:e.className})),
            editors: Array.from(document.querySelectorAll('[contenteditable="true"]')).map(e => ({tag:e.tagName,cls:e.className})),
            buttons: Array.from(document.querySelectorAll('button, xhs-publish-btn')).map(e => ({tag:e.tagName,label:e.innerText.trim().slice(0,50),cls:e.className,disabled:e.disabled})),
            permissions: Array.from(document.querySelectorAll('[class*="permission"], [class*="select-content"], [class*="custom-option"]')).map(e => ({tag:e.tagName,cls:e.className,text:e.innerText.trim().slice(0,180)})),
            previewContainers: Array.from(document.querySelectorAll('[class*="img-preview"], [class*="img-list"], [class*="image-list"], [class*="upload-list"]')).map(e => ({cls:e.className,children:Array.from(e.children).map(c=>c.className)}))
        })""")
        result.update(account_id=account, preview_count=page.locator(PREVIEW_SELECTOR).count())
        editors = page.locator('[contenteditable="true"]')
        if editors.count() == 1:
            actual = editors.inner_text().replace("\r\n", "\n")
            result["body_comparison"] = {"matches": actual.strip() == editor_body.strip(),
                "logical_matches": _editor_text(editors) == editor_body.strip(),
                "expected_length": len(editor_body), "actual_length": len(actual),
                "expected": editor_body, "actual": actual, "html": editors.inner_html()}
            result["title_value"] = page.get_by_placeholder(re.compile("填写标题|输入标题|标题")).input_value()
        screenshot = REPORT.parent / "editor-preview.png"
        page.screenshot(path=str(screenshot), full_page=True)
        print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
