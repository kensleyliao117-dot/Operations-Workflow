"""只读核对创作中心中本次演练作品的状态和可见范围。"""
import json
import argparse
from datetime import datetime, timezone
from urllib.parse import urlsplit
from urllib.parse import urlencode
from collectors.chrome import ChromeBrowser
from collectors.rednote import RednoteCollector
from integrations.publishers.rednote import read_account, _editor_text, PREVIEW_SELECTOR
from utils.run_private_flow import REPORT


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--read-detail", action="store_true")
    parser.add_argument("--inspect-controls", action="store_true")
    parser.add_argument("--inspect-actions", action="store_true")
    parser.add_argument("--read-editor", action="store_true")
    options = parser.parse_args()
    report = json.loads(REPORT.read_text(encoding="utf-8"))
    title = report["image_result"]["content"]["title"]
    with ChromeBrowser() as browser:
        pages = [p for p in browser.context.pages if urlsplit(p.url).hostname == "creator.xiaohongshu.com"]
        if not pages:
            raise RuntimeError("Creator tab not found")
        page = pages[-1]
        focus = browser.context.new_cdp_session(page)
        focus.send("Emulation.setFocusEmulationEnabled", {"enabled": True})
        if read_account(page) != report["browser_account_id"]:
            raise RuntimeError("Browser account changed")
        observed, response_shapes, candidates = [], [], []
        def inspect_response(response):
            parsed = urlsplit(response.url)
            if parsed.hostname not in {"creator.xiaohongshu.com", "edith.xiaohongshu.com"}:
                return
            try:
                payload = response.json()
                if isinstance(payload, dict):
                    data = payload.get("data")
                    response_shapes.append({"path": parsed.path, "keys": list(payload),
                        "data_keys": list(data) if isinstance(data, dict) else type(data).__name__})
                def walk(value):
                    if isinstance(value, dict):
                        if any(isinstance(v, str) and v == title for v in value.values()):
                            keys = {"id", "note_id", "title", "display_title", "displayTitle", "permission", "visibility", "status", "create_time", "time", "noteId", "note_title", "noteTitle", "privacy", "level", "publish_time", "likes", "comments_count", "collected_count", "view_count", "shared_count", "permission_code", "permission_msg", "tab_status"}
                            observed.append({"path": parsed.path, "keys": list(value), "note": {k:v for k,v in value.items() if k in keys}})
                            if parsed.path == "/api/galaxy/v2/creator/note/user/posted":
                                candidates.append(value)
                        for child in value.values():
                            walk(child)
                    elif isinstance(value, list):
                        for child in value:
                            walk(child)
                walk(payload)
            except Exception:
                pass
        page.on("response", inspect_response)
        page.reload(wait_until="domcontentloaded", timeout=30000)
        page.get_by_text(title, exact=False).first.wait_for(state="visible", timeout=20000)
        result = {"path": urlsplit(page.url).path, "title": title}
        matches = page.get_by_text(title, exact=False)
        result["match_count"] = matches.count()
        result["cards"] = matches.evaluate_all("""items => items.map(e=>{
            const rows=[]; for(let i=0;e&&i<5;e=e.parentElement,i++)rows.push({tag:e.tagName,cls:e.className,text:e.innerText.slice(0,1200),
              links:Array.from(e.querySelectorAll('a')).map(a=>({path:new URL(a.href).pathname,text:a.innerText})),
              attrs:Array.from(e.attributes).filter(a=>/id|note|data/.test(a.name)).map(a=>[a.name,a.value])}); return rows;
        })""")
        result["page_text"] = page.inner_text("body")[:5000]
        result["observed_notes"] = observed
        result["observed_at"] = datetime.now(timezone.utc).isoformat()
        result["response_shapes"] = response_shapes
        result["tabs"] = [{"host": urlsplit(p.url).hostname, "path": urlsplit(p.url).path} for p in browser.context.pages]
        if options.inspect_controls:
            card = matches.first.locator("xpath=ancestor::div[contains(concat(' ', normalize-space(@class), ' '), ' note-card ')][1]")
            card.hover()
            result["card_controls"] = card.evaluate("""e=>Array.from(e.querySelectorAll('*')).filter(n=>n.getClientRects().length).map(n=>({tag:n.tagName,cls:n.className,text:n.children.length?'':n.innerText,title:n.getAttribute('title'),label:n.getAttribute('aria-label')}))""")
        if options.inspect_actions:
            card = matches.first.locator("xpath=ancestor::div[contains(concat(' ', normalize-space(@class), ' '), ' note-card ')][1]")
            card.hover()
            actions = card.locator(".note-card__action-btn")
            labels = []
            for index in range(actions.count()):
                actions.nth(index).hover()
                page.wait_for_timeout(400)
                labels.append({"index": index, "class": actions.nth(index).get_attribute("class"),
                    "tips": page.locator('[role="tooltip"], [class*="tooltip"]:visible').all_text_contents()})
            result["action_labels"] = labels
        if options.read_editor:
            if len(candidates) != 1 or matches.count() != 1:
                raise RuntimeError("Published note is not unique")
            card = matches.first.locator("xpath=ancestor::div[contains(concat(' ', normalize-space(@class), ' '), ' note-card ')][1]")
            card.hover()
            edit = card.locator(".note-card__action-btn").nth(3)
            edit.hover()
            page.get_by_text("编辑", exact=True).wait_for(state="visible")
            opened = []
            browser.context.on("page", lambda created: opened.append(created))
            edit.click()
            page.wait_for_timeout(1000)
            editor = opened[-1] if opened else page
            editor.wait_for_load_state("domcontentloaded")
            editor.locator('[contenteditable="true"]').wait_for(state="visible", timeout=30000)
            if read_account(editor) != report["browser_account_id"]:
                raise RuntimeError("Editor account changed")
            result["published_editor"] = {"path": urlsplit(editor.url).path,
                "title": editor.get_by_placeholder("填写标题会有更多赞哦").input_value(),
                "body": _editor_text(editor.locator('[contenteditable="true"]')),
                "image_count": editor.locator(PREVIEW_SELECTOR).count()}
            if opened:
                editor.close()
            else:
                page.go_back(wait_until="domcontentloaded")
        if options.read_detail:
            if len(candidates) != 1:
                raise RuntimeError("Published note is not unique")
            note = candidates[0]
            source = "https://www.xiaohongshu.com/explore/" + note["id"]
            if note.get("xsec_token") and note.get("xsec_source"):
                source += "?" + urlencode({"xsec_token": note["xsec_token"], "xsec_source": note["xsec_source"]})
            detail = RednoteCollector(browser).collect(source)
            result["published_detail"] = {"title": detail.title, "body": detail.body,
                "author_platform_id": detail.author_platform_id, "image_count": len(detail.image_urls),
                "metrics": detail.metrics, "metrics_raw": detail.metrics_raw}
        (REPORT.parent / "publication-observation.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
