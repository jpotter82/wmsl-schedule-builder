"""Accounts, sessions and per-user storage.

Deliberately small: stdlib sqlite3 rather than an ORM, and werkzeug's password
hashing (which ships with Flask) rather than a separate crypto dependency. The
schema is a handful of columns and is unlikely to grow much.

Everything a user owns lives under data/users/<id>/. Those paths are always derived
from the logged-in session, never from anything in the request, so one user cannot
reach another's files even if they craft the URL by hand.
"""
import hashlib
import hmac
import os
import re
import secrets
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from flask_login import UserMixin
from werkzeug.security import check_password_hash, generate_password_hash

import plans

BASE_DIR = Path(__file__).resolve().parent


def env(name, default=''):
    """Read a SKEDWORX_<name> setting.

    Falls back through the previous names — SKEDDY_<name>, then WMSL_<name> — so an
    instance deployed under an older name keeps working across a git pull. That
    matters most for DATA_DIR: silently ignoring it would point the app at a new,
    empty data directory and make every existing account look as though it had
    vanished. It matters again for the SMTP settings, where ignoring them would
    quietly stop password resets from being delivered.

    A rename is not worth an outage, so the old names keep working until every
    deployment has moved over.
    """
    for prefix in ('SKEDWORX_', 'SKEDDY_', 'WMSL_'):
        value = os.environ.get(f'{prefix}{name}')
        if value is not None:
            return value
    return default


# Where accounts, per-user files and the signing key live.
#
# Override with SKEDWORX_DATA_DIR. Worth doing on shared hosting where the application
# sits in the web root: pointing this somewhere above the docroot means user data
# and the database cannot be fetched over HTTP even if .htaccess is misconfigured
# or ignored.
DATA_DIR = Path(env('DATA_DIR') or (BASE_DIR / 'data')).resolve()


def _warn_if_data_dir_is_web_reachable():
    """Warn when the data directory sits inside the application directory.

    On shared hosting the application often IS the document root, which would make
    data/ fetchable over HTTP. That matters more than it sounds: secret_key signs
    session cookies, so anyone who downloads it can forge a session for any account
    without knowing a password, and app.db carries every account's password hash.

    An .htaccess in the repo blocks this, but .htaccess is not guaranteed to be
    honoured — AllowOverride can forbid it, and a rewrite mistake silently disables
    it. Set SKEDWORX_DATA_DIR to somewhere above the document root instead.
    """
    try:
        DATA_DIR.relative_to(BASE_DIR)
    except ValueError:
        return  # already outside the app directory
    sys.stderr.write(
        "skedworx: WARNING data directory is inside the application directory\n"
        f"skedworx:   {DATA_DIR}\n"
        "skedworx:   If the app directory is your web root, accounts, password hashes\n"
        "skedworx:   and the session signing key may be downloadable over HTTP.\n"
        "skedworx:   Set SKEDWORX_DATA_DIR to a path above the document root.\n")


_warn_if_data_dir_is_web_reachable()
USERS_DIR = DATA_DIR / 'users'
DB_PATH = DATA_DIR / 'app.db'
SECRET_KEY_PATH = DATA_DIR / 'secret_key'

# Registration limits. Open signup on a public URL attracts junk, and with no email
# verification these are the only brakes available.
MIN_PASSWORD_LENGTH = 10
MAX_SIGNUPS_PER_IP_PER_DAY = 5

# Password resets. An hour is long enough to find the mail and short enough that a
# link sitting in an inbox is not a standing key to the account.
RESET_TOKEN_TTL_SECONDS = 3600
MAX_RESET_REQUESTS_PER_IP_PER_HOUR = 5

# Optional shared secret. When SKEDWORX_INVITE_CODE is set in the environment,
# registration additionally requires it — a kill switch if open signup is abused,
# without needing a redeploy.
INVITE_CODE = env('INVITE_CODE').strip()

EMAIL_RE = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')


# ---------------------------------------------------------------- secret key
def get_secret_key():
    """Return a stable signing key, creating it once on first use.

    This MUST be stable across processes. Under CGI every request is a new process,
    so a key generated at import time would differ each time, invalidating every
    session cookie and silently logging everyone out on every request.
    """
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if SECRET_KEY_PATH.exists():
        key = SECRET_KEY_PATH.read_bytes().strip()
        if key:
            return key
    key = secrets.token_bytes(32)
    SECRET_KEY_PATH.write_bytes(key)
    try:
        os.chmod(SECRET_KEY_PATH, 0o600)
    except OSError:
        pass
    return key


# ---------------------------------------------------------------- database
def _connect():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with _connect() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                email         TEXT    NOT NULL,
                email_lower   TEXT    NOT NULL UNIQUE,
                password_hash TEXT    NOT NULL,
                display_name  TEXT,
                is_admin      INTEGER NOT NULL DEFAULT 0,
                plan          TEXT    NOT NULL DEFAULT 'free',
                created_at    TEXT    NOT NULL,
                last_login_at TEXT
            );

            CREATE TABLE IF NOT EXISTS signups (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                ip         TEXT NOT NULL,
                created_at REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_signups_ip ON signups(ip, created_at);

            CREATE TABLE IF NOT EXISTS password_resets (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id      INTEGER NOT NULL,
                token_hash   TEXT    NOT NULL UNIQUE,
                created_at   REAL    NOT NULL,
                expires_at   REAL    NOT NULL,
                used_at      REAL,
                requested_ip TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_resets_user ON password_resets(user_id);
            CREATE INDEX IF NOT EXISTS idx_resets_ip ON password_resets(requested_ip, created_at);

            -- Who changed whose plan, when. Deliberately one table rather than a
            -- general audit framework: plan changes are the only administrative
            -- action that moves money, and an unexplained upgrade is the one
            -- thing worth being able to reconstruct.
            CREATE TABLE IF NOT EXISTS plan_changes (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id      INTEGER NOT NULL,
                user_email   TEXT    NOT NULL,
                from_plan    TEXT,
                to_plan      TEXT    NOT NULL,
                changed_by   INTEGER,
                changed_by_email TEXT,
                created_at   TEXT    NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_plan_changes_user ON plan_changes(user_id, created_at);
        """)

        # Migrate databases created before plans existed. Everyone lands on Free,
        # which is what they effectively had.
        cols = {r['name'] for r in conn.execute("PRAGMA table_info(users)")}
        if 'plan' not in cols:
            conn.execute("ALTER TABLE users ADD COLUMN plan TEXT NOT NULL DEFAULT 'free'")
        conn.execute("UPDATE users SET plan = ? WHERE plan IS NULL OR plan = ''",
                     (plans.DEFAULT_PLAN,))


# ---------------------------------------------------------------- user model
def _password_stamp(password_hash):
    """Short fingerprint of the stored hash, used to tie a session to a password.

    The session cookie carries "<id>.<stamp>". Changing the password changes the
    stored hash, so every cookie issued beforehand stops resolving. Without this, a
    reset would leave an attacker's existing session logged in — which defeats the
    main reason people reset a password in the first place.

    The stamp is a hash of a hash, so the cookie leaks nothing useful about the
    password even though it travels to the browser.
    """
    return hashlib.sha256((password_hash or '').encode()).hexdigest()[:16]


class User(UserMixin):
    def __init__(self, row):
        self.user_id = int(row['id'])
        # Flask-Login stores this in the session and hands it back to the loader.
        self.password_stamp = _password_stamp(row['password_hash'])
        self.id = f"{self.user_id}.{self.password_stamp}"
        self.email = row['email']
        self.display_name = row['display_name'] or row['email'].split('@')[0]
        # Role and plan are independent: an admin may sit on Free, which is how the
        # free experience gets tested without a second account.
        self.is_admin = bool(row['is_admin'])
        self.plan = (row['plan'] if 'plan' in row.keys() else None) or plans.DEFAULT_PLAN

    # -- storage ---------------------------------------------------------
    @property
    def home(self):
        """Root of this user's private storage."""
        return USERS_DIR / str(self.user_id)

    def dir(self, name):
        """Return (and create) one of this user's storage directories."""
        d = self.home / name
        d.mkdir(parents=True, exist_ok=True)
        return d

    @property
    def state_file(self):
        self.home.mkdir(parents=True, exist_ok=True)
        return self.home / 'run_state.json'


def _row_to_user(row):
    return User(row) if row else None


def get_user_by_id(user_id):
    try:
        uid = int(user_id)
    except (TypeError, ValueError):
        return None
    with _connect() as conn:
        return _row_to_user(conn.execute(
            "SELECT * FROM users WHERE id = ?", (uid,)).fetchone())


def get_user_by_session_id(session_id):
    """Resolve the "<id>.<stamp>" value Flask-Login keeps in the session cookie.

    Rejects the cookie when the stamp does not match the current password hash, so
    changing a password signs out every other device. Cookies issued before this
    format existed carry a bare id and no stamp; they are rejected too, which costs
    everyone one extra sign-in on the deploy that introduces it.
    """
    uid, _, stamp = str(session_id or '').partition('.')
    user = get_user_by_id(uid)
    if user is None or not stamp:
        return None
    if not hmac.compare_digest(stamp, user.password_stamp):
        return None
    return user


def get_user_by_email(email):
    with _connect() as conn:
        return _row_to_user(conn.execute(
            "SELECT * FROM users WHERE email_lower = ?",
            ((email or '').strip().lower(),)).fetchone())


def user_count():
    with _connect() as conn:
        return conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()['n']


def verify_password(email, password):
    """Return the User when the credentials are right, else None."""
    with _connect() as conn:
        row = conn.execute("SELECT * FROM users WHERE email_lower = ?",
                           ((email or '').strip().lower(),)).fetchone()
    if not row:
        # Hash anyway so a missing account is not detectably faster than a wrong
        # password, which would let someone enumerate registered addresses.
        check_password_hash(generate_password_hash('x'), 'y')
        return None
    if not check_password_hash(row['password_hash'], password or ''):
        return None
    with _connect() as conn:
        conn.execute("UPDATE users SET last_login_at = ? WHERE id = ?",
                     (datetime.now(timezone.utc).isoformat(), row['id']))
    return _row_to_user(row)


def validate_password(password):
    """Return an error message, or None when the password is acceptable."""
    if len(password or '') < MIN_PASSWORD_LENGTH:
        return f"Password must be at least {MIN_PASSWORD_LENGTH} characters."
    return None


def validate_registration(email, password, invite=None):
    """Return an error message, or None when the details are acceptable."""
    email = (email or '').strip()
    if not EMAIL_RE.match(email):
        return "Enter a valid email address."
    bad_password = validate_password(password)
    if bad_password:
        return bad_password
    if INVITE_CODE and (invite or '').strip() != INVITE_CODE:
        return "That invite code is not valid."
    if get_user_by_email(email):
        return "An account already exists for that email address."
    return None


def signup_allowed(ip):
    """Crude per-IP throttle so open signup cannot be scripted into thousands of rows."""
    cutoff = time.time() - 86400
    with _connect() as conn:
        conn.execute("DELETE FROM signups WHERE created_at < ?", (cutoff,))
        n = conn.execute("SELECT COUNT(*) AS n FROM signups WHERE ip = ? AND created_at >= ?",
                         (ip or '', cutoff)).fetchone()['n']
    return n < MAX_SIGNUPS_PER_IP_PER_DAY


def record_signup(ip):
    with _connect() as conn:
        conn.execute("INSERT INTO signups (ip, created_at) VALUES (?, ?)",
                     (ip or '', time.time()))


# ---------------------------------------------------------------- password reset
def _hash_token(token):
    """Store only the hash of a reset token.

    Same reasoning as passwords: the database already holds enough to be worth
    protecting, and a leaked copy should not hand over live reset links. The token
    is high-entropy and single-use, so a plain SHA-256 is enough here — there is no
    weak secret to brute-force.
    """
    return hashlib.sha256((token or '').encode()).hexdigest()


def reset_request_allowed(ip):
    """Throttle reset requests per IP, so the form cannot be used to spam an inbox."""
    cutoff = time.time() - 3600
    with _connect() as conn:
        n = conn.execute(
            "SELECT COUNT(*) AS n FROM password_resets"
            " WHERE requested_ip = ? AND created_at >= ?",
            (ip or '', cutoff)).fetchone()['n']
    return n < MAX_RESET_REQUESTS_PER_IP_PER_HOUR


def create_reset_token(user, ip=None):
    """Issue a single-use reset token, invalidating any still outstanding."""
    token = secrets.token_urlsafe(32)
    now = time.time()
    with _connect() as conn:
        # Asking again should retire the previous link, so a forwarded or leaked
        # earlier mail cannot still be redeemed.
        conn.execute("UPDATE password_resets SET used_at = ?"
                     " WHERE user_id = ? AND used_at IS NULL", (now, user.user_id))
        conn.execute(
            "INSERT INTO password_resets (user_id, token_hash, created_at,"
            " expires_at, requested_ip) VALUES (?, ?, ?, ?, ?)",
            (user.user_id, _hash_token(token), now,
             now + RESET_TOKEN_TTL_SECONDS, ip or ''))
    return token


def user_for_reset_token(token):
    """Return the User a live token belongs to, else None."""
    if not token:
        return None
    with _connect() as conn:
        row = conn.execute("SELECT * FROM password_resets WHERE token_hash = ?",
                           (_hash_token(token),)).fetchone()
    if not row or row['used_at'] is not None or row['expires_at'] < time.time():
        return None
    return get_user_by_id(row['user_id'])


def consume_reset_token(token, new_password):
    """Set the new password and burn the token. False if the token is not usable.

    Both happen in one transaction: a password changed without the token being
    marked used would leave a working link behind.
    """
    user = user_for_reset_token(token)
    if user is None:
        return False
    now = time.time()
    with _connect() as conn:
        conn.execute("UPDATE users SET password_hash = ? WHERE id = ?",
                     (generate_password_hash(new_password), user.user_id))
        conn.execute("UPDATE password_resets SET used_at = ?"
                     " WHERE user_id = ? AND used_at IS NULL", (now, user.user_id))
    return True


def set_password(user_id, new_password):
    """Change a password directly. For administrative use from the host."""
    with _connect() as conn:
        cur = conn.execute("UPDATE users SET password_hash = ? WHERE id = ?",
                           (generate_password_hash(new_password), int(user_id)))
        return cur.rowcount > 0


def list_users():
    """Every account, with enough to run a user list. Never returns hashes."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT id, email, display_name, is_admin, plan, created_at, last_login_at"
            " FROM users ORDER BY id").fetchall()
    return [dict(r) for r in rows]


def admin_count():
    with _connect() as conn:
        return conn.execute(
            "SELECT COUNT(*) AS n FROM users WHERE is_admin = 1").fetchone()['n']


def set_admin(user_id, make_admin):
    """Grant or revoke admin. Returns (ok, message).

    Refuses to remove the last admin: there is no other way back in short of
    editing the database on the host, and locking yourself out of your own
    instance is a bad afternoon.
    """
    user = get_user_by_id(user_id)
    if user is None:
        return False, "No such account."
    if user.is_admin and not make_admin and admin_count() <= 1:
        return False, "That is the only admin. Promote someone else first."
    with _connect() as conn:
        conn.execute("UPDATE users SET is_admin = ? WHERE id = ?",
                     (1 if make_admin else 0, int(user_id)))
    return True, ("%s is now an admin." % user.email if make_admin
                  else "%s is no longer an admin." % user.email)


def set_plan(user_id, new_plan, changed_by=None):
    """Move a user onto a plan, recording who did it. Returns (ok, message).

    The plan is validated here rather than at the route, so no caller can write a
    value the entitlement service would not recognise.
    """
    if not plans.is_valid_plan(new_plan):
        return False, "Unknown plan."
    user = get_user_by_id(user_id)
    if user is None:
        return False, "No such account."
    if user.plan == new_plan:
        return True, "%s is already on %s." % (user.email, plans.PLAN_LABELS[new_plan]['name'])

    with _connect() as conn:
        conn.execute("UPDATE users SET plan = ? WHERE id = ?", (new_plan, int(user_id)))
        conn.execute(
            "INSERT INTO plan_changes (user_id, user_email, from_plan, to_plan,"
            " changed_by, changed_by_email, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (user.user_id, user.email, user.plan, new_plan,
             getattr(changed_by, 'user_id', None), getattr(changed_by, 'email', None),
             datetime.now(timezone.utc).isoformat()))
    return True, "%s moved from %s to %s." % (
        user.email, plans.PLAN_LABELS[user.plan]['name'], plans.PLAN_LABELS[new_plan]['name'])


def plan_changes(limit=50):
    """Most recent plan changes, newest first."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM plan_changes ORDER BY id DESC LIMIT ?", (int(limit),)).fetchall()
    return [dict(r) for r in rows]


def create_user(email, password, display_name=None, is_admin=None):
    """Create an account. The first account created becomes the admin."""
    email = (email or '').strip()
    if is_admin is None:
        is_admin = (user_count() == 0)
    with _connect() as conn:
        cur = conn.execute(
            "INSERT INTO users (email, email_lower, password_hash, display_name,"
            " is_admin, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (email, email.lower(), generate_password_hash(password),
             display_name or email.split('@')[0], 1 if is_admin else 0,
             datetime.now(timezone.utc).isoformat()))
        uid = cur.lastrowid
    user = get_user_by_id(uid)
    for sub in ('uploads', 'output', 'configs'):
        user.dir(sub)
    return user
