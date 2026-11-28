"""Isolated real-Chromium Back/CloseWatcher checks with a modeled native port.

No .env, production accounts, Google requests, emails, payments, or AI transport.
Escape is a trusted Chromium close request; Android's actual activity exit and
toast are tested separately by the native wrapper tests and on a physical device.
Run from repository root: python scripts/android_back_browser_smoke.py
"""

import json
import logging
import os
from pathlib import Path
import sys
import tempfile
from threading import Thread
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import dotenv
from playwright.sync_api import sync_playwright, expect
from werkzeug.serving import make_server, WSGIRequestHandler


# QA instrumentation captures the real bridge listener before application code
# loads. Browser messages remain unmodified: only this harness invokes that
# captured listener with the native source=null shape (unforgeable by web JS).
NATIVE_PORT_MODEL = r"""(() => {
  window.__closeWatcherLog = [];
  if (window.CloseWatcher) {
    const RealCloseWatcher = window.CloseWatcher;
    let watcherId = 0;
    window.CloseWatcher = class extends RealCloseWatcher {
      constructor(...args) {
        super(...args);
        this.qaId = ++watcherId;
        window.__closeWatcherLog.push({event:'create', id:this.qaId, at:performance.now()});
        this.addEventListener('close', () => window.__closeWatcherLog.push({event:'close', id:this.qaId, at:performance.now()}));
      }
      destroy() {
        window.__closeWatcherLog.push({event:'destroy', id:this.qaId, at:performance.now()});
        return super.destroy();
      }
    };
  }
  const original = window.addEventListener;
  const receivers = [];
  window.addEventListener = function(type, listener, options) {
    if (type === 'message' && listener.name === 'receive') receivers.push(listener);
    return original.call(this, type, listener, options);
  };
  window.__nativeBack = {
    log: [], deadline: 0, respond: true, closed: false,
    attach() {
      if (receivers.length !== 1) throw Error('Expected exactly one native receive handler: ' + receivers.length);
      this.deadline = 0;
      this.closed = false;
      const model = this;
      const port = {
        onmessage: null, onmessageerror: null, start() {},
        close() { model.closed = true; },
        postMessage(raw) {
          const request = JSON.parse(raw);
          const now = performance.now();
          let state;
          if (request.action === 'reset') { model.deadline = 0; state = 'reset'; }
          else if (request.action === 'exit') { model.deadline = 0; state = 'exiting'; }
          else if (request.action === 'root') {
            if (model.deadline && now <= model.deadline) { model.deadline = 0; state = 'exiting'; }
            else { model.deadline = now + 2000; state = 'armed'; }
          } else throw Error('Unexpected native request action');
          model.log.push({action: request.action, requestId: request.requestId, state, at: now});
          if (model.respond) setTimeout(() => port.onmessage?.({data: JSON.stringify({
            type: 'ennoic.native.back', version: 1, nonce: request.nonce,
            requestId: request.requestId, state
          })}), 0);
        }
      };
      // Real Chrome first transfers the port in an empty window message, then
      // delivers the native hello on that port. Keep this distinct from ACKs.
      receivers[0]({isTrusted: true, source: null,
        origin: 'android-app://ennoic.onrender.com', ports: [port], data: ''});
      setTimeout(() => port.onmessage?.({data: JSON.stringify({type: 'ennoic.native',
        version: 1, capability: 'back-v1', nonce: '0'.repeat(32)})}), 0);
    }
  };
})();"""


class QuietHandler(WSGIRequestHandler):
    def log(self, *args, **kwargs):
        pass


def watch(page, errors):
    page.on("pageerror", lambda error: errors.append(str(error)))
    def unexpected_dialog(dialog):
        errors.append("Unexpected browser dialog: " + dialog.type)
        dialog.dismiss()
    page.on("dialog", unexpected_dialog)


def attach(page):
    page.wait_for_load_state("load")
    page.evaluate("window.__nativeBack.attach()")
    page.wait_for_timeout(60)
    assert page.evaluate("window.__nativeBack.log.at(-1).state") == "reset"


def root_actions(page):
    return page.evaluate("window.__nativeBack.log.filter(item => item.action === 'root').map(item => item.state)")


def history(page):
    return page.evaluate("window.navigation.entries().map(entry => new URL(entry.url).pathname)")


def press_back(page):
    page.keyboard.press("Escape")
    page.wait_for_timeout(80)


def login(page, origin, email):
    page.goto(origin + "/app")
    page.locator("#email").fill(email)
    page.get_by_role("button", name="Continue with email", exact=True).click()
    page.locator("#password").fill("local-back-password-2026")
    page.get_by_role("button", name="Continue", exact=True).click()
    expect(page.locator("#app")).to_be_visible()
    expect(page).to_have_url(origin + "/app")
    page.wait_for_load_state("load")


def main():
    dotenv.load_dotenv = lambda *args, **kwargs: False
    for key in list(os.environ):
        if key.startswith(("ENNOIC_", "GOOGLE_", "GROQ_", "PADDLE_", "RAZORPAY_", "SMTP_", "RESEND_")) or key in {"DATABASE_URL", "DISCORD_WEBHOOK_URL"}:
            os.environ.pop(key, None)
    os.environ["ENNOIC_SECRET_KEY"] = "isolated-back-smoke-secret-over-32-characters"
    with tempfile.TemporaryDirectory(prefix="ennoic-back-smoke-") as directory:
        os.environ["ENNOIC_TASK_DB"] = str(Path(directory) / "test.sqlite3")
        import app as application
        import account_store
        application.app.config.update(TESTING=True, WTF_CSRF_ENABLED=True, RATELIMIT_ENABLED=False)
        application.limiter.enabled = False
        logging.getLogger("werkzeug").setLevel(logging.ERROR)
        account_store.initialize()
        user = account_store.create_user("back-smoke@example.com", "local-back-password-2026")
        account_store.verify_email(user["id"])
        application.process_message_with_metadata = lambda *a, **k: {"reply": "Local Back test reply."}
        application.requests.sessions.Session.request = lambda *a, **k: (_ for _ in ()).throw(AssertionError("External server request blocked"))
        server = make_server("127.0.0.1", 0, application.app, threaded=True, request_handler=QuietHandler)
        Thread(target=server.serve_forever, daemon=True).start()
        origin = f"http://127.0.0.1:{server.server_port}"
        output = ROOT / "instance/pwa-qa"
        output.mkdir(parents=True, exist_ok=True)
        results = {"method": "Trusted Edge Escape/CloseWatcher; modeled native ACK/clock/exit; no physical Android exit claim"}
        errors = []
        try:
            with sync_playwright() as playwright:
                edge = Path("C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe")
                options = {"executable_path": str(edge)} if edge.exists() else {}
                browser = playwright.chromium.launch(**options)
                context = browser.new_context(viewport={"width": 800, "height": 1280}, has_touch=True)
                context.route("**/*", lambda route: route.continue_() if route.request.url.startswith(origin + "/") else route.abort())
                context.add_init_script(NATIVE_PORT_MODEL)
                page = context.new_page()
                watch(page, errors)
                login(page, origin, user["email"])
                assert page.evaluate("typeof CloseWatcher") == "function"
                assert page.evaluate("typeof window.EnnoicAndroidBack") == "object", {
                    "errors": errors, "url": page.url,
                    "scripts": page.locator("script[src]").evaluate_all("nodes => nodes.map(node=>node.src)"),
                }
                attach(page)
                initial_history = history(page)

                # Root first press, elapsed expiry, rearm, then second press.
                press_back(page)
                assert root_actions(page) == ["armed"], root_actions(page)
                expect(page).to_have_url(origin + "/app")
                page.wait_for_timeout(2100)
                press_back(page)
                assert root_actions(page) == ["armed", "armed"], root_actions(page)
                press_back(page)
                assert root_actions(page) == ["armed", "armed", "exiting"], root_actions(page)
                assert history(page) == initial_history
                results["root_exit_interval"] = "first arms, timeout resets, first rearms, second exits; no synthetic history entries"
                results["email_auth_boundary"] = "real existing email form/session completes at /app; root Back requests native exit without traversing completed authentication history"

                attach(page)
                count = len(root_actions(page))
                page.locator("#mobile-menu-toggle").click()
                expect(page.locator("#app")).to_have_attribute("data-sidebar", "open")
                press_back(page)
                expect(page.locator("#app")).to_have_attribute("data-sidebar", "closed")
                assert len(root_actions(page)) == count
                page.locator("#show-tasks-view").click()
                expect(page.locator("#app")).to_have_attribute("data-mobile-view", "tasks")
                press_back(page)
                expect(page.locator("#app")).to_have_attribute("data-mobile-view", "chat")
                assert len(root_actions(page)) == count

                page.locator("#composer-input").fill("Local navigation history")
                page.locator("#send").click()
                expect(page.locator("#messages")).to_contain_text("Local Back test reply.")
                page.locator("#mobile-menu-toggle").click()
                page.locator(".conversation-menu-trigger").first.click()
                expect(page.locator(".conversation-menu:not([hidden])")).to_be_visible()
                press_back(page)
                expect(page.locator(".conversation-menu:not([hidden])")).to_have_count(0)
                expect(page.locator("#app")).to_have_attribute("data-sidebar", "open")
                assert len(root_actions(page)) == count
                press_back(page)
                expect(page.locator("#app")).to_have_attribute("data-sidebar", "closed")
                assert len(root_actions(page)) == count
                results["panels_one_level"] = "menu, then sidebar; Tasks returns to Chat; no exit arm during panel dismissal"

                page.locator("#show-tasks-view").click()
                page.locator("#create-task").click()
                expect(page.locator("#task-editor[open]")).to_be_visible()
                page.evaluate("window.__taskDialogClosed = false; document.getElementById('task-editor').addEventListener('close', () => {window.__taskDialogClosed = true}, {once:true})")
                press_back(page)
                expect(page.locator("#task-editor[open]")).to_have_count(0)
                expect(page.locator("#app")).to_have_attribute("data-mobile-view", "tasks")
                assert len(root_actions(page)) == count
                page.evaluate("""async () => {
                  const limit = performance.now() + 2000;
                  while (!window.__taskDialogClosed && performance.now() < limit) await new Promise(resolve => setTimeout(resolve, 20));
                  if (!window.__taskDialogClosed) throw Error('Native task dialog close event missing');
                }""")
                results["task_dialog_watcher_trace"] = page.evaluate("window.__closeWatcherLog")
                press_back(page)
                try:
                    expect(page.locator("#app")).to_have_attribute("data-mobile-view", "chat")
                except AssertionError:
                    print(json.dumps({"watchers":page.evaluate("window.__closeWatcherLog"), "native":page.evaluate("window.__nativeBack.log"), "errors":errors}, indent=2), flush=True)
                    raise

                # Exercise the real shared confirmation, no destructive action.
                page.evaluate("void window.EnnoicConfirmation.confirm({title:'Back smoke', message:'Cancel safely', confirmLabel:'Confirm', cancelLabel:'Cancel'}).then(value => {window.__confirmResult=value})")
                expect(page.locator("#ennoic-confirmation[open]")).to_be_visible()
                press_back(page)
                expect(page.locator("#ennoic-confirmation[open]")).to_have_count(0)
                assert page.evaluate("window.__confirmResult") is False
                assert len(root_actions(page)) == count
                results["dialogs"] = "native task dialog and shared confirmation each dismiss alone without navigation or exit arm"

                page.locator("#mobile-menu-toggle").click()
                page.locator("#mobile-open-settings").click()
                expect(page).to_have_url(origin + "/settings")
                attach(page)
                page.locator('[data-section="profile"]').click()
                press_back(page)
                expect(page.locator('[data-section="general"]')).to_have_attribute("aria-current", "true")
                expect(page).to_have_url(origin + "/settings")
                press_back(page)
                expect(page).to_have_url(origin + "/app")
                expect(page.locator("#app")).to_be_visible()
                results["settings_back"] = "Profile returns to General; second Back returns to existing safe dashboard entry"

                # Completed auth/checkout are not safe predecessors. Their real
                # history entries remain intact; the bridge replaces only the
                # current non-root location when no safe predecessor exists.
                page.goto(origin + "/checkout/success")
                page.goto(origin + "/settings")
                attach(page)
                press_back(page)
                expect(page).to_have_url(origin + "/app")
                results["checkout_boundary"] = "Back from settings with checkout predecessor replaces /app instead of revisiting checkout"

                # A stale native channel must fail open after a bounded timeout.
                attach(page)
                page.evaluate("window.__nativeBack.respond = false")
                press_back(page)
                press_back(page)
                page.wait_for_timeout(850)
                assert page.evaluate("window.__nativeBack.closed") is True
                closed_count = len(root_actions(page))
                press_back(page)
                assert len(root_actions(page)) == closed_count
                results["stale_bridge"] = "unacknowledged bridge disconnects within 750ms despite repeated Back; no further close interception"

                page.goto(origin + "/settings")
                attach(page)
                page.locator('[data-section="account"]').click()
                page.locator("#settings-sign-out-form button").click()
                expect(page.locator("#ennoic-confirmation[open]")).to_be_visible()
                press_back(page)
                assert page.evaluate("fetch('/api/auth/me').then(r=>r.json()).then(j=>j.authenticated)") is True
                page.locator("#settings-sign-out-form button").click()
                page.locator("[data-confirmation-accept]").click()
                expect(page.locator("[data-app-welcome]")).to_be_visible()
                assert page.evaluate("fetch('/api/auth/me').then(r=>r.json()).then(j=>j.authenticated)") is False
                attach(page)
                press_back(page)
                assert page.evaluate("window.__nativeBack.log.at(-1).action") == "exit"
                results["signout"] = "Cancel preserves auth; explicit CSRF-protected signout returns welcome; welcome Back requests immediate native exit"

                oauth = {}
                class TokenResponse:
                    def raise_for_status(self):
                        pass
                    def json(self):
                        return {"id_token": "local-identity-token"}
                # Browser interception provides a real cross-origin history
                # entry without contacting Google or controlling their chooser.
                context.route("https://accounts.google.test/**", lambda route: route.fulfill(
                    status=200, content_type="text/html", body="<title>Modeled provider chooser</title><p>Isolated provider history</p>"))
                with patch.dict(os.environ, {"ENNOIC_GOOGLE_CLIENT_ID": "local-client", "ENNOIC_GOOGLE_CLIENT_SECRET": "local-secret", "ENNOIC_PUBLIC_ORIGIN": origin}), patch(
                    "app.requests.post", return_value=TokenResponse()
                ), patch("google.oauth2.id_token.verify_oauth2_token", side_effect=lambda *a, **k: {
                    "nonce": oauth["nonce"], "email": user["email"], "email_verified": True
                }):
                    authorize = context.request.get(origin + "/auth/google", max_redirects=0)
                    assert authorize.status == 302
                    params = parse_qs(urlparse(authorize.headers["location"]).query)
                    oauth.update({key: value[0] for key, value in params.items()})
                    page.goto("https://accounts.google.test/chooser")
                    page.goto(origin + "/auth/google/callback?state=" + oauth["state"] + "&code=mock-code")
                    expect(page.locator("#app")).to_be_visible()
                    expect(page).to_have_url(origin + "/app")
                assert oauth["code_challenge_method"] == "S256"
                attach(page)
                before_google_back = history(page)
                press_back(page)
                assert root_actions(page) == ["armed"]
                expect(page).to_have_url(origin + "/app")
                assert history(page) == before_google_back
                press_back(page)
                assert root_actions(page) == ["armed", "exiting"]
                expect(page).to_have_url(origin + "/app")
                results["google_auth_boundary"] = "real local callback + mocked nonce/token transport and cross-origin provider history; root Back arms/exits without provider traversal"

                # Ordinary browser receives no trusted native port. Native-only
                # handling never starts merely because /app was visited.
                web = browser.new_context(viewport={"width": 1440, "height": 1000})
                web.route("**/*", lambda route: route.continue_() if route.request.url.startswith(origin + "/") else route.abort())
                web_page = web.new_page()
                watch(web_page, errors)
                login(web_page, origin, user["email"])
                web_page.goto(origin + "/settings")
                web_page.go_back()
                expect(web_page).to_have_url(origin + "/app")
                web_page.goto(origin + "/pricing")
                web_page.go_back()
                expect(web_page).to_have_url(origin + "/app")
                results["ordinary_browser"] = "normal history Back from settings/pricing preserved; native listener inactive without trusted channel"
                page.screenshot(path=str(output / "android-back-root-v022.png"))
                results["page_errors"] = errors
                assert not errors, errors
                browser.close()
            (output / "android-back-browser-v022.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
            print(json.dumps(results, indent=2))
        finally:
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    main()
