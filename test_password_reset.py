"""Tests for the password reset flow.

Run with:  python test_password_reset.py

Plain stdlib, no pytest, so it works on the host with nothing extra installed. Every
test runs against a throwaway data directory, so it never touches a real database.

These lean on the security properties rather than the happy path, because that is
where the flow can be wrong while still appearing to work: a reset that leaks which
addresses are registered, a token that can be replayed, or a reset that leaves the
attacker's session logged in all behave perfectly from the user's point of view.
"""
import io
import os
import re
import shutil
import sqlite3
import sys
import tempfile
import time

RESULTS = []


def check(name, condition):
    RESULTS.append(bool(condition))
    print(("  PASS  " if condition else "  FAIL  ") + name)


def main():
    tmp = tempfile.mkdtemp(prefix='skeddy-test-')
    os.environ['SKEDDY_DATA_DIR'] = tmp
    os.environ['SKEDDY_INSECURE_COOKIES'] = '1'
    for leftover in ('SKEDDY_SMTP_HOST', 'SKEDDY_SMTP_FROM', 'SKEDDY_BASE_URL',
                     'SKEDDY_INVITE_CODE'):
        os.environ.pop(leftover, None)

    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import app as appmod
    import auth

    appmod.app.config.update(TESTING=True)
    client = appmod.app.test_client()
    auth.create_user('owner@example.com', 'original-password-1')

    check("GET /forgot renders", client.get('/forgot').status_code == 200)

    # No account enumeration: a registered and an unregistered address must be
    # indistinguishable, or the form becomes a way to discover who has an account.
    captured, real_stderr = io.StringIO(), sys.stderr
    sys.stderr = captured
    known = client.post('/forgot', data={'email': 'owner@example.com'})
    unknown = client.post('/forgot', data={'email': 'nobody@example.com'})
    sys.stderr = real_stderr

    check("known and unknown emails share a status code",
          known.status_code == unknown.status_code == 200)
    check("known and unknown emails share a response body",
          known.get_data(as_text=True).replace('owner@example.com', 'X')
          == unknown.get_data(as_text=True).replace('nobody@example.com', 'X'))

    log = captured.getvalue()
    match = re.search(r'(/reset/[A-Za-z0-9_\-]+)', log)
    check("reset link goes to stderr when SMTP is unconfigured", match is not None)
    check("reset link never appears in the HTTP response",
          '/reset/' not in known.get_data(as_text=True))
    check("no token is issued for an unregistered address", log.count('/reset/') == 1)

    path = match.group(1)

    check("valid token opens the reset page", client.get(path).status_code == 200)
    check("garbage token is rejected", client.get('/reset/nope').status_code == 400)
    check("mismatched confirmation is rejected",
          client.post(path, data={'password': 'a-good-long-password',
                                  'confirm': 'a-different-password'}).status_code == 400)
    check("too-short password is rejected",
          client.post(path, data={'password': 'short', 'confirm': 'short'}).status_code == 400)

    # A reset exists largely to lock out whoever should not be there, so any session
    # opened before it must stop working.
    other = appmod.app.test_client()
    other.post('/login', data={'email': 'owner@example.com',
                               'password': 'original-password-1'})
    check("a session works before the reset", other.get('/app').status_code == 200)

    done = client.post(path, data={'password': 'brand-new-password-2',
                                   'confirm': 'brand-new-password-2'})
    check("successful reset redirects to sign in",
          done.status_code == 302 and 'reset=1' in done.headers['Location'])
    check("the reset invalidates other sessions", other.get('/app').status_code == 302)
    check("the token cannot be replayed", client.get(path).status_code == 400)
    check("the old password no longer works",
          auth.verify_password('owner@example.com', 'original-password-1') is None)
    check("the new password works",
          auth.verify_password('owner@example.com', 'brand-new-password-2') is not None)

    user = auth.get_user_by_email('owner@example.com')
    stale = auth.create_reset_token(user, '1.2.3.4')
    with sqlite3.connect(os.path.join(tmp, 'app.db')) as conn:
        conn.execute("UPDATE password_resets SET expires_at = ? WHERE used_at IS NULL",
                     (time.time() - 1,))
    check("an expired token is rejected", auth.user_for_reset_token(stale) is None)

    # Requesting again must retire the previous link, so an older mail cannot still
    # be redeemed by anyone who sees it.
    first = auth.create_reset_token(user, '1.2.3.4')
    auth.create_reset_token(user, '1.2.3.4')
    check("requesting a new link retires the previous one",
          auth.user_for_reset_token(first) is None)

    for _ in range(auth.MAX_RESET_REQUESTS_PER_IP_PER_HOUR):
        auth.create_reset_token(user, '9.9.9.9')
    check("the per-IP throttle trips", auth.reset_request_allowed('9.9.9.9') is False)
    check("the throttle is per IP, not global",
          auth.reset_request_allowed('8.8.8.8') is True)

    # The link is mailed to the account owner, so its host must not come from the
    # requester's Host header.
    os.environ['SKEDDY_BASE_URL'] = 'https://schedule.wmsl.ca'
    with appmod.app.test_request_context('/forgot', headers={'Host': 'evil.example'}):
        url = appmod._reset_url('abc')
    check("SKEDDY_BASE_URL overrides a spoofed Host header",
          url.startswith('https://schedule.wmsl.ca/'))
    del os.environ['SKEDDY_BASE_URL']

    shutil.rmtree(tmp, ignore_errors=True)

    print(f"\n  {sum(RESULTS)}/{len(RESULTS)} passed")
    return 0 if all(RESULTS) else 1


if __name__ == '__main__':
    sys.exit(main())
