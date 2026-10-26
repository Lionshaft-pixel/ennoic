import base64
import hashlib
import os
import secrets
import smtplib
import ssl
from threading import Event, Lock, Thread
from email.message import EmailMessage
from pathlib import Path
from urllib.parse import urljoin

import requests

from flask import Flask, g, jsonify, redirect, render_template, request, session, url_for
from flask_login import LoginManager, UserMixin, current_user, login_required, login_user, logout_user
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_wtf.csrf import CSRFError, CSRFProtect, generate_csrf
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from werkzeug.security import check_password_hash, generate_password_hash
from dotenv import load_dotenv

import account_store
import task_store
from main import process_message_with_metadata
from task_management import resolve_task_confirmation


app = Flask(__name__, instance_relative_config=True)
load_dotenv(Path(__file__).resolve().with_name(".env"), override=False)


def _environment_value(*names, default=None):
    for name in names:
        value = os.environ.get(name)
        if value:
            return value
    return default


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
    return redirect(url_for("login_page"))


@app.before_request
def initialize_database():
    path = str(account_store.database_path().resolve())
    with _database_initialization_lock:
        if path not in _initialized_databases:
            account_store.initialize()
            _initialized_databases.add(path)
    if not app.testing:
        _start_account_deletion_worker()


def _run_account_deletion_worker():
    while not _account_deletion_worker_stop.is_set():
        try:
            account_store.purge_due_deletions()
        except Exception:
            app.logger.exception("account_deletion_purge_failed")
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
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    bundle_nonce = getattr(g, "landing_csp_nonce", None) or getattr(g, "auth_csp_nonce", None)
    if bundle_nonce:
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            f"script-src 'self' 'nonce-{bundle_nonce}' 'strict-dynamic' 'unsafe-eval' blob: https://cdn.jsdelivr.net; "
            "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
            "font-src 'self' data: https://fonts.gstatic.com; img-src 'self' data: blob:; "
            "connect-src 'self' https://fonts.googleapis.com https://fonts.gstatic.com; "
            "object-src 'none'; base-uri 'self'; frame-ancestors 'none'"
        )
    else:
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self' https://cdn.jsdelivr.net; "
            "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
            "font-src 'self' https://fonts.gstatic.com; img-src 'self' data:; "
            "connect-src 'self'; object-src 'none'; base-uri 'self'; frame-ancestors 'none'",
        )
    if request.is_secure:
        response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
    if response.mimetype == "text/html":
        response.headers.setdefault("Cache-Control", "no-store")
    return response


@app.errorhandler(CSRFError)
def handle_csrf_error(error):
    if request.path.startswith("/api/"):
        return jsonify({"error": "Your session security token expired. Reload and try again."}), 400
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
    host = _environment_value("ENNOIC_SMTP_HOST", "SMTP_HOST")
    sender = _environment_value("ENNOIC_SMTP_FROM", "SMTP_FROM_EMAIL", "SMTP_FROM")
    if not host or not sender:
        app.logger.warning("Account email is not configured; set ENNOIC_SMTP_HOST and ENNOIC_SMTP_FROM.")
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
    return bool(
        _environment_value("ENNOIC_SMTP_HOST", "SMTP_HOST")
        and _environment_value("ENNOIC_SMTP_FROM", "SMTP_FROM_EMAIL", "SMTP_FROM")
    )


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
    configured_url = _environment_value("ENNOIC_PUBLIC_URL", "PUBLIC_URL", default=request.url_root)
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
        "authentication.html",
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


@app.route("/")
def home():
    if current_user.is_authenticated:
        return render_template("index.html", user=current_user, csrf_token=generate_csrf())
    g.landing_csp_nonce = secrets.token_urlsafe(18)
    return render_template("landing.html", csp_nonce=g.landing_csp_nonce)


@app.route("/login")
def login_page():
    if current_user.is_authenticated:
        return redirect(url_for("home"))
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
        return redirect(url_for("home"))
    if not _google_oauth_configured():
        return _google_login_error("Google sign-in is not configured. Ask the server administrator.", 503)

    state = secrets.token_urlsafe(32)
    nonce = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).rstrip(b"=").decode("ascii")
    session["google_oauth"] = {"state": state, "nonce": nonce, "verifier": verifier}
    params = {
        "client_id": _google_oauth_value("CLIENT_ID"),
        "redirect_uri": url_for("google_callback", _external=True),
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

    redirect_uri = url_for("google_callback", _external=True)
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
    return redirect(url_for("home"))


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
        _send_token_email(user, "verify")
    return _auth_template(
        auth_notice="If the address can be registered, a verification link will be sent. If it does not arrive, check the app terminal for account_email_delivery_failed and its stage.",
        active_form="login",
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
    return redirect(url_for("home"))


@app.route("/auth/logout", methods=["POST"])
@login_required
def logout():
    logout_user()
    session.clear()
    return redirect(url_for("home"))


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
    sent = False
    try:
        user = account_store.get_user_by_email(request.form.get("email", ""))
        if user and not user["email_verified"]:
            sent = _send_token_email(user, "verify")
        elif user:
            app.logger.info("account_verification_resend_skipped reason=already_verified")
        else:
            app.logger.info("account_verification_resend_skipped reason=no_matching_account")
    except ValueError:
        app.logger.info("account_verification_resend_skipped reason=invalid_email")
    return _auth_template(
        auth_notice=(
            "Verification email sent. Check your inbox and spam folder."
            if sent
            else "If an unverified account matches that address, Ennoic tried to send a link. If none arrives, check the app terminal for account_email_delivery_failed and its stage."
        )
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
        auth_notice="Email sent"
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
        return jsonify({"error": "The password could not be changed."}), 500
    updated = account_store.get_user(user["id"])
    logout_user()
    session.clear()
    login_user(Account(updated), fresh=True)
    session["account_auth_version"] = updated["auth_version"]
    session.permanent = True
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
    deletion = pending or account_store.schedule_user_deletion(user_id, request.remote_addr)
    if deletion is None:
        return jsonify({"error": "The account could not be scheduled for deletion."}), 500
    session["account_deletion_id"] = user_id
    return jsonify({"scheduled": True, "purge_after": deletion["purge_after"]}), 202


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
        return jsonify({"conversations": account_store.list_conversations(current_user.id)})
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
        return jsonify({"task": task_store.create_task(data, user_id=current_user.id)}), 201
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
def chat():
    data = _request_data() or {}
    message = data.get("message")
    if not isinstance(message, str) or not message.strip():
        return jsonify({"error": "A message is required."}), 400
    conversation_id = data.get("conversation_id")
    if not isinstance(conversation_id, str) or not conversation_id or len(conversation_id) > 200:
        return jsonify({"error": "A valid conversation ID is required."}), 400
    conversation = account_store.get_conversation(current_user.id, conversation_id)
    if conversation is None:
        return jsonify({"error": "Conversation not found."}), 404
    try:
        result = process_message_with_metadata(
            message.strip(),
            conversation["messages"],
            conversation_id,
            user_id=current_user.id,
        )
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
        account_store.append_conversation_turn(
            current_user.id,
            conversation_id,
            message.strip(),
            conversation["messages"],
            assistant_message,
        )
        return jsonify(result)
    except Exception:
        app.logger.exception("Failed to process chat message")
        return jsonify({"error": "Ennoic couldn't process that message."}), 500


if __name__ == "__main__":
    app.run(debug=False)
