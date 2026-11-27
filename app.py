import base64
import hashlib
import os
import secrets
import smtplib
import ssl
import time
import traceback
from threading import Event, Lock, Thread
from email.message import EmailMessage
from pathlib import Path
from urllib.parse import urljoin

import requests

from flask import Flask, abort, g, jsonify, redirect, render_template, request, session, url_for
from flask_login import LoginManager, UserMixin, current_user, login_required, login_user, logout_user
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_wtf.csrf import CSRFError, CSRFProtect, generate_csrf
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.middleware.proxy_fix import ProxyFix
from dotenv import load_dotenv

import account_store
import billing
import database as database_layer
import entitlements
import paddle_billing
import task_store
from discord_notifications import notify_discord
from main import process_message_with_metadata
from task_management import message_requires_ai, resolve_task_confirmation
from latency_trace import begin_trace, end_trace, mark_trace
from pwa import pwa


app = Flask(__name__, instance_relative_config=True)
app.register_blueprint(pwa)
load_dotenv(Path(__file__).resolve().with_name(".env"), override=False)


def _chat_error_location(error):
    """Return the deepest in-repository frame without exposing exception data."""
    repository_root = Path(__file__).resolve().parent
    location = None
    for frame in traceback.extract_tb(error.__traceback__):
        try:
            frame_path = Path(frame.filename).resolve()
        except (OSError, RuntimeError):
            continue
        if frame_path.parent == repository_root:
            location = f"{frame_path.name}:{frame.name}:{frame.lineno}"
    return location or "unknown"

if os.environ.get("ENNOIC_TRUST_PROXY", "").casefold() in {"1", "true", "yes"}:
    # Render terminates TLS and forwards one trusted proxy hop to the app.
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)


def _environment_value(*names, default=None):
    for name in names:
        value = os.environ.get(name)
        if value:
            return value
    return default


def _trusted_public_origin():
    """Return a configured canonical origin, never one derived from request headers."""
    configured = _environment_value(
        "ENNOIC_PUBLIC_ORIGIN", "ENNOIC_PUBLIC_URL", "PUBLIC_URL",
        default="https://ennoic.onrender.com",
    ).strip()
    from urllib.parse import urlsplit

    parsed = urlsplit(configured)
    local_hosts = {"localhost", "127.0.0.1", "::1"}
    if (parsed.scheme not in {"https", "http"} or not parsed.hostname or parsed.username
            or parsed.password or parsed.query or parsed.fragment or parsed.path not in {"", "/"}
            or (parsed.scheme != "https" and parsed.hostname not in local_hosts)):
        raise RuntimeError("ENNOIC_PUBLIC_ORIGIN must be a trusted HTTPS origin.")
    return f"{parsed.scheme}://{parsed.netloc}"


def _trusted_external_url(endpoint, **values):
    return _trusted_public_origin() + url_for(endpoint, **values)


def _secret_key():
    configured = _environment_value("ENNOIC_SECRET_KEY")
    if configured:
        if len(configured) < 32:
            raise RuntimeError("ENNOIC_SECRET_KEY must contain at least 32 characters.")
        return configured
    Path(app.instance_path).mkdir(parents=True, exist_ok=True)
    key_path = Path(app.instance_path) / "secret-key"
    try:
        return key_path.read_text(encoding="ascii").strip()
    except FileNotFoundError:
        value = secrets.token_hex(32)
        try:
            with key_path.open("x", encoding="ascii") as key_file:
                key_file.write(value)
        except FileExistsError:
            value = key_path.read_text(encoding="ascii").strip()
        return value


app.config.update(
    SECRET_KEY=_secret_key(),
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.environ.get("ENNOIC_COOKIE_SECURE", "").casefold() in {"1", "true", "yes"},
    PERMANENT_SESSION_LIFETIME=60 * 60 * 12,
    WTF_CSRF_TIME_LIMIT=60 * 60 * 2,
    RATELIMIT_STORAGE_URI=os.environ.get("ENNOIC_RATE_LIMIT_STORAGE", "memory://"),
    RATELIMIT_HEADERS_ENABLED=True,
    MAX_CONTENT_LENGTH=2 * 1024 * 1024,
)


@app.before_request
def start_chat_latency_trace():
    if request.path == "/api/chat":
        begin_trace()


csrf = CSRFProtect(app)
login_manager = LoginManager(app)
login_manager.session_protection = "strong"
limiter = Limiter(get_remote_address, app=app, default_limits=[])
_initialized_databases = set()
_database_initialization_lock = Lock()
_account_deletion_worker_lock = Lock()
_account_deletion_worker_started = False
_account_deletion_worker_stop = Event()
_dummy_password_hash = generate_password_hash(
    secrets.token_urlsafe(32),
    method="pbkdf2:sha256:600000",
)
_AUTH_CONTINUE_NOTICE = (
    "If these details need an email step, check your inbox and spam folder. "
    "If you already have an account, you can also reset your password."
)


class Account(UserMixin):
    def __init__(self, record):
        self.id = record["id"]
        self.email = record["email"]
        self.email_verified = bool(record["email_verified"])
        self.auth_version = record["auth_version"]


@login_manager.user_loader
def load_user(user_id):
    record = account_store.get_user(user_id)
    if record is None or not record["email_verified"] or record["deleted_at"] is not None:
        return None
    if session.get("account_auth_version") != record["auth_version"]:
        return None
    return Account(record)


@login_manager.unauthorized_handler
def unauthorized():
    if request.path.startswith("/api/"):
        return jsonify({"error": "Sign in to continue."}), 401
    if request.path == "/checkout":
        session["login_next"] = url_for("checkout_page")
    return redirect(url_for("login_page"))


@app.before_request
def initialize_database():
    if (request.path in {"/healthz", "/readyz"}
            or request.blueprint == "pwa" or request.endpoint == "static"):
        return
    database_started_at = time.perf_counter()
    path = account_store.database_key()
    with _database_initialization_lock:
        if path not in _initialized_databases:
            account_store.initialize()
            _initialized_databases.add(path)
    mark_trace(
        "database.initialization",
        duration_ms=round((time.perf_counter() - database_started_at) * 1000),
    )
    if not app.testing:
        _start_account_deletion_worker()


@app.route("/healthz")
def health_check():
    return jsonify({"status": "ok"})


@app.route("/readyz")
def readiness_check():
    try:
        database_layer.check_readiness(timeout_seconds=2)
    except Exception as error:
        app.logger.warning("database_readiness_failed error_type=%s", type(error).__name__)
        return jsonify({"status": "not_ready"}), 503
    return jsonify({"status": "ready"})


def _run_account_deletion_worker():
    while not _account_deletion_worker_stop.is_set():
        try:
            _purge_due_account_deletions()
        except Exception as error:
            app.logger.error(
                "account_deletion_purge_failed error_type=%s", type(error).__name__
            )
        _account_deletion_worker_stop.wait(60)


def _start_account_deletion_worker():
    global _account_deletion_worker_started
    with _account_deletion_worker_lock:
        if _account_deletion_worker_started:
            return
        worker = Thread(target=_run_account_deletion_worker, name="ennoic-account-deletion", daemon=True)
        worker.start()
        _account_deletion_worker_started = True


@app.after_request
def add_security_headers(response):
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    # Flask-WTF checks the same-origin Referer on HTTPS state-changing requests.
    # Keep it within this origin only; never send it to external sites.
    response.headers.setdefault("Referrer-Policy", "same-origin")
    bundle_nonce = (
        getattr(g, "landing_csp_nonce", None)
        or getattr(g, "auth_csp_nonce", None)
        or getattr(g, "pricing_csp_nonce", None)
        or getattr(g, "checkout_csp_nonce", None)
    )
    if bundle_nonce:
        # Paddle.js injects its overlay stylesheet into the pricing document.
        # Keep that permission scoped to payment pages and the exact CDN origin.
        payment_styles = " https://cdn.paddle.com" if (
            getattr(g, "pricing_csp_nonce", None) or getattr(g, "checkout_csp_nonce", None)
        ) else ""
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            f"script-src 'self' 'nonce-{bundle_nonce}' 'strict-dynamic' blob: https://cdn.jsdelivr.net https://checkout.razorpay.com https://cdn.paddle.com; "
            f"style-src 'self' 'unsafe-inline' https://fonts.googleapis.com{payment_styles}; "
            "font-src 'self' data: https://fonts.gstatic.com; img-src 'self' data: blob:; "
            "connect-src 'self' https://fonts.googleapis.com https://fonts.gstatic.com https://api.razorpay.com https://checkout.razorpay.com https://*.paddle.com https://*.paddle.net; "
            "frame-src https://api.razorpay.com https://checkout.razorpay.com https://*.paddle.com https://*.paddle.net; "
            "worker-src 'self'; manifest-src 'self'; "
            "object-src 'none'; base-uri 'self'; frame-ancestors 'none'"
        )
    else:
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self' https://cdn.jsdelivr.net; "
            "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
            "font-src 'self' https://fonts.gstatic.com; img-src 'self' data:; "
            "connect-src 'self'; worker-src 'self'; manifest-src 'self'; "
            "object-src 'none'; base-uri 'self'; frame-ancestors 'none'",
        )
    if request.is_secure:
        response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
    if response.mimetype == "text/html":
        response.headers.setdefault("Cache-Control", "no-store")
    if response.status_code >= 500 and not getattr(g, "discord_error_notified", False):
        notify_discord(
            "server_error",
            endpoint=request.endpoint or "unknown",
            error_type=f"HTTP{response.status_code}",
        )
    end_trace(response.status_code)
    return response


@app.errorhandler(CSRFError)
def handle_csrf_error(error):
    if request.path.startswith("/api/"):
        app.logger.warning(
            "csrf_validation_failed path=%s reason=%s",
            request.path,
            error.description,
        )
        return jsonify({
            "error": "Your session security token expired. Reload and try again.",
            "code": "csrf_expired",
        }), 400
    return _auth_template(auth_error="Your session expired. Please try again."), 400


@app.errorhandler(429)
def handle_rate_limit(error):
    if request.path.startswith("/api/"):
        return jsonify({"error": "Too many attempts. Please wait and try again."}), 429
    return _auth_template(auth_error="Too many attempts. Please wait and try again."), 429


@app.errorhandler(413)
def handle_request_too_large(error):
    if request.path.startswith("/api/"):
        return jsonify({"error": "The request is too large."}), 413
    return "The request is too large.", 413


@app.errorhandler(500)
def handle_internal_server_error(error):
    original = getattr(error, "original_exception", None)
    g.discord_error_notified = True
    notify_discord(
        "server_error",
        endpoint=request.endpoint or "unknown",
        error_type=type(original or error).__name__,
    )
    return error


def _request_data():
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else None


def _token_serializer(purpose):
    return URLSafeTimedSerializer(app.config["SECRET_KEY"], salt=f"ennoic-account-{purpose}")


def _create_email_token(user, purpose):
    nonce = secrets.token_urlsafe(32)
    account_store.create_account_token(
        user["id"],
        hashlib.sha256(nonce.encode("ascii")).hexdigest(),
        purpose,
    )
    token = _token_serializer(purpose).dumps({"user_id": user["id"], "nonce": nonce})
    return token


def _consume_email_token(token, purpose, *, max_age):
    try:
        payload = _token_serializer(purpose).loads(token, max_age=max_age)
    except (BadSignature, SignatureExpired):
        return None
    if not isinstance(payload, dict):
        return None
    user_id, nonce = payload.get("user_id"), payload.get("nonce")
    if not isinstance(user_id, str) or not isinstance(nonce, str):
        return None
    token_hash = hashlib.sha256(nonce.encode("ascii")).hexdigest()
    if not account_store.consume_account_token(user_id, token_hash, purpose):
        return None
    user = account_store.get_user(user_id)
    return user if user and user["deleted_at"] is None else None


def _send_account_email(recipient, subject, body, html_body=None, inline_images=()):
    resend_api_key = _environment_value("RESEND_API_KEY")
    resend_sender = _environment_value("RESEND_FROM_EMAIL", "ENNOIC_EMAIL_FROM")
    if resend_api_key and resend_sender:
        payload = {
            "from": resend_sender,
            "to": [recipient],
            "subject": subject,
            "text": body,
        }
        if html_body:
            payload["html"] = html_body
        if inline_images:
            payload["attachments"] = [
                {
                    "filename": Path(image_path).name,
                    "content": base64.b64encode(Path(image_path).read_bytes()).decode("ascii"),
                    "content_type": f"image/{subtype}",
                    "content_id": content_id,
                }
                for content_id, image_path, subtype in inline_images
            ]
        try:
            response = requests.post(
                "https://api.resend.com/emails",
                headers={"Authorization": f"Bearer {resend_api_key}"},
                json=payload,
                timeout=15,
            )
        except requests.RequestException as error:
            app.logger.error(
                "account_email_delivery_failed provider=resend stage=request error_type=%s",
                type(error).__name__,
            )
            raise OSError("Email API request failed.") from error
        if not response.ok:
            app.logger.error(
                "account_email_delivery_failed provider=resend stage=send status_code=%s",
                response.status_code,
            )
            raise OSError("Email API rejected the message.")
        return True

    host = _environment_value("ENNOIC_SMTP_HOST", "SMTP_HOST")
    sender = _environment_value("ENNOIC_SMTP_FROM", "SMTP_FROM_EMAIL", "SMTP_FROM")
    if not host or not sender:
        app.logger.warning("Account email is not configured; set Resend or SMTP credentials.")
        return False

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = sender
    message["To"] = recipient
    message.set_content(body)
    if html_body:
        message.add_alternative(html_body, subtype="html")
        html_part = message.get_payload()[-1]
        for content_id, image_path, subtype in inline_images:
            html_part.add_related(
                Path(image_path).read_bytes(),
                maintype="image",
                subtype=subtype,
                cid=f"<{content_id}>",
            )
    port = int(_environment_value("ENNOIC_SMTP_PORT", "SMTP_PORT", default="587"))
    context = ssl.create_default_context()
    use_ssl = _environment_value(
        "ENNOIC_SMTP_SSL",
        "SMTP_SSL",
        "SMTP_USE_SSL",
        default="",
    ).casefold() in {"1", "true", "yes"}
    smtp_class = smtplib.SMTP_SSL if use_ssl else smtplib.SMTP
    smtp_options = {"timeout": 15}
    if use_ssl:
        smtp_options["context"] = context
    stage = "connect"
    try:
        with smtp_class(host, port, **smtp_options) as smtp:
            stage = "initial_handshake"
            smtp.ehlo()
            if not use_ssl:
                stage = "starttls"
                smtp.starttls(context=context)
                stage = "post_tls_handshake"
                smtp.ehlo()
            username = _environment_value("ENNOIC_SMTP_USERNAME", "SMTP_USERNAME")
            if username:
                stage = "authentication"
                smtp.login(username, _environment_value("ENNOIC_SMTP_PASSWORD", "SMTP_PASSWORD", default=""))
            stage = "send_message"
            smtp.send_message(message)
    except smtplib.SMTPAuthenticationError as error:
        app.logger.error(
            "account_email_delivery_failed stage=%s error_type=%s smtp_code=%s",
            stage,
            type(error).__name__,
            error.smtp_code,
        )
        raise
    except smtplib.SMTPException as error:
        app.logger.error(
            "account_email_delivery_failed stage=%s error_type=%s smtp_code=%s",
            stage,
            type(error).__name__,
            getattr(error, "smtp_code", "unavailable"),
        )
        raise
    except (OSError, ValueError) as error:
        app.logger.error(
            "account_email_delivery_failed stage=%s error_type=%s",
            stage,
            type(error).__name__,
        )
        raise
    return True


def _email_delivery_configured():
    resend_configured = bool(
        _environment_value("RESEND_API_KEY")
        and _environment_value("RESEND_FROM_EMAIL", "ENNOIC_EMAIL_FROM")
    )
    smtp_configured = bool(
        _environment_value("ENNOIC_SMTP_HOST", "SMTP_HOST")
        and _environment_value("ENNOIC_SMTP_FROM", "SMTP_FROM_EMAIL", "SMTP_FROM")
    )
    return resend_configured or smtp_configured


def _google_oauth_value(name):
    return _environment_value(f"ENNOIC_GOOGLE_{name}", f"GOOGLE_{name}")


def _google_oauth_configured():
    return bool(_google_oauth_value("CLIENT_ID") and _google_oauth_value("CLIENT_SECRET"))


def _google_login_error(message, status=400):
    return _auth_template(auth_error=message), status


def _send_token_email(user, purpose):
    if not _email_delivery_configured():
        app.logger.error(
            "account_email_delivery_failed purpose=%s stage=configuration error_type=MissingConfiguration",
            purpose,
        )
        return False
    token = _create_email_token(user, purpose)
    configured_url = _trusted_public_origin()
    if purpose == "verify":
        relative_url = url_for("verify_email", token=token)
        subject = "Verify your Ennoic account"
        body = "Verify your Ennoic account by opening this link within 1 hour:\n\n{link}\n"
        html_body = None
        inline_images = ()
    else:
        relative_url = url_for("reset_password", token=token)
        subject = "Reset your Ennoic password"
        body = (
            "Reset your Ennoic password within 30 minutes by opening this link:\n\n"
            "{link}\n\nIf you did not request this, you can ignore this email."
        )
        html_body = None
        inline_images = ((
            "ennoic-logo",
            Path(app.static_folder) / "email" / "ennoic-logo.png",
            "png",
        ),)
    link = urljoin(configured_url.rstrip("/") + "/", relative_url.lstrip("/"))
    body = body.format(link=link)
    if purpose != "verify":
        html_body = render_template("password_reset_email.html", reset_url=link)
    app.logger.info("account_email_delivery_started purpose=%s", purpose)
    try:
        sent = _send_account_email(
            user["email"], subject, body, html_body=html_body, inline_images=inline_images
        )
        if sent:
            app.logger.info("account_email_delivery_succeeded purpose=%s", purpose)
        return sent
    except (OSError, smtplib.SMTPException, ValueError):
        app.logger.error(
            "account_email_delivery_failed purpose=%s stage=transport error_type=DeliveryError",
            purpose,
        )
        return False


def _auth_template(**values):
    # This flag is set only by the dedicated installed-app entry route and is
    # carried in Flask's signed session, never inferred from a user agent or an
    # arbitrary return URL. Browser visits to / keep their existing landing page.
    app_launch = session.get("app_launch") is True
    if app_launch:
        values.setdefault("email", request.form.get("email", "")[:254])
        values.setdefault("email_action", None)
    g.auth_csp_nonce = secrets.token_urlsafe(18)
    values.setdefault("active_form", "login")
    values.setdefault("auth_error", None)
    values.setdefault("auth_notice", None)
    values.setdefault("reset_token", None)
    auth_context = {
        "activeForm": values["active_form"],
        "csrfToken": generate_csrf(),
        "error": values["auth_error"],
        "notice": values["auth_notice"],
        "resetToken": values["reset_token"],
    }
    return render_template(
        "app_welcome.html" if app_launch else "authentication.html",
        auth_context=auth_context,
        csp_nonce=g.auth_csp_nonce,
        **values,
    )


def _logged_in_user():
    user = account_store.get_user(current_user.get_id())
    if user is None:
        logout_user()
        return None
    return user


def _price_currency():
    """Use a configured trusted geo header, then the browser's locale as a hint."""
    country = ""
    country_header = os.environ.get("ENNOIC_COUNTRY_HEADER", "").strip()
    if country_header:
        country = request.headers.get(country_header, "").strip().upper()
    if not country:
        for language, _quality in request.accept_languages:
            subtags = language.replace("_", "-").split("-")
            country = next((part.upper() for part in subtags[1:] if len(part) == 2 and part.isalpha()), "")
            if country:
                break
    return "INR" if country == "IN" else "USD"


def _is_configured_admin(email, *, email_verified=False):
    return (
        email_verified is True
        and isinstance(email, str)
        and email.strip().casefold() == "anukalpy29@gmail.com"
    )


def _post_login_redirect():
    target = session.pop("login_next", None)
    if target == url_for("checkout_page"):
        return redirect(target)
    return redirect(url_for("app_launch" if session.get("app_launch") is True else "home"))


@app.route("/app")
def app_launch():
    """Stable installed-app launch URL, without changing the browser homepage."""
    session["app_launch"] = True
    if current_user.is_authenticated:
        return home()
    return _auth_template(active_form="register" if request.args.get("form") == "register" else "login")


@app.route("/app/email-help")
def app_email_help():
    session["app_launch"] = True
    if current_user.is_authenticated:
        return redirect(url_for("app_launch"))
    action = "verify" if request.args.get("action") == "verify" else "reset"
    return _auth_template(email_action=action)


@app.route("/")
def home():
    if current_user.is_authenticated:
        # Initial HTML only needs subscription status; usage counters load via
        # /api/entitlements. Avoid three extra database round trips on navigation.
        account_plan = entitlements.account_subscription_status(current_user.id)["plan"]
        return render_template(
            "index.html", user=current_user, account_plan=account_plan,
            csrf_token=generate_csrf(),
        )
    g.landing_csp_nonce = secrets.token_urlsafe(18)
    return render_template("landing.html", csp_nonce=g.landing_csp_nonce)


@app.route("/settings")
@login_required
def settings_page():
    account = entitlements.account_subscription_status(current_user.id)
    return render_template(
        "settings.html",
        user=current_user,
        account=account,
        is_admin=_is_configured_admin(
            current_user.email, email_verified=current_user.email_verified
        ),
        csrf_token=generate_csrf(),
    )


@app.route("/pricing")
def pricing_page():
    g.pricing_csp_nonce = secrets.token_urlsafe(18)
    return render_template(
        "pricing.html",
        csrf_token=generate_csrf() if current_user.is_authenticated else "",
        authenticated=current_user.is_authenticated,
        csp_nonce=g.pricing_csp_nonce,
    )


@app.route("/terms")
def terms_page():
    return render_template("terms.html")


@app.route("/privacy")
def privacy_page():
    return render_template("privacy.html")


@app.route("/refund-policy")
def refund_policy_page():
    return render_template("refund_policy.html")


@app.route("/checkout")
def checkout_page():
    return redirect(url_for("pricing_page"))


@app.route("/api/checkout/contact", methods=["POST"])
@login_required
@limiter.limit("10 per hour")
def save_checkout_contact():
    data = _request_data()
    if data is None:
        return jsonify({"error": "Contact details must be a JSON object."}), 400
    if data.get("save_contact") is not True:
        return jsonify({
            "saved": False,
            "message": "Your contact details will not be saved to your Ennoic account.",
        })
    try:
        contact = account_store.save_checkout_contact(
            current_user.id,
            data.get("name"),
            data.get("email"),
            data.get("contact"),
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({
        "saved": True,
        "message": "Your contact details were saved to your Ennoic account.",
    })


@app.route("/admin/users")
@login_required
def admin_users():
    if not _is_configured_admin(current_user.email, email_verified=current_user.email_verified):
        abort(404)
    users = account_store.list_admin_users()
    for account in users:
        subscription = entitlements.account_subscription_status(account["id"])
        account["plan"] = subscription["plan"]
        account["subscription_status"] = subscription["status"]
        account["period_end"] = subscription["period_end"]
        account["provider"] = subscription["provider"]
    return render_template(
        "admin_users.html",
        users=users,
        user_count=len(users),
        free_count=sum(account["plan"] == "free" for account in users),
        pro_count=sum(account["plan"] == "pro" for account in users),
    )


@app.route("/api/pricing")
def pricing_data():
    language = entitlements.language_for(request.args.get("lang", "en").split("-")[0].lower())
    currency = _price_currency()
    pricing = entitlements.public_pricing(language, selected_currency=currency)
    paddle_config = paddle_billing.checkout_configuration()
    pricing["checkout_available"] = paddle_config["ready"]
    pricing["checkout_message"] = paddle_config["message"]
    pricing["paddle"] = {
        "environment": paddle_config["environment"],
        "client_token": paddle_config["client_token"],
        "price_id": paddle_config["price_id"],
    }
    return jsonify(pricing)


@app.route("/api/entitlements")
@login_required
def current_entitlements():
    return jsonify(entitlements.account_entitlements(current_user.id))


@app.route("/api/paddle/checkout-intent", methods=["POST"])
@login_required
@limiter.limit("10 per hour")
def create_paddle_checkout_intent():
    config = paddle_billing.checkout_configuration()
    if not config["ready"]:
        return jsonify({"error": config["message"] or "Paddle checkout is not configured."}), 503
    if entitlements.account_entitlements(current_user.id)["plan"] == "pro":
        return jsonify({"error": "Your account already has an active Pro subscription."}), 409
    token = account_store.create_paddle_checkout_intent(
        current_user.id, config["environment"], config["price_id"]
    )
    return jsonify({"checkout_intent": token, "email": current_user.email}), 200


@app.route("/checkout/success")
def paddle_checkout_success():
    activation_confirmed = (
        current_user.is_authenticated
        and entitlements.account_subscription_status(current_user.id)["plan"] == "pro"
    )
    return render_template(
        "checkout_success.html",
        activation_confirmed=activation_confirmed,
        authenticated=current_user.is_authenticated,
        csrf_token=generate_csrf() if current_user.is_authenticated else "",
    )


@app.route("/checkout/success/reconcile", methods=["POST"])
@login_required
@limiter.limit("5 per hour")
def reconcile_paddle_subscription():
    env = paddle_billing.environment()
    configured_price = paddle_billing.price_id(env) if env else ""
    if not configured_price:
        return redirect(url_for("paddle_checkout_success", check="pending"))
    try:
        paddle_billing.PaddleAPI().reconcile_account_subscription(
            user_id=current_user.id,
            email=current_user.email,
            expected_price_id=configured_price,
        )
    except paddle_billing.PaddleUnavailable:
        return redirect(url_for("paddle_checkout_success", check="pending"))
    return redirect(url_for("paddle_checkout_success"))


@app.route("/api/paddle/webhook", methods=["POST"])
@csrf.exempt
def paddle_webhook():
    payload = request.get_data(cache=True)
    signature = request.headers.get("Paddle-Signature")
    try:
        processed = paddle_billing.process_webhook(payload, signature)
    except paddle_billing.PaddleUnavailable as exc:
        app.logger.error("paddle_webhook_unavailable reason=%s", exc)
        return jsonify({"error": "Paddle webhook processing is not configured."}), 503
    except ValueError as exc:
        app.logger.warning("paddle_webhook_rejected reason=%s", exc)
        return jsonify({"error": "Invalid Paddle webhook."}), 400
    return jsonify({"received": True, "processed": processed}), 200


@app.route("/api/paddle/portal", methods=["POST"])
@login_required
@limiter.limit("10 per hour")
def paddle_customer_portal():
    stored = account_store.get_paddle_subscription_for_portal(
        current_user.id, paddle_billing.environment() or ""
    )
    if not stored:
        return jsonify({"error": "No Paddle subscription is linked to this account."}), 404
    try:
        portal_url = paddle_billing.PaddleAPI().create_portal_session(
            stored["paddle_customer_id"], stored["paddle_subscription_id"]
        )
    except paddle_billing.PaddleUnavailable as exc:
        return jsonify({"error": str(exc)}), 503
    return jsonify({"url": portal_url}), 200


@app.route("/api/checkout", methods=["POST"])
@login_required
def create_checkout():
    data = _request_data() or {}
    contact_data = data.get("contact")
    if not isinstance(contact_data, dict):
        return jsonify({"error": "Checkout contact details are required."}), 400
    try:
        contact = account_store.validate_checkout_contact(
            contact_data.get("name"), contact_data.get("email"), contact_data.get("contact")
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    try:
        checkout = billing.subscription_service.create_checkout_session(
            user_id=current_user.id,
            plan=data.get("plan", "pro"),
            currency=_price_currency(),
            return_url=_trusted_external_url("pricing_page"),
            contact=contact,
        )
    except billing.PaymentProviderUnavailable as exc:
        return jsonify({"error": str(exc)}), 503
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    session["razorpay_pending_subscription_id"] = checkout["subscription_id"]
    return jsonify(checkout), 200


@app.route("/api/checkout/verify", methods=["POST"])
@login_required
def verify_checkout_payment():
    data = _request_data() or {}
    expected_subscription_id = session.get("razorpay_pending_subscription_id")
    subscription_id = data.get("razorpay_subscription_id")
    payment_id = data.get("razorpay_payment_id")
    signature = data.get("razorpay_signature")
    if (not expected_subscription_id or subscription_id != expected_subscription_id
            or not isinstance(payment_id, str) or not isinstance(signature, str)):
        return jsonify({"error": "Could not verify this Razorpay checkout."}), 400
    try:
        verified = billing.subscription_service.verify_checkout_payment(
            subscription_id=expected_subscription_id,
            payment_id=payment_id,
            signature=signature,
        )
    except billing.PaymentProviderUnavailable as exc:
        return jsonify({"error": str(exc)}), 503
    if not verified:
        return jsonify({"error": "Razorpay payment signature is invalid."}), 400
    session.pop("razorpay_pending_subscription_id", None)
    return jsonify({
        "verified": True,
        "message": "Payment authorization was verified. Pro access will update when Razorpay confirms the subscription.",
    })


@app.route("/api/razorpay/webhook", methods=["POST"])
@csrf.exempt
def razorpay_webhook():
    payload = request.get_data(cache=True)
    signature = request.headers.get("X-Razorpay-Signature")
    try:
        processed = billing.subscription_service.process_provider_event(payload, signature)
    except billing.PaymentProviderUnavailable as exc:
        return jsonify({"error": str(exc)}), 503
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"received": True, "processed": processed}), 200


@app.route("/login")
def login_page():
    if current_user.is_authenticated:
        return _post_login_redirect()
    active_form = "register" if request.args.get("form") == "register" else "login"
    auth_notice = (
        "Account deletion is scheduled. Sign in again within 30 days to restore your account."
        if request.args.get("deletion") == "scheduled"
        else None
    )
    return _auth_template(active_form=active_form, auth_notice=auth_notice)


@app.route("/auth/google")
def google_login():
    if current_user.is_authenticated:
        return _post_login_redirect()
    if not _google_oauth_configured():
        return _google_login_error("Google sign-in is not configured. Ask the server administrator.", 503)

    state = secrets.token_urlsafe(32)
    nonce = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).rstrip(b"=").decode("ascii")
    session["google_oauth"] = {"state": state, "nonce": nonce, "verifier": verifier}
    params = {
        "client_id": _google_oauth_value("CLIENT_ID"),
        "redirect_uri": _trusted_external_url("google_callback"),
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
        "nonce": nonce,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "prompt": "select_account",
    }
    from urllib.parse import urlencode
    return redirect("https://accounts.google.com/o/oauth2/v2/auth?" + urlencode(params))


@app.route("/auth/google/callback")
def google_callback():
    oauth_session = session.pop("google_oauth", None)
    if not _google_oauth_configured():
        return _google_login_error("Google sign-in is not configured. Ask the server administrator.", 503)
    if not isinstance(oauth_session, dict) or not secrets.compare_digest(
        str(request.args.get("state", "")), str(oauth_session.get("state", ""))
    ):
        return _google_login_error("Google sign-in could not be verified. Please try again.")
    if request.args.get("error") or not request.args.get("code"):
        return _google_login_error("Google sign-in was cancelled or could not be completed.")

    redirect_uri = _trusted_external_url("google_callback")
    try:
        token_response = requests.post(
            "https://oauth2.googleapis.com/token",
            data={
                "code": request.args["code"],
                "client_id": _google_oauth_value("CLIENT_ID"),
                "client_secret": _google_oauth_value("CLIENT_SECRET"),
                "redirect_uri": redirect_uri,
                "grant_type": "authorization_code",
                "code_verifier": oauth_session["verifier"],
            },
            timeout=10,
        )
        token_response.raise_for_status()
        id_token_value = token_response.json().get("id_token")
        if not id_token_value:
            raise ValueError("Google did not return an identity token.")
        from google.auth.transport.requests import Request as GoogleRequest
        from google.oauth2 import id_token
        claims = id_token.verify_oauth2_token(
            id_token_value, GoogleRequest(), _google_oauth_value("CLIENT_ID")
        )
    except ImportError as error:
        app.logger.error("google_sign_in_dependency_missing error_type=%s", type(error).__name__)
        return _google_login_error(
            "Google sign-in verification is unavailable. Install the app requirements and restart the server.",
            503,
        )
    except (requests.RequestException, ValueError, KeyError, TypeError) as error:
        app.logger.warning("google_sign_in_failed error_type=%s", type(error).__name__)
        return _google_login_error("Google sign-in could not be completed. Please try again.")
    except Exception as error:
        # Token verification can raise provider-specific validation errors.
        app.logger.warning("google_sign_in_failed error_type=%s", type(error).__name__)
        return _google_login_error("Google sign-in could not be verified. Please try again.")

    if claims.get("nonce") != oauth_session.get("nonce"):
        return _google_login_error("Google sign-in could not be verified. Please try again.")
    email = claims.get("email")
    if not claims.get("email_verified") or not isinstance(email, str):
        return _google_login_error("Use a Google account with a verified email address.")
    try:
        user = account_store.get_user_by_email(email)
        if user is None:
            user = account_store.create_user(email, secrets.token_urlsafe(48))
            if user is not None:
                notify_discord("registration")
            if user is None:
                # Another callback may have created this address at the same time.
                user = account_store.get_user_by_email(email)
            elif account_store.verify_email(user["id"]):
                user = account_store.get_user(user["id"])
        if user is None or not user["email_verified"]:
            return _google_login_error("This email is awaiting verification. Sign in after verifying it.")
        if user["deleted_at"] is not None:
            if not account_store.cancel_user_deletion(user["id"]):
                return _google_login_error("This account is past its recovery period and is being deleted.")
            user = account_store.get_user(user["id"])
            session.pop("account_deletion_id", None)
    except ValueError:
        return _google_login_error("Google returned an invalid email address.")

    login_user(Account(user), fresh=True)
    session["account_auth_version"] = user["auth_version"]
    session.permanent = True
    account_store.record_user_login(user["id"], request.remote_addr)
    notify_discord("login")
    return _post_login_redirect()


@app.route("/auth/register", methods=["POST"])
@limiter.limit("5 per hour")
def register():
    if not _email_delivery_configured():
        return _auth_template(
            auth_error="Account email delivery is not configured. Ask the server administrator before signing up."
        ), 503
    email, password = request.form.get("email", ""), request.form.get("password", "")
    try:
        user = account_store.create_user(email, password)
    except ValueError as exc:
        return _auth_template(auth_error=str(exc), active_form="register"), 400
    if user:
        notify_discord("registration")
        _send_token_email(user, "verify")
    return _auth_template(
        auth_notice="If this address can be registered, a verification link will be sent. Check your inbox and spam folder.",
        active_form="login",
    ), 202


@app.route("/auth/continue", methods=["POST"])
@limiter.limit("10 per minute")
def continue_auth():
    email = request.form.get("email", "")
    password = request.form.get("password", "")
    try:
        account_store.validate_password(password)
    except ValueError as exc:
        return _auth_template(auth_error=str(exc)), 400
    try:
        user = account_store.get_user_by_email(email)
    except ValueError as exc:
        return _auth_template(auth_error=str(exc)), 400

    if user is not None:
        if not account_store.verify_password(user, password):
            if not _email_delivery_configured():
                return _auth_template(
                    auth_error="Account email delivery is temporarily unavailable. Please try again later."
                ), 503
            return _auth_template(auth_notice=_AUTH_CONTINUE_NOTICE), 202
        if not user["email_verified"]:
            if not _email_delivery_configured():
                return _auth_template(
                    auth_error="Account email delivery is temporarily unavailable. Please try again later."
                ), 503
            return _auth_template(auth_notice=_AUTH_CONTINUE_NOTICE), 202
        if user["deleted_at"] is not None:
            if not account_store.cancel_user_deletion(user["id"]):
                return _auth_template(auth_error="Email or password is incorrect."), 401
            user = account_store.get_user(user["id"])
            session.pop("account_deletion_id", None)
        login_user(Account(user), fresh=True)
        session["account_auth_version"] = user["auth_version"]
        session.permanent = True
        account_store.record_user_login(user["id"], request.remote_addr)
        notify_discord("login")
        return _post_login_redirect()

    if not _email_delivery_configured():
        return _auth_template(
            auth_error="Account email delivery is temporarily unavailable. Please try again later."
        ), 503

    try:
        user = account_store.create_user(email, password)
    except ValueError as exc:
        return _auth_template(auth_error=str(exc)), 400
    if user is None:
        # A parallel request may have created the account after the initial lookup.
        user = account_store.get_user_by_email(email)
        if user and account_store.verify_password(user, password) and user["email_verified"]:
            login_user(Account(user), fresh=True)
            session["account_auth_version"] = user["auth_version"]
            session.permanent = True
            account_store.record_user_login(user["id"], request.remote_addr)
            notify_discord("login")
            return _post_login_redirect()
    else:
        notify_discord("registration")
        _send_token_email(user, "verify")
    return _auth_template(
        auth_notice=_AUTH_CONTINUE_NOTICE
    ), 202


@app.route("/auth/login", methods=["POST"])
@limiter.limit("10 per minute")
def login():
    email = request.form.get("email", "")
    password = request.form.get("password", "")
    try:
        user = account_store.get_user_by_email(email)
    except ValueError:
        user = None
    if not account_store.verify_password(user, password) and not (
        user is None and isinstance(password, str)
        and check_password_hash(_dummy_password_hash, password)
    ):
        return _auth_template(auth_error="Email or password is incorrect."), 401
    if user is None or not user["email_verified"]:
        return _auth_template(auth_error="Email or password is incorrect."), 401
    if user["deleted_at"] is not None:
        if not account_store.cancel_user_deletion(user["id"]):
            return _auth_template(auth_error="Email or password is incorrect."), 401
        user = account_store.get_user(user["id"])
        session.pop("account_deletion_id", None)
    login_user(Account(user), fresh=True)
    session["account_auth_version"] = user["auth_version"]
    session.permanent = True
    account_store.record_user_login(user["id"], request.remote_addr)
    notify_discord("login")
    return _post_login_redirect()


@app.route("/auth/logout", methods=["POST"])
@login_required
def logout():
    app_launch = session.get("app_launch") is True
    logout_user()
    session.clear()
    return redirect(url_for("app_launch" if app_launch else "home"))


@app.route("/auth/verify/<token>")
@limiter.limit("10 per minute")
def verify_email(token):
    user = _consume_email_token(token, "verify", max_age=60 * 60)
    if user is None or not account_store.verify_email(user["id"]):
        return _auth_template(auth_error="That verification link is invalid or expired."), 400
    return _auth_template(auth_notice="Your email is verified. You can now sign in."), 200


@app.route("/auth/resend-verification", methods=["POST"])
@limiter.limit("3 per hour")
def resend_verification():
    try:
        user = account_store.get_user_by_email(request.form.get("email", ""))
        if user and not user["email_verified"]:
            _send_token_email(user, "verify")
        elif user:
            app.logger.info("account_verification_resend_skipped reason=already_verified")
        else:
            app.logger.info("account_verification_resend_skipped reason=no_matching_account")
    except ValueError:
        app.logger.info("account_verification_resend_skipped reason=invalid_email")
    return _auth_template(
        auth_notice="If an unverified account matches this address, a verification email will be sent. Check your inbox and spam folder."
    ), 202


@app.route("/auth/password-reset", methods=["POST"])
@limiter.limit("3 per hour")
def request_password_reset():
    try:
        user = account_store.get_user_by_email(request.form.get("email", ""))
        if user and user["email_verified"] and user["deleted_at"] is None:
            _send_token_email(user, "reset")
    except ValueError:
        pass
    return _auth_template(
        auth_notice="If an account matches this address, a password reset email will be sent."
    ), 202


@app.route("/auth/password-reset/<token>", methods=["GET", "POST"])
@limiter.limit("10 per hour", methods=["POST"])
def reset_password(token):
    if request.method == "GET":
        try:
            _token_serializer("reset").loads(token, max_age=30 * 60)
        except (BadSignature, SignatureExpired):
            return _auth_template(auth_error="That password reset link is invalid or expired."), 400
        return _auth_template(reset_token=token, active_form="reset")

    password = request.form.get("password", "")
    try:
        account_store.validate_password(password)
    except ValueError as exc:
        return _auth_template(auth_error=str(exc), reset_token=token, active_form="reset"), 400
    user = _consume_email_token(token, "reset", max_age=30 * 60)
    if user is None or not account_store.update_password(user["id"], password):
        return _auth_template(auth_error="That password reset link is invalid or expired."), 400
    return _auth_template(auth_notice="Your password has been changed. You can now sign in."), 200


@app.route("/api/auth/me")
def auth_status():
    if not current_user.is_authenticated:
        return jsonify({"authenticated": False})
    return jsonify({"authenticated": True, "user": {"id": current_user.id, "email": current_user.email}})


@app.route("/api/account/password", methods=["POST"])
@login_required
def change_password():
    data = _request_data()
    if data is None:
        return jsonify({"error": "Password details must be a JSON object."}), 400
    user = _logged_in_user()
    if user is None or not account_store.verify_password(user, data.get("current_password")):
        return jsonify({"error": "Current password is incorrect."}), 400
    try:
        account_store.validate_password(data.get("new_password"))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    if not account_store.update_password(user["id"], data["new_password"]):
        g.discord_error_notified = True
        notify_discord("server_error", endpoint="change_password", error_type="PasswordUpdateFailed")
        return jsonify({"error": "The password could not be changed."}), 500
    updated = account_store.get_user(user["id"])
    app_launch = session.get("app_launch") is True
    logout_user()
    session.clear()
    if app_launch:
        session["app_launch"] = True
    login_user(Account(updated), fresh=True)
    session["account_auth_version"] = updated["auth_version"]
    session.permanent = True
    account_store.record_user_login(updated["id"], request.remote_addr)
    return jsonify({"changed": True, "csrf_token": generate_csrf()})


@app.route("/api/account", methods=["DELETE"])
def delete_account():
    data = _request_data()
    typed_email = data.get("email") if data else None
    if not isinstance(typed_email, str):
        return jsonify({"error": "Enter your account email to confirm deletion."}), 400
    user_id = session.get("account_deletion_id") or session.get("_user_id")
    pending = account_store.get_scheduled_deletion(user_id) if isinstance(user_id, str) else None
    if pending:
        account_email = pending["email"]
    elif current_user.is_authenticated:
        user = _logged_in_user()
        if user is None:
            return jsonify({"error": "Sign in again before deleting your account."}), 401
        user_id, account_email = user["id"], user["email"]
    else:
        return jsonify({"error": "Sign in again before deleting your account."}), 401
    confirmation = typed_email.strip().casefold().encode("utf-8")
    if not secrets.compare_digest(confirmation, account_email.encode("utf-8")):
        return jsonify({"error": "The email did not match your account."}), 400

    # A deletion request must not disable the account until every linked,
    # potentially recurring provider subscription has confirmed cancellation.
    try:
        provider_cancellation_confirmed = _ensure_account_subscriptions_canceled(user_id)
    except (paddle_billing.PaddleUnavailable, billing.PaymentProviderUnavailable) as exc:
        app.logger.warning(
            "account_deletion_billing_cancellation_failed error_type=%s",
            type(exc).__name__,
        )
        return jsonify({
            "error": (
                "We couldn't confirm that your subscription will stop renewing, so your account "
                "was not scheduled for deletion and remains available. Retry, use Manage subscription "
                "in Settings if available, or contact ennoic.app@gmail.com."
            )
        }), 503

    try:
        deletion = pending or account_store.schedule_user_deletion(user_id, request.remote_addr)
    except Exception as exc:
        g.discord_error_notified = True
        notify_discord(
            "server_error", endpoint="delete_account", error_type=type(exc).__name__
        )
        app.logger.error(
            "account_deletion_schedule_failed error_type=%s",
            type(exc).__name__,
        )
        if provider_cancellation_confirmed:
            message = (
                "Your provider cancellation was confirmed, but Ennoic couldn't schedule account "
                "deletion. Your account remains available; retry the deletion request."
            )
        else:
            message = "Ennoic couldn't schedule account deletion. Your account remains available; retry the request."
        return jsonify({"error": message}), 503
    if deletion is None:
        g.discord_error_notified = True
        notify_discord("server_error", endpoint="delete_account", error_type="DeletionScheduleFailed")
        return jsonify({"error": "The account could not be scheduled for deletion."}), 500
    session["account_deletion_id"] = user_id
    return jsonify({"scheduled": True, "purge_after": deletion["purge_after"]}), 202


def _ensure_account_subscriptions_canceled(user_id):
    """Cancel all billable subscriptions linked to this authenticated account."""
    provider_cancellation_confirmed = False
    configured_paddle_environment = paddle_billing.environment()
    unresolved_intents = account_store.get_unlinked_paddle_checkout_intents(user_id)
    if unresolved_intents:
        account = account_store.get_user(user_id)
        if account is None:
            raise paddle_billing.PaddleUnavailable(
                "The account could not be verified while checking Paddle checkout attempts."
            )
        for intent in unresolved_intents:
            environment = intent["environment"]
            if environment == "sandbox" and configured_paddle_environment != "sandbox":
                continue
            # A delayed webhook may have left a paid Paddle subscription unmapped.
            # Reconcile only against this account's opaque checkout intents; never
            # adopt a subscription based on an email match alone.
            paddle_billing.PaddleAPI(env=environment).reconcile_account_subscription(
                user_id=user_id,
                email=account["email"],
                expected_price_id=intent["price_id"],
            )
    paddle_subscriptions = account_store.get_paddle_subscriptions_for_deletion(user_id)
    for subscription in paddle_subscriptions:
        environment = subscription["environment"]
        # Sandbox subscriptions cannot bill a customer. Check them only while the
        # app itself is configured for sandbox; always check production records.
        if environment == "sandbox" and configured_paddle_environment != "sandbox":
            continue
        confirmation = paddle_billing.PaddleAPI(env=environment).ensure_subscription_canceled(
            subscription["paddle_subscription_id"],
            expected_customer_id=subscription["paddle_customer_id"],
        )
        try:
            recorded = account_store.record_paddle_cancellation_schedule(
                user_id, environment, subscription["paddle_subscription_id"],
                confirmation.get("effective_at"),
            )
        except Exception as exc:
            raise paddle_billing.PaddleUnavailable(
                "The confirmed Paddle cancellation could not be saved to the Ennoic account."
            ) from exc
        if not recorded:
            raise paddle_billing.PaddleUnavailable(
                "The confirmed Paddle cancellation could not be saved to the Ennoic account."
            )
        provider_cancellation_confirmed = True

    legacy = account_store.get_account_subscription_for_deletion(user_id)
    if not legacy:
        return provider_cancellation_confirmed
    status = legacy.get("status")
    needs_provider_check = status in {"active", "past_due"} or (
        status == "canceled"
        and account_store._timestamp_after(legacy.get("period_end"), account_store._now())
    ) or (status == "expired" and legacy.get("provider") == "razorpay")
    if not needs_provider_check:
        return provider_cancellation_confirmed
    if status in {"past_due", "expired"} and legacy.get("provider") == "razorpay" \
            and not legacy.get("provider_subscription_id"):
        raise billing.PaymentProviderUnavailable(
            "The stored Razorpay record has no provider subscription reference."
        )
    if legacy.get("provider") != "razorpay" or not legacy.get("provider_subscription_id"):
        raise billing.PaymentProviderUnavailable(
            "The stored subscription cannot be matched to a supported billing provider."
        )
    confirmation = billing.subscription_service.ensure_subscription_canceled(
        subscription_id=legacy["provider_subscription_id"], expected_user_id=user_id
    )
    if status != "expired":
        try:
            recorded = account_store.record_legacy_cancellation_schedule(
                user_id, legacy["provider"], legacy["provider_subscription_id"],
                confirmation.get("effective_at"),
            )
        except Exception as exc:
            raise billing.PaymentProviderUnavailable(
                "The confirmed provider cancellation could not be saved to the Ennoic account."
            ) from exc
        if not recorded:
            raise billing.PaymentProviderUnavailable(
                "The confirmed provider cancellation could not be saved to the Ennoic account."
            )
    return True


def _purge_due_account_deletions(limit=25):
    """Reconfirm provider cancellation immediately before permanently purging data."""
    due_user_ids = account_store.due_deletion_user_ids(limit=limit)
    verified_user_ids = []
    for user_id in due_user_ids:
        try:
            _ensure_account_subscriptions_canceled(user_id)
        except (paddle_billing.PaddleUnavailable, billing.PaymentProviderUnavailable) as exc:
            app.logger.warning(
                "account_deletion_purge_deferred error_type=%s",
                type(exc).__name__,
            )
            continue
        verified_user_ids.append(user_id)
    return account_store.purge_due_deletions(
        verified_user_ids=verified_user_ids,
        limit=limit,
    )


@app.route("/api/account/memories")
@login_required
def memories():
    return jsonify({"memories": account_store.list_memories(current_user.id)})


@app.route("/api/csrf")
@login_required
def csrf_token():
    return jsonify({"csrf_token": generate_csrf()})


@app.route("/api/conversations", methods=["GET", "POST"])
@login_required
def conversations():
    if request.method == "GET":
        plan = entitlements.account_entitlements(current_user.id)
        history_limit = plan["usage"]["conversations"]["limit"]
        saved = account_store.list_conversations(current_user.id, limit=history_limit)
        total = account_store.count_conversations(current_user.id)
        return jsonify({
            "conversations": saved,
            "total": total,
            "history_limit": history_limit,
            "has_more": history_limit is not None and total > history_limit,
        })
    data = _request_data()
    title = data.get("title") if data else None
    if not isinstance(title, str) or not title.strip() or len(title.strip()) > 120:
        return jsonify({"error": "Conversation title must be between 1 and 120 characters."}), 400
    conversation = account_store.create_conversation(current_user.id, secrets.token_urlsafe(24), title.strip())
    return jsonify({"conversation": conversation}), 201


@app.route("/api/conversations/import", methods=["POST"])
@login_required
def import_conversations():
    data = _request_data()
    try:
        imported = account_store.import_conversations(current_user.id, data.get("conversations") if data else None)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"conversations": imported, "imported": len(imported)}), 201


@app.route("/api/conversations", methods=["DELETE"])
@login_required
def delete_all_conversations():
    return jsonify({"deleted": account_store.delete_all_conversations(current_user.id)})


@app.route("/api/conversations/<conversation_id>", methods=["PUT", "DELETE"])
@login_required
def conversation_detail(conversation_id):
    if request.method == "DELETE":
        if not account_store.delete_conversation(current_user.id, conversation_id):
            return jsonify({"error": "Conversation not found."}), 404
        return jsonify({"deleted": True})
    data = _request_data()
    if data is None or set(data) - {"title", "pinned"}:
        return jsonify({"error": "Only the conversation title and pin state can be changed."}), 400
    title = data.get("title")
    pinned = data.get("pinned")
    if title is not None and (
        not isinstance(title, str) or not title.strip() or len(title.strip()) > 120
    ):
        return jsonify({"error": "Conversation title must be between 1 and 120 characters."}), 400
    if pinned is not None and not isinstance(pinned, bool):
        return jsonify({"error": "Pinned must be true or false."}), 400
    conversation = account_store.update_conversation(
        current_user.id,
        conversation_id,
        title=title.strip() if title is not None else None,
        pinned=pinned,
    )
    if conversation is None:
        return jsonify({"error": "Conversation not found."}), 404
    return jsonify({"conversation": conversation})


@app.route("/api/tasks", methods=["GET", "POST"])
@login_required
def tasks():
    if request.method == "GET":
        try:
            result = task_store.list_tasks(
                view=request.args.get("view", "all"),
                status=request.args.get("status"),
                project=request.args.get("project"),
                search=request.args.get("search"),
                user_id=current_user.id,
            )
            return jsonify({"tasks": result})
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
    data = _request_data()
    if data is None:
        return jsonify({"error": "Task details must be a JSON object."}), 400
    try:
        task = task_store.create_task(data, user_id=current_user.id)
        return jsonify({"task": task}), 201
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400


@app.route("/api/tasks/<task_id>", methods=["PATCH", "DELETE"])
@login_required
def task_detail(task_id):
    if request.method == "DELETE":
        if not task_store.delete_task(task_id, user_id=current_user.id):
            return jsonify({"error": "Task not found."}), 404
        return jsonify({"deleted": True})
    data = _request_data()
    if data is None:
        return jsonify({"error": "Task changes must be a JSON object."}), 400
    try:
        task = task_store.update_task(task_id, data, user_id=current_user.id)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    if task is None:
        return jsonify({"error": "Task not found."}), 404
    return jsonify({"task": task})


@app.route("/api/tasks/<task_id>/status", methods=["PATCH"])
@login_required
def task_status(task_id):
    data = _request_data()
    if data is None:
        return jsonify({"error": "Task status must be a JSON object."}), 400
    try:
        task = task_store.set_task_status(task_id, data.get("status"), user_id=current_user.id)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    if task is None:
        return jsonify({"error": "Task not found."}), 404
    return jsonify({"task": task})


@app.route("/api/tasks/confirm-duplicate", methods=["POST"])
@login_required
def confirm_task_duplicate():
    data = _request_data()
    if data is None:
        return jsonify({"error": "A duplicate choice is required."}), 400
    proposal_id = data.get("proposal_id")
    choice = data.get("choice")
    candidate_id = data.get("candidate_id")
    if not isinstance(proposal_id, str) or not proposal_id:
        return jsonify({"error": "A valid duplicate proposal is required."}), 400
    try:
        result = resolve_task_confirmation(proposal_id, choice, candidate_id, user_id=current_user.id)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    if result is None:
        return jsonify({"error": "That duplicate question has already been resolved."}), 404
    conversation_id = result.get("conversation_id")
    conversation = account_store.get_conversation(current_user.id, conversation_id)
    if conversation is not None:
        messages = conversation["messages"]
        for message in messages:
            if message.get("role") == "assistant":
                for confirmation in message.get("task_confirmations", []):
                    if confirmation.get("id") == proposal_id:
                        confirmation["resolved"] = True
                        confirmation["resolution"] = result["reply"]
        account_store.update_conversation(
            current_user.id,
            conversation_id,
            messages=messages,
        )
    return jsonify(result)


@app.route("/api/chat", methods=["POST"])
@login_required
@limiter.limit("30 per minute")
def chat():
    mark_trace("chat.route.enter")
    data = _request_data() or {}
    message = data.get("message")
    if not isinstance(message, str) or not message.strip():
        return jsonify({"error": "A message is required."}), 400
    conversation_id = data.get("conversation_id")
    if not isinstance(conversation_id, str) or not conversation_id or len(conversation_id) > 200:
        return jsonify({"error": "A valid conversation ID is required."}), 400
    step_started_at = time.perf_counter()
    conversation = account_store.get_conversation(current_user.id, conversation_id)
    mark_trace("conversation.loaded", duration_ms=round((time.perf_counter() - step_started_at) * 1000))
    if conversation is None:
        return jsonify({"error": "Conversation not found."}), 404
    step_started_at = time.perf_counter()
    chat_limits = entitlements.chat_limits(current_user.id)
    history_limit = chat_limits["conversation_history_limit"]
    if history_limit is not None and not account_store.conversation_is_in_window(
        current_user.id, conversation_id, history_limit
    ):
        return jsonify({
            "error": "This conversation is outside your plan's available history. Upgrade to Pro to access older conversations.",
            "code": "conversation_history_limit",
            "upgrade_url": url_for("pricing_page"),
            "limit": history_limit,
        }), 403
    mark_trace("entitlements.loaded", duration_ms=round((time.perf_counter() - step_started_at) * 1000))

    request_id = data.get("request_id") or request.headers.get("Idempotency-Key") or secrets.token_urlsafe(24)
    if not isinstance(request_id, str) or not (16 <= len(request_id) <= 100):
        return jsonify({"error": "A valid request ID is required."}), 400
    request_hash = hashlib.sha256(
        (conversation_id + "\0" + message.strip()).encode("utf-8")
    ).hexdigest()
    step_started_at = time.perf_counter()
    billable = message_requires_ai(message.strip(), conversation_id, current_user.id)
    mark_trace("billing.classified", duration_ms=round((time.perf_counter() - step_started_at) * 1000))
    step_started_at = time.perf_counter()
    period_start, period_end = entitlements.current_period()
    ai_limit = chat_limits["ai_requests_monthly"]
    reservation = account_store.reserve_ai_request(
        current_user.id,
        request_id,
        request_hash,
        period_start,
        period_end,
        ai_limit,
        billable=billable,
        conversation_id=conversation_id,
    )
    mark_trace(
        "usage.reserved",
        duration_ms=round((time.perf_counter() - step_started_at) * 1000),
        state=reservation["state"],
    )
    if reservation["state"] == "limit":
        return jsonify({
            "error": f"You've used all {ai_limit} AI requests in this month's Free plan. Your usage resets on {period_end[:10]}.",
            "code": "ai_usage_limit",
            "used": reservation["used"],
            "limit": ai_limit,
            "resets_at": period_end,
            "upgrade_url": url_for("pricing_page"),
        }), 429
    if reservation["state"] == "conflict":
        return jsonify({"error": "This request ID was already used for a different message.", "code": "idempotency_conflict"}), 409
    if reservation["state"] == "in_progress":
        return jsonify({"error": "This message is still being processed. Try again in a moment.", "code": "request_in_progress"}), 409
    if reservation["state"] == "completed":
        mark_trace("idempotency.cached_response")
        return jsonify(reservation["response"]), reservation["response_status"]
    if reservation["state"] == "response_expired":
        return jsonify({
            "error": "This earlier response is no longer available. Send a new message to continue.",
            "code": "cached_response_unavailable",
        }), 410
    try:
        step_started_at = time.perf_counter()
        result = process_message_with_metadata(
            message.strip(),
            conversation["messages"],
            conversation_id,
            user_id=current_user.id,
            max_memories=chat_limits["memory_capacity"],
        )
        mark_trace("message.processing.complete", duration_ms=round((time.perf_counter() - step_started_at) * 1000))
        assistant_message = {
            "role": "assistant",
            "text": result["reply"],
            "truncated": result.get("truncated") is True,
            "task_confirmations": result.get("task_confirmations", []),
        }
        if result.get("resolved_proposal_id"):
            for saved_message in conversation["messages"]:
                if saved_message.get("role") == "assistant":
                    for confirmation in saved_message.get("task_confirmations", []):
                        if confirmation.get("id") == result["resolved_proposal_id"]:
                            confirmation["resolved"] = True
                            confirmation["resolution"] = result["reply"]
            account_store.update_conversation(
                current_user.id,
                conversation_id,
                messages=conversation["messages"],
            )
        step_started_at = time.perf_counter()
        account_store.append_conversation_turn(
            current_user.id,
            conversation_id,
            message.strip(),
            conversation["messages"],
            assistant_message,
        )
        account_store.complete_ai_request(current_user.id, request_id, result)
        mark_trace("conversation.persisted", duration_ms=round((time.perf_counter() - step_started_at) * 1000))
        notify_discord(
            "chat_response",
            email=current_user.email,
            message=message.strip(),
            reply=result.get("reply", ""),
        )
        return jsonify(result)
    except account_store.MemoryCapacityReached as exc:
        response = {
            "error": f"Your plan's {exc.limit}-memory capacity is full. Existing memories are still available. Upgrade for more capacity.",
            "code": "memory_capacity_limit",
            "used": exc.used,
            "limit": exc.limit,
            "upgrade_url": url_for("pricing_page"),
        }
        account_store.complete_ai_request(current_user.id, request_id, response, response_status=403)
        return jsonify(response), 403
    except Exception as error:
        account_store.release_ai_request(current_user.id, request_id)
        request_ref = hashlib.sha256(request_id.encode("utf-8")).hexdigest()[:12]
        error_location = _chat_error_location(error)
        mark_trace(
            "chat.failed",
            error_type=type(error).__name__,
            error_location=error_location,
        )
        app.logger.error(
            "chat_request_failed request_ref=%s error_type=%s error_location=%s",
            request_ref,
            type(error).__name__,
            error_location,
        )
        g.discord_error_notified = True
        notify_discord(
            "chat_error",
            reference=request_ref,
            error_type=type(error).__name__,
            location=error_location,
        )
        return jsonify({"error": "Ennoic couldn't process that message."}), 500


if __name__ == "__main__":
    app.run(debug=False)
