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
from threading import Event, Thread
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import dotenv
from playwright.sync_api import sync_playwright, expect
from flask import Response, request
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


def navigation_timing(page):
    return page.evaluate("""() => {
      const navigation = performance.getEntriesByType('navigation')[0];
      const paint = performance.getEntriesByName('first-contentful-paint')[0];
      return {ttfb_ms: Math.round(navigation.responseStart - navigation.requestStart),
        dom_content_loaded_ms: Math.round(navigation.domContentLoadedEventEnd),
        first_contentful_paint_ms: paint ? Math.round(paint.startTime) : null};
    }""")


def watch_page(page, errors):
    page.on("pageerror", lambda error: errors.append(str(error)))
    def unexpected_dialog(dialog):
        errors.append("Unexpected native browser dialog: " + dialog.type)
        dialog.dismiss()
    page.on("dialog", unexpected_dialog)


def confirmation(page):
    modal = page.locator("#ennoic-confirmation[open]")
    expect(modal).to_be_visible()
    return modal


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
        deletion_user = account_store.create_user("delete-smoke@example.com", "local-smoke-password-2026")
        account_store.verify_email(deletion_user["id"])
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
        pricing_gate = Event()
        pricing_gate.set()

        @application.app.before_request
        def hold_pricing_for_feedback_check():
            if request.path == "/pricing":
                pricing_gate.wait(timeout=10)

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
                watch_page(page, errors)
                page.goto(origin + "/")
                expect(page.locator("html.ennoic-landing-document")).to_be_attached()
                expect(page.locator("#dc-root")).to_be_attached()
                check_metadata(page)
                assert page.evaluate("getComputedStyle(document.documentElement).overscrollBehaviorY") == "auto"
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
                page.get_by_role("button", name="Delete PWA delete test", exact=True).click()
                modal = confirmation(page)
                expect(modal.locator("[data-confirmation-cancel]")).to_be_focused()
                page.keyboard.press("Escape")
                expect(page.locator(".task-title", has_text="PWA delete test")).to_be_visible()
                expect(page.get_by_role("button", name="Delete PWA delete test", exact=True)).to_be_focused()
                page.get_by_role("button", name="Delete PWA delete test", exact=True).click()
                confirmation(page).locator("[data-confirmation-accept]").click()
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
                watch_page(page, errors)
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
                page.locator('#settings-sign-out-form button').click()
                modal = confirmation(page)
                expect(modal.locator("[data-confirmation-cancel]")).to_be_focused()
                page.keyboard.press("Tab")
                expect(modal.locator("[data-confirmation-accept]")).to_be_focused()
                page.keyboard.press("Tab")
                expect(modal.locator("[data-confirmation-cancel]")).to_be_focused()
                modal.locator("[data-confirmation-cancel]").click()
                assert page.evaluate("fetch('/api/auth/me').then(r => r.json()).then(j => j.authenticated)") is True
                page.locator('#settings-sign-out-form button').click()
                confirmation(page).locator("[data-confirmation-accept]").click()
                expect(page.locator("html.ennoic-landing-document")).to_be_attached()
                assert page.evaluate("fetch('/api/auth/me').then(r => r.json()).then(j => j.authenticated)") is False
                results["logout"] = "passed through existing settings form"
                page.close()

                mobile = browser.new_context(**playwright.devices["Pixel 7"])
                mobile_page = mobile.new_page()
                watch_page(mobile_page, errors)
                mobile_page.goto(origin + "/")
                expect(mobile_page.locator("html.ennoic-landing-document")).to_be_attached()
                check_metadata(mobile_page)
                assert mobile_page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                mobile_page.screenshot(path=str(output / "android-width.png"))
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
                mobile.close()

                # A predictable start URL is shared by Android TWA and installed
                # PWAs; no user-agent classification is involved in this flow.
                installed = browser.new_context(viewport={"width": 800, "height": 1280}, has_touch=True)
                app_page = installed.new_page()
                watch_page(app_page, errors)
                app_page.goto(origin + "/app")
                expect(app_page.locator("[data-app-welcome]")).to_be_visible()
                expect(app_page.get_by_text("Verify email", exact=True)).to_have_count(0)
                assert app_page.evaluate("getComputedStyle(document.documentElement).overscrollBehaviorY") == "auto"
                expect(app_page.get_by_role("link", name="Continue with Google")).to_be_visible()
                expect(app_page.locator("#password")).to_be_hidden()
                assert app_page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                check_metadata(app_page)
                app_page.screenshot(path=str(output / "installed-welcome-tablet.png"))
                # Browser marketing remains available in the same session.
                app_page.goto(origin + "/")
                expect(app_page.locator("#dc-root")).to_be_attached()
                app_page.goto(origin + "/app?form=register")
                expect(app_page.get_by_text("Verify email", exact=True)).to_have_count(0)
                expect(app_page.get_by_role("link", name="Sign in", exact=True)).to_be_visible()
                app_page.goto(origin + "/app/email-help")
                expect(app_page.locator('form[action="/auth/password-reset"]')).to_be_visible()
                app_page.get_by_role("link", name="Back to sign in").click()
                expect(app_page.get_by_role("button", name="Continue with email", exact=True)).to_be_visible()
                app_page.locator("#email").fill(user["email"])
                app_page.get_by_role("button", name="Continue with email", exact=True).click()
                expect(app_page.locator("#password")).to_be_visible()
                app_page.locator("#password").fill("local-smoke-password-2026")
                app_page.get_by_role("button", name="Continue", exact=True).click()
                expect(app_page).to_have_url(origin + "/app")
                expect(app_page.locator("#app")).to_be_visible()
                expect(app_page.locator(".history-item")).to_have_count(2)
                if app_page.locator("#app").get_attribute("data-sidebar") != "open":
                    toggle = app_page.locator("#mobile-menu-toggle")
                    (toggle if toggle.is_visible() else app_page.locator("#toggle-sidebar")).click()
                app_page.locator(".history-item").filter(has_text="Hello from the PWA test").click()
                expect(app_page.locator("#messages")).to_contain_text("Local AI transport test succeeded.")
                results["installed_email_auth"] = "passed welcome, email reveal, existing login, direct /app dashboard; browser / unchanged"
                results["local_dashboard_timing"] = navigation_timing(app_page)
                # Hold a real navigation request to observe immediate feedback
                # before the server responds, then let the existing link finish.
                pricing_gate.clear()
                # Use the existing same-origin anchor; visibility is irrelevant
                # to this event-level check (the viewport may hide its sidebar).
                feedback = app_page.evaluate("""() => {
                  const started = performance.now();
                  document.querySelector('a[href="/pricing"]').click();
                  const progress = document.getElementById('ennoic-navigation-progress');
                  return {visible: !!progress && !progress.hidden && progress.getBoundingClientRect().height === 3,
                    status: progress?.textContent, elapsed_ms: performance.now() - started};
                }""")
                assert feedback["visible"] and "Opening page" in feedback["status"], feedback
                pricing_gate.set()
                expect(app_page).to_have_url(origin + "/pricing")
                expect(app_page.locator("#ennoic-navigation-progress")).to_have_count(0)
                results["navigation_feedback"] = feedback

                pricing_sizes = [(320, 740), (390, 844), (800, 1280), (1280, 800), (1440, 1000)]
                results["pricing_viewports"] = []
                for width, height in pricing_sizes:
                    app_page.set_viewport_size({"width": width, "height": height})
                    app_page.goto(origin + "/pricing")
                    assert app_page.evaluate("document.documentElement.scrollWidth <= innerWidth"), (width, height)
                    button = app_page.locator("button, a[href]").filter(visible=True).first
                    assert button.evaluate("element => getComputedStyle(element).webkitTapHighlightColor") == "rgba(0, 0, 0, 0)"
                    button.focus()
                    # Keyboard focus style remains authored; touch suppression
                    # must not erase the focus-visible rule.
                    assert app_page.evaluate("[...document.styleSheets].filter(s => s.href?.includes('app-navigation.css')).some(s => [...s.cssRules].some(r => r.selectorText?.includes(':focus-visible')))")
                    app_page.screenshot(path=str(output / f"pricing-{width}.png"), full_page=True)
                    results["pricing_viewports"].append({"width": width, "height": height, "overflow": False})
                app_page.set_viewport_size({"width": 800, "height": 1280})
                app_page.goto(origin + "/app")
                app_page.close()
                app_page = installed.new_page()
                watch_page(app_page, errors)
                app_page.goto(origin + "/app")
                expect(app_page.locator("#app")).to_be_visible()
                expect(app_page.locator("#messages")).to_contain_text("Local AI transport test succeeded.")
                results["installed_reopen"] = "passed existing cookie/session and saved conversation"
                app_page.evaluate("navigator.serviceWorker.ready.then(() => true)")
                installed.set_offline(True)
                app_page.goto(origin + "/app")
                expect(app_page.get_by_role("heading", name="You're offline")).to_be_visible()
                installed.set_offline(False)
                expect(app_page.locator("#network-status")).to_contain_text("Connection restored")
                app_page.get_by_role("link", name="Try again").click()
                expect(app_page.locator("#app")).to_be_visible()
                results["installed_offline_recovery"] = "passed generic offline /app fallback and authenticated recovery"
                app_page.goto(origin + "/settings")
                app_page.locator('[data-section="account"]').click()
                app_page.locator('#settings-sign-out-form button').click()
                confirmation(app_page).locator("[data-confirmation-accept]").click()
                expect(app_page).to_have_url(origin + "/app")
                expect(app_page.locator("[data-app-welcome]")).to_be_visible()
                assert app_page.evaluate("fetch('/api/auth/me').then(r => r.json()).then(j => j.authenticated)") is False
                results["installed_logout"] = "passed return to welcome without an authenticated session"

                # Use the browser cookie jar for real authorization initiation,
                # then substitute the provider token/identity transport and
                # navigate the actual callback. Never open a Google login.
                oauth = {}

                class TokenResponse:
                    def raise_for_status(self):
                        pass

                    def json(self):
                        return {"id_token": "local-identity-token"}

                with patch.dict(os.environ, {"ENNOIC_GOOGLE_CLIENT_ID": "local-client", "ENNOIC_GOOGLE_CLIENT_SECRET": "local-secret", "ENNOIC_PUBLIC_ORIGIN": origin}), patch(
                    "app.requests.post", return_value=TokenResponse()
                ), patch("google.oauth2.id_token.verify_oauth2_token", side_effect=lambda *a, **k: {
                    "nonce": oauth["nonce"], "email": user["email"], "email_verified": True
                }):
                    expect(app_page.get_by_role("link", name="Continue with Google")).to_have_attribute("href", "/auth/google")
                    authorize = installed.request.get(origin + "/auth/google", max_redirects=0)
                    assert authorize.status == 302
                    params = parse_qs(urlparse(authorize.headers["location"]).query)
                    oauth.update({key: value[0] for key, value in params.items()})
                    app_page.goto(origin + "/auth/google/callback?state=" + oauth["state"] + "&code=mock-code")
                    expect(app_page.locator("#app")).to_be_visible()
                    expect(app_page).to_have_url(origin + "/app")
                assert oauth["redirect_uri"] == origin + "/auth/google/callback"
                assert oauth["code_challenge_method"] == "S256"
                results["installed_google_callback"] = "passed authorization with browser cookie jar, mocked token/identity callback, canonical URI, PKCE and /app return"

                # Exercise the remaining destructive UI using disposable local
                # data only. The real server retains CSRF/email/deletion policy.
                expect(app_page.locator(".history-item")).to_have_count(2)
                if app_page.locator("#app").get_attribute("data-sidebar") != "open":
                    app_page.locator("#mobile-menu-toggle").click()
                history = app_page.locator(".history-row").filter(has_text="Second conversation history test")
                history.locator(".conversation-menu-trigger").click()
                history.get_by_role("menuitem", name="Delete", exact=True).click()
                modal = confirmation(app_page)
                modal.locator("[data-confirmation-cancel]").click()
                expect(app_page.locator(".history-item")).to_have_count(2)
                history.locator(".conversation-menu-trigger").click()
                history.get_by_role("menuitem", name="Delete", exact=True).click()
                confirmation(app_page).locator("[data-confirmation-accept]").click()
                expect(app_page.locator(".history-item")).to_have_count(1)
                results["custom_confirmation_flows"] = "passed task/conversation deletion cancel+accept, sign-out Cancel/Tab/Escape/focus return+accept, no native dialogs"
                installed.close()

                deletion_context = browser.new_context(viewport={"width":390,"height":844}, has_touch=True)
                deletion_page = deletion_context.new_page()
                watch_page(deletion_page, errors)
                deletion_page.goto(origin + "/app")
                expect(deletion_page.get_by_role("button", name="Continue with email", exact=True)).to_be_visible()
                deletion_page.locator("#email").fill(deletion_user["email"])
                deletion_page.get_by_role("button", name="Continue with email", exact=True).click()
                deletion_page.locator("#password").fill("local-smoke-password-2026")
                deletion_page.get_by_role("button", name="Continue", exact=True).click()
                expect(deletion_page.locator("#app")).to_be_visible()
                deletion_page.goto(origin + "/settings")
                deletion_page.locator('[data-section="account"]').click()
                deletion_page.locator("#delete-account").click()
                modal = confirmation(deletion_page)
                expect(modal).to_contain_text("30 days")
                expect(modal.locator("[data-confirmation-cancel]")).to_be_focused()
                deletion_page.set_viewport_size({"width":390,"height":350})
                modal.locator("#ennoic-confirmation-input").focus()
                bounds = modal.bounding_box()
                actions = modal.locator(".ennoic-confirmation-actions").bounding_box()
                field = modal.locator("#ennoic-confirmation-input").bounding_box()
                assert bounds["y"] >= 0 and bounds["y"] + bounds["height"] <= 350, bounds
                assert actions["y"] + actions["height"] <= 350, actions
                assert field["y"] >= bounds["y"] and field["y"] + field["height"] <= actions["y"], field
                deletion_page.screenshot(path=str(output / "v021-account-modal-keyboard.png"))
                deletion_page.set_viewport_size({"width":390,"height":844})
                modal.locator("[data-confirmation-cancel]").click()
                assert deletion_page.evaluate("fetch('/api/auth/me').then(r => r.json()).then(j => j.authenticated)") is True
                deletion_page.locator("#delete-account").click()
                modal = confirmation(deletion_page)
                modal.locator("#ennoic-confirmation-input").fill("wrong@example.com")
                modal.locator("[data-confirmation-accept]").click()
                expect(deletion_page.locator("#account-feedback")).not_to_be_empty()
                assert deletion_page.evaluate("fetch('/api/auth/me').then(r => r.json()).then(j => j.authenticated)") is True
                deletion_page.locator("#delete-account").click()
                modal = confirmation(deletion_page)
                modal.locator("#ennoic-confirmation-input").fill(deletion_user["email"])
                modal.locator("[data-confirmation-accept]").click()
                expect(deletion_page).to_have_url(origin + "/login?deletion=scheduled")
                assert deletion_page.evaluate("fetch('/api/auth/me').then(r => r.json()).then(j => j.authenticated)") is False
                results["account_deletion_modal"] = "passed Cancel, server wrong-email rejection and existing scheduled deletion with exact-email confirmation; disposable local account only"
                deletion_context.close()

                ios = browser.new_context(**playwright.devices["iPhone 13"])
                ios_page = ios.new_page()
                watch_page(ios_page, errors)
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
                expect(ios_page).to_have_url(origin + "/app")
                expect(ios_page.locator("[data-app-welcome]")).to_be_visible()
                expect(ios_page.locator("[data-pwa-install-slot]")).to_have_count(0)
                assert ios_page.evaluate("getComputedStyle(document.documentElement).overscrollBehaviorY") == "contain"
                results["standalone_legacy_launch"] = "passed simulated iOS standalone / to /app upgrade; no install prompt"
                results["installed_scroll_policy"] = "passed standalone vertical overscroll containment; no touch-event interception"
                results["page_errors"] = errors
                assert not errors, errors
                browser.close()
            (output / "results.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
            print(json.dumps(results, indent=2))
        finally:
            pricing_gate.set()
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    main()
