"""只读检查已打开的创作中心控件，不填写表单、不上传或发布。"""
import json
import argparse
from urllib.parse import urlsplit

from collectors.chrome import ChromeBrowser
from integrations.publishers.rednote import ACCOUNT_SCRIPT, PREVIEW_SELECTOR


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--refresh-account", action="store_true")
    options = parser.parse_args()
    with ChromeBrowser() as browser:
        pages = [page for page in browser.context.pages
                 if urlsplit(page.url).hostname == "creator.xiaohongshu.com"]
        if not pages:
            print("No creator tab found")
            print(json.dumps([{"host": urlsplit(p.url).hostname, "path": urlsplit(p.url).path}
                              for p in browser.context.pages], ensure_ascii=False))
            return
        page = pages[-1]
        account_responses = []
        if options.refresh_account:
            if page.locator('[contenteditable="true"], input[type="text"]').count():
                raise RuntimeError("Editor found; refresh skipped to preserve draft")
            def inspect_response(response):
                parsed = urlsplit(response.url)
                if (parsed.hostname not in {"creator.xiaohongshu.com", "edith.xiaohongshu.com"}
                        or not any(word in parsed.path for word in ("user", "account"))):
                    return
                item = {"path": parsed.path, "status": response.status, "method": response.request.method}
                try:
                    value = response.json()
                    data = value.get("data", {}) if isinstance(value, dict) else {}
                    item["data_keys"] = list(data) if isinstance(data, dict) else []
                    if isinstance(data, dict):
                        item["public_ids"] = {k: data[k] for k in ("user_id", "userId", "id", "uid") if k in data}
                except Exception:
                    pass
                account_responses.append(item)
            page.on("response", inspect_response)
            page.reload(wait_until="domcontentloaded")
            page.wait_for_timeout(3000)
        result = page.evaluate("""() => ({
            path: location.pathname,
            inputs: Array.from(document.querySelectorAll('input')).map(e => ({type:e.type,
                placeholder:e.placeholder, accept:e.accept, multiple:e.multiple, cls:e.className, maxLength:e.maxLength})),
            editors: Array.from(document.querySelectorAll('[contenteditable="true"]')).map(e => ({tag:e.tagName,cls:e.className})),
            buttons: Array.from(document.querySelectorAll('button, xhs-publish-btn')).map(e => ({tag:e.tagName,
                label:e.innerText.trim().slice(0,50), disabled:e.disabled, cls:e.className,
                submitDisabled:e.getAttribute('submit-disabled'), isPublish:e.getAttribute('is-publish')})),
            stateKeys: Object.keys(window).filter(k => /INITIAL_STATE|PINIA|NUXT|USER_INFO/i.test(k)),
            userStateKeys: Object.keys(window.__INITIAL_STATE__?.user || {}),
            profileLinks: Array.from(document.querySelectorAll('a[href*="/user/profile/"]')).map(a => new URL(a.href).pathname)
        })""")
        result["account_candidates"] = page.evaluate(ACCOUNT_SCRIPT)
        result["previews"] = page.locator(PREVIEW_SELECTOR).count()
        result["account_responses"] = account_responses
        print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
