"""Isolated browser checks, never loading .env or connecting to production services.

Optional dev dependencies: pip install playwright; playwright install chromium
On Windows the script can also use an already installed Microsoft Edge.
Run from the repository root: python scripts/pwa_browser_smoke.py
"""

import json
import logging
import os
from pathlib import Path
import sys
import tempfile
from threading import Thread

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import dotenv
from playwright.sync_api import sync_playwright, expect
from flask import Response
from werkzeug.serving import make_server, WSGIRequestHandler


def check_metadata(page):
    expect(page.locator('link[rel="manifest"]')).to_have_count(1)
    expect(page.locator('link[rel="apple-touch-icon"]')).to_have_count(1)
    assert page.evaluate("window.__ennoicPwaStarted") is True


def cache_paths(page):
    return page.evaluate("""async () => {
      const names = (await caches.keys()).filter(name => name.startsWith('ennoic-public-offline-'));
      return Promise.all(names.map(async name => ({name, paths: (await (await caches.open(name)).keys()).map(r => new URL(r.url).pathname)})));
    }""")


class QuietHandler(WSGIRequestHandler):
    def log(self, *args, **kwargs):
        pass


def main():
    dotenv.load_dotenv = lambda *args, **kwargs: False
    for key in list(os.environ):
        if key.startswith(("ENNOIC_", "GOOGLE_", "GROQ_", "PADDLE_", "RAZORPAY_", "SMTP_", "RESEND_")) or key in {"DATABASE_URL", "DISCORD_WEBHOOK_URL"}:
            os.environ.pop(key, None)
    os.environ["ENNOIC_SECRET_KEY"] = "isolated-pwa-smoke-secret-over-32-characters"
    with tempfile.TemporaryDirectory(prefix="ennoic-pwa-smoke-") as directory:
        os.environ["ENNOIC_TASK_DB"] = str(Path(directory) / "test.sqlite3")
        import app as application
        import account_store

        application.app.config.update(TESTING=True, WTF_CSRF_ENABLED=True, RATELIMIT_ENABLED=False)
        application.limiter.enabled = False
        logging.getLogger("werkzeug").setLevel(logging.ERROR)
        account_store.initialize()
        user = account_store.create_user("pwa-smoke@example.com", "local-smoke-password-2026")
        account_store.verify_email(user["id"])
        # Exercise the real chat endpoint/persistence/UI without a billable AI call.
        application.process_message_with_metadata = lambda *a, **k: {"reply": "Local AI transport test succeeded."}
        # Prevent any incidental external server-side request in this test process.
        application.requests.sessions.Session.request = lambda *a, **k: (_ for _ in ()).throw(AssertionError("External server request blocked"))

        version = {"number": 1}
        worker_source = (ROOT / "static/pwa/service-worker.js").read_text(encoding="utf-8")

        def worker_response():
            # Change actual response bytes without retaining a file-based v1 ETag.
            response = Response(worker_source.replace("${CACHE_PREFIX}v1", f"${{CACHE_PREFIX}}v{version['number']}"),
                                mimetype="application/javascript")
            response.headers["Cache-Control"] = "no-cache, max-age=0, must-revalidate"
            response.headers["Service-Worker-Allowed"] = "/"
            return response

        application.app.view_functions["pwa.service_worker"] = worker_response
        server = make_server("127.0.0.1", 0, application.app, threaded=True, request_handler=QuietHandler)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        origin = f"http://127.0.0.1:{server.server_port}"
        output = ROOT / "instance/pwa-qa"
        output.mkdir(parents=True, exist_ok=True)
        results = {}
        try:
            with sync_playwright() as playwright:
                edge = Path("C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe")
                launch_options = {"executable_path": str(edge)} if edge.exists() else {}
                # Installation eligibility is disabled in incognito contexts.
                context = playwright.chromium.launch_persistent_context(
                    str(Path(directory) / "browser-profile"),
                    viewport={"width": 1440, "height": 1000}, **launch_options,
                )
                browser = context.browser
                page = context.new_page()
                errors = []
                page.on("pageerror", lambda error: (errors.append(str(error)), print("PAGE ERROR:", str(error))))
                page.goto(origin + "/")
                expect(page.locator("html.ennoic-landing-document")).to_be_attached()
                expect(page.locator("#dc-root")).to_be_attached()
                check_metadata(page)
                # Promise listeners avoid Playwright's eval-based polling under CSP.
                page.evaluate("""async () => {
                  await navigator.serviceWorker.ready;
                  if (!navigator.serviceWorker.controller) await new Promise(resolve =>
                    navigator.serviceWorker.addEventListener('controllerchange', () => resolve(true), {once: true}));
                  return true;
                }""")
                session = context.new_cdp_session(page)
                results["chromium_installability"] = session.send("Page.getInstallabilityErrors")
                assert results["chromium_installability"]["installabilityErrors"] == [], results["chromium_installability"]
                # Exercise the optional control; this is a simulated prompt, not native installation.
                page.evaluate("""() => {
                  const event = new Event('beforeinstallprompt', {cancelable: true});
                  event.prompt = async () => { window.promptCalls = (window.promptCalls || 0) + 1; };
                  event.userChoice = Promise.resolve({outcome: 'dismissed'});
                  window.dispatchEvent(event);
                }""")
                page.get_by_role("button", name="Install Ennoic", exact=True).click()
                expect(page.get_by_role("button", name="Install Ennoic", exact=True)).to_have_count(0)
                assert page.evaluate("window.promptCalls") == 1
                results["optional_install_control"] = "passed (simulated browser event)"

                page.goto(origin + "/login")
                expect(page.locator("#email")).to_be_visible()
                check_metadata(page)
                page.locator("#email").fill(user["email"])
                page.locator("#password").fill("local-smoke-password-2026")
                page.get_by_role("button", name="Continue", exact=True).click()
                page.wait_for_load_state("load")
                expect(page.locator("#app")).to_be_visible()
                expect(page.locator("#task-status")).to_contain_text("0 tasks")
                check_metadata(page)
                results["login"] = "passed through existing sign-in form"
                page.locator('[data-task-view="open"]').click()
                page.locator("#create-task").click()
                page.locator("#task-title-input").fill("PWA smoke task")
                page.locator("#save-task").click()
                expect(page.locator(".task-title", has_text="PWA smoke task")).to_be_visible()
                page.get_by_role("button", name="Edit PWA smoke task", exact=True).click()
                page.locator("#task-title-input").fill("PWA edited task")
                page.locator("#save-task").click()
                expect(page.locator(".task-title", has_text="PWA edited task")).to_be_visible()
                page.get_by_role("checkbox", name="Complete PWA edited task", exact=True).check()
                page.locator('[data-task-view="completed"]').click()
                expect(page.locator(".task-title", has_text="PWA edited task")).to_be_visible()
                results["task_create_edit_complete"] = "passed through existing task UI"
                page.locator('[data-task-view="open"]').click()
                page.locator("#create-task").click()
                page.locator("#task-title-input").fill("PWA delete test")
                page.locator("#save-task").click()
                expect(page.locator(".task-title", has_text="PWA delete test")).to_be_visible()
                page.once("dialog", lambda dialog: dialog.accept())
                page.get_by_role("button", name="Delete PWA delete test", exact=True).click()
                expect(page.locator(".task-title", has_text="PWA delete test")).to_have_count(0)
                page.locator('[data-task-view="completed"]').click()
                results["task_deletion"] = "passed with disposable local test task"
                page.locator("#composer-input").fill("Hello from the PWA test")
                page.locator("#send").click()
                expect(page.locator("#messages")).to_contain_text("Local AI transport test succeeded.")
                results["chat"] = "passed with mocked AI provider and real endpoint/storage"
                page.locator("#new-chat").click()
                page.locator("#composer-input").fill("Second conversation history test")
                page.locator("#send").click()
                expect(page.locator("#messages")).to_contain_text("Local AI transport test succeeded.")
                expect(page.locator(".history-item")).to_have_count(2)
                if not page.locator(".history-item").first.is_visible():
                    page.locator("#toggle-sidebar").click()
                page.locator(".history-item").filter(has_text="Hello from the PWA test").click()
                expect(page.locator("#messages")).to_contain_text("Hello from the PWA test")
                expect(page.locator("#messages")).not_to_contain_text("Second conversation history test")
                results["conversation_history"] = "passed two saved conversations and switching back"
                page.screenshot(path=str(output / "desktop.png"))

                version["number"] = 2
                page.evaluate("""() => new Promise(async (resolve, reject) => {
                  navigator.serviceWorker.addEventListener('controllerchange', () => resolve(true), {once: true});
                  try { await (await navigator.serviceWorker.getRegistration()).update(); }
                  catch (error) { reject(error); }
                })""")
                page.evaluate("""async () => {
                  const deadline = Date.now() + 10000;
                  while (Date.now() < deadline) {
                    const keys = await caches.keys();
                    if (keys.includes('ennoic-public-offline-v2') && !keys.includes('ennoic-public-offline-v1')) return true;
                    await new Promise(resolve => setTimeout(resolve, 50));
                  }
                  throw new Error('Updated offline cache did not activate');
                }""")
                expect(page.locator("#messages")).to_contain_text("Local AI transport test succeeded.")
                results["worker_update_without_reload"] = "passed v1 to v2, old cache removed, chat retained"
                paths = cache_paths(page)
                assert [item["name"] for item in paths] == ["ennoic-public-offline-v2"], paths
                assert sorted(paths[0]["paths"]) == sorted(["/offline", "/static/pwa/offline.css", "/static/pwa/offline.js", "/static/email/ennoic-logo.png"])
                results["cache_privacy_after_authenticated_use"] = paths

                # Reopen the page in the same browser context to retain the existing session cookie.
                page.close()
                page = context.new_page()
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(origin + "/")
                expect(page.locator("#app")).to_be_visible()
                expect(page.locator("#messages")).to_contain_text("Local AI transport test succeeded.")
                results["close_reopen_session"] = "passed (same browser context)"
                context.set_offline(True)
                page.goto(origin + "/settings")
                expect(page.get_by_role("heading", name="You're offline")).to_be_visible()
                page.screenshot(path=str(output / "offline.png"))
                context.set_offline(False)
                expect(page.locator("#network-status")).to_contain_text("Connection restored")
                page.get_by_role("link", name="Try again").click()
                expect(page.locator("#app")).to_be_visible()
                results["offline_and_recovery"] = "passed; generic fallback only"
                page.goto(origin + "/settings")
                expect(page.locator("#settings-app")).to_be_visible()
                page.locator('[data-section="account"]').click()
                page.once("dialog", lambda dialog: dialog.accept())
                page.locator('#settings-sign-out-form button').click()
                expect(page.locator("html.ennoic-landing-document")).to_be_attached()
                assert page.evaluate("fetch('/api/auth/me').then(r => r.json()).then(j => j.authenticated)") is False
                results["logout"] = "passed through existing settings form"

                mobile = browser.new_context(**playwright.devices["Pixel 7"])
                mobile_page = mobile.new_page()
                mobile_page.on("pageerror", lambda error: errors.append(str(error)))
                mobile_page.goto(origin + "/")
                expect(mobile_page.locator("html.ennoic-landing-document")).to_be_attached()
                check_metadata(mobile_page)
                assert mobile_page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                mobile_page.screenshot(path=str(output / "android-width.png"), full_page=True)
                results["android_viewport"] = "passed Chromium mobile emulation; physical install still required"
                mobile_page.goto(origin + "/login")
                mobile_page.locator("#email").fill(user["email"])
                mobile_page.locator("#password").fill("local-smoke-password-2026")
                mobile_page.get_by_role("button", name="Continue", exact=True).click()
                mobile_page.wait_for_load_state("load")
                mobile_page.locator("#show-tasks-view").click()
                mobile_page.locator('[data-task-view="completed"]').click()
                expect(mobile_page.locator(".task-title", has_text="PWA edited task")).to_be_visible()
                assert mobile_page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                mobile_page.screenshot(path=str(output / "android-tasks.png"))
                mobile_page.locator("#show-chat-view").click()
                expect(mobile_page.locator("#composer-input")).to_be_visible()
                results["mobile_authenticated_layout"] = "passed login, task/chat view switching, and no horizontal overflow"

                ios = browser.new_context(**playwright.devices["iPhone 13"])
                ios_page = ios.new_page()
                ios_page.on("pageerror", lambda error: errors.append(str(error)))
                ios_page.goto(origin + "/")
                expect(ios_page.locator("#dc-root")).to_be_attached()
                ios_button = ios_page.get_by_role("button", name="Add Ennoic to Home Screen", exact=True)
                expect(ios_button).to_be_visible()
                ios_button.click()
                expect(ios_page.locator("#ennoic-ios-install-help")).to_contain_text("In Safari, tap Share")
                expect(ios_page.locator("#ennoic-ios-install-help")).to_be_visible()
                check_metadata(ios_page)
                ios_page.locator("#ennoic-ios-install-help").scroll_into_view_if_needed()
                ios_page.screenshot(path=str(output / "ios-instructions.png"))
                results["ios_instructions"] = "passed iOS user-agent emulation; physical Safari A2HS still required"
                ios_page.emulate_media(media="screen")
                ios_page.add_init_script("Object.defineProperty(navigator, 'standalone', {value: true})")
                ios_page.reload()
                expect(ios_page.locator("html.ennoic-landing-document")).to_be_attached()
                expect(ios_page.locator("[data-pwa-install-slot]")).to_be_hidden()
                results["standalone_install_control_hidden"] = "passed simulated iOS standalone mode"
                results["page_errors"] = errors
                assert not errors, errors
                browser.close()
            (output / "results.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
            print(json.dumps(results, indent=2))
        finally:
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    main()
