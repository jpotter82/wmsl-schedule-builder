"""Accounts, sessions and per-user storage.

Deliberately small: stdlib sqlite3 rather than an ORM, and werkzeug's password
hashing (which ships with Flask) rather than a separate crypto dependency. The
schema is a handful of columns and is unlikely to grow much.

Everything a user owns lives under data/users/<id>/. Those paths are always derived
from the logged-in session, never from anything in the request, so one user cannot
reach another's files even if they craft the URL by hand.
"""
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

BASE_DIR = Path(__file__).resolve().parent


def env(name, default=''):
    """Read a SKEDDY_<name> setting.

    Falls back to the old WMSL_<name> so an instance deployed before the rename
    keeps working across a git pull. That matters most for DATA_DIR: silently
    ignoring it would point the app at a new, empty data directory and make every
    existing account look as though it had vanished. The fallback can be dropped
    once no deployment sets the old names.
    """
    return os.environ.get(f'SKEDDY_{name}', os.environ.get(f'WMSL_{name}', default))


# Where accounts, per-user files and the signing key live.
#
# Override with SKEDDY_DATA_DIR. Worth doing on shared hosting where the application
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
    it. Set SKEDDY_DATA_DIR to somewhere above the document root instead.
    """
    try:
        DATA_DIR.relative_to(BASE_DIR)
    except ValueError:
        return  # already outside the app directory
    sys.stderr.write(
        "skeddy: WARNING data directory is inside the application directory\n"
        f"skeddy:   {DATA_DIR}\n"
        "skeddy:   If the app directory is your web root, accounts, password hashes\n"
        "skeddy:   and the session signing key may be downloadable over HTTP.\n"
        "skeddy:   Set SKEDDY_DATA_DIR to a path above the document root.\n")


_warn_if_data_dir_is_web_reachable()
USERS_DIR = DATA_DIR / 'users'
DB_PATH = DATA_DIR / 'app.db'
SECRET_KEY_PATH = DATA_DIR / 'secret_key'

# Registration limits. Open signup on a public URL attracts junk, and with no email
# verification these are the only brakes available.
MIN_PASSWORD_LENGTH = 10
MAX_SIGNUPS_PER_IP_PER_DAY = 5

# Optional shared secret. When SKEDDY_INVITE_CODE is set in the environment,
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
                created_at    TEXT    NOT NULL,
                last_login_at TEXT
            );

            CREATE TABLE IF NOT EXISTS signups (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                ip         TEXT NOT NULL,
                created_at REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_signups_ip ON signups(ip, created_at);
        """)


# ---------------------------------------------------------------- user model
class User(UserMixin):
    def __init__(self, row):
        self.id = str(row['id'])
        self.user_id = int(row['id'])
        self.email = row['email']
        self.display_name = row['display_name'] or row['email'].split('@')[0]
        self.is_admin = bool(row['is_admin'])

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


def validate_registration(email, password, invite=None):
    """Return an error message, or None when the details are acceptable."""
    email = (email or '').strip()
    if not EMAIL_RE.match(email):
        return "Enter a valid email address."
    if len(password or '') < MIN_PASSWORD_LENGTH:
        return f"Password must be at least {MIN_PASSWORD_LENGTH} characters."
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
