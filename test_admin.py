"""Tests for the admin panel guard.

Run with:  python test_admin.py

Admin is the only role in the app, and the panel is where it is granted, so the
interesting cases are all about who is refused rather than what renders.
"""
import os
import shutil
import sys
import tempfile

RESULTS = []
PW = 'a-long-enough-password'


def check(name, condition):
    RESULTS.append(bool(condition))
    print(("  PASS  " if condition else "  FAIL  ") + name)


def main():
    tmp = tempfile.mkdtemp(prefix='skedworx-admin-')
    os.environ['SKEDWORX_DATA_DIR'] = tmp
    os.environ['SKEDWORX_INSECURE_COOKIES'] = '1'
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import app as appmod
    import auth

    appmod.app.config.update(TESTING=True)
    auth.create_user('boss@example.com', PW)        # first account becomes admin
    auth.create_user('member@example.com', PW)

    def client(email):
        c = appmod.app.test_client()
        c.post('/login', data={'email': email, 'password': PW})
        return c

    boss, member, anon = client('boss@example.com'), client('member@example.com'), appmod.app.test_client()
    boss_id = auth.get_user_by_email('boss@example.com').user_id
    member_id = auth.get_user_by_email('member@example.com').user_id

    check("admin can open the panel", boss.get('/admin').status_code == 200)
    # 404 rather than 403: no reason to confirm the page exists to someone who
    # cannot use it.
    check("non-admin gets 404", member.get('/admin').status_code == 404)
    r = anon.get('/admin')
    check("anonymous is sent to login", r.status_code == 302 and 'login' in r.headers['Location'])

    check("non-admin cannot promote themselves",
          member.post('/admin/users/%d/admin' % member_id, data={'make': '1'}).status_code == 404)
    check("...and did not become one", auth.get_user_by_id(member_id).is_admin is False)

    boss.post('/admin/users/%d/admin' % member_id, data={'make': '1'})
    check("admin can promote", auth.get_user_by_id(member_id).is_admin is True)
    check("the promoted account can open the panel",
          client('member@example.com').get('/admin').status_code == 200)

    boss.post('/admin/users/%d/admin' % member_id, data={'make': '0'})
    check("admin can demote", auth.get_user_by_id(member_id).is_admin is False)

    # There is no other way back in short of editing the database on the host.
    r = boss.post('/admin/users/%d/admin' % boss_id, data={'make': '0'}, follow_redirects=True)
    check("the last admin cannot demote themselves", auth.get_user_by_id(boss_id).is_admin is True)
    check("...and is told why", 'only admin' in r.get_data(as_text=True))

    body = boss.get('/admin').get_data(as_text=True)
    check("no password hash reaches the page",
          'scrypt' not in body and 'pbkdf2' not in body)

    shutil.rmtree(tmp, ignore_errors=True)
    print("\n  %d/%d passed" % (sum(RESULTS), len(RESULTS)))
    return 0 if all(RESULTS) else 1


if __name__ == '__main__':
    sys.exit(main())
