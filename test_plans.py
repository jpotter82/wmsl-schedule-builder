"""Roles, plans, entitlements and plan management.

Run with:  python test_plans.py

The point of the plan service is that application code asks whether an action is
allowed, never what plan someone is on. These tests lean on the boundaries that
matter commercially: that a plan cannot be self-granted, that role and plan stay
independent, and that a defined-but-unagreed limit does not quietly start
refusing people.
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
    tmp = tempfile.mkdtemp(prefix='skedworx-plans-')
    os.environ['SKEDWORX_DATA_DIR'] = tmp
    os.environ['SKEDWORX_INSECURE_COOKIES'] = '1'
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import app as appmod
    import auth
    import plans

    appmod.app.config.update(TESTING=True)
    boss = auth.create_user('boss@example.com', PW)      # first account -> admin
    member = auth.create_user('member@example.com', PW)

    def client(email):
        c = appmod.app.test_client()
        c.post('/login', data={'email': email, 'password': PW})
        return c

    # ---------------------------------------------------------------- roles
    admin_c, user_c = client('boss@example.com'), client('member@example.com')
    check("admin reaches /admin", admin_c.get('/admin').status_code == 200)
    check("normal user does not", user_c.get('/admin').status_code == 404)

    r = admin_c.post('/admin/users/%d/admin' % boss.user_id,
                     data={'make': '0'}, follow_redirects=True)
    check("the last admin still cannot be demoted",
          auth.get_user_by_id(boss.user_id).is_admin is True)

    # ---------------------------------------------------------------- plans
    check("a new account defaults to Free", plans.plan_of(member) == plans.FREE)
    check("an account created before plans existed reads as Free",
          plans.plan_of(object()) == plans.FREE)

    # --------------------------------------------------------- entitlements
    auth.set_plan(member.user_id, plans.PRO, changed_by=boss)
    pro = auth.get_user_by_id(member.user_id)
    free = auth.get_user_by_id(boss.user_id)

    check("Free resolves Free limits", plans.limit(free, 'saved_seasons') == 1)
    check("Pro resolves unlimited saved seasons",
          plans.limit(pro, 'saved_seasons') is plans.UNLIMITED)
    check("both plans may replay a seed",
          plans.can(free, 'seed_replay') and plans.can(pro, 'seed_replay'))

    # Role and plan must not leak into one another.
    check("an admin on Free is still Free", free.is_admin and plans.plan_of(free) == plans.FREE)
    auth.set_plan(boss.user_id, plans.PRO, changed_by=boss)
    boss_pro = auth.get_user_by_id(boss.user_id)
    check("an admin on Pro is Pro, by plan not by role",
          boss_pro.is_admin and plans.plan_of(boss_pro) == plans.PRO)
    check("a non-admin can hold Pro", (not pro.is_admin) and plans.plan_of(pro) == plans.PRO)
    auth.set_plan(boss.user_id, plans.FREE, changed_by=boss)

    # ------------------------------------------------------ plan management
    ok, _ = auth.set_plan(member.user_id, plans.FREE, changed_by=boss)
    check("admin can move Pro -> Free", ok and plans.plan_of(auth.get_user_by_id(member.user_id)) == plans.FREE)
    ok, _ = auth.set_plan(member.user_id, plans.PRO, changed_by=boss)
    check("admin can move Free -> Pro", ok and plans.plan_of(auth.get_user_by_id(member.user_id)) == plans.PRO)

    ok, msg = auth.set_plan(member.user_id, 'enterprise', changed_by=boss)
    check("an unknown plan is rejected", ok is False)
    check("...and nothing changed", plans.plan_of(auth.get_user_by_id(member.user_id)) == plans.PRO)

    # A normal user must not be able to move anyone, including themselves.
    check("a user cannot change their own plan",
          user_c.post('/admin/users/%d/plan' % member.user_id,
                      data={'plan': 'pro'}).status_code == 404)
    check("a user cannot change someone else's plan",
          user_c.post('/admin/users/%d/plan' % boss.user_id,
                      data={'plan': 'pro'}).status_code == 404)
    auth.set_plan(member.user_id, plans.FREE, changed_by=boss)

    # --------------------------------------------------------------- audit
    log = auth.plan_changes(50)
    check("plan changes are recorded", len(log) >= 4)
    latest = log[0]
    check("...with target, both plans, who and when",
          latest['user_email'] == 'member@example.com'
          and latest['to_plan'] == plans.FREE
          and latest['from_plan'] == plans.PRO
          and latest['changed_by_email'] == 'boss@example.com'
          and bool(latest['created_at']))

    # --------------------------------------------------- enforcement, server side
    free_user = client('member@example.com')            # back on Free
    r1 = free_user.post('/api/configs/season-one', json={'divisions': {}, 'general': {}})
    check("a Free account can save its first season", r1.status_code == 200)
    r2 = free_user.post('/api/configs/season-two', json={'divisions': {}, 'general': {}})
    check("a second season is refused with 402", r2.status_code == 402)
    body = r2.get_json()
    check("...as an upgrade prompt, not a bare error",
          body.get('error') == 'upgrade_required' and body.get('learn_more') == '/pricing')
    r3 = free_user.post('/api/configs/season-one', json={'divisions': {}, 'general': {}})
    check("overwriting the season they already have still works", r3.status_code == 200)

    auth.set_plan(member.user_id, plans.PRO, changed_by=boss)
    pro_user = client('member@example.com')
    check("Pro can save beyond the Free ceiling",
          pro_user.post('/api/configs/season-two', json={'divisions': {}, 'general': {}}).status_code == 200)

    # An undecided limit must not refuse anything.
    check("export is not enforced yet", not plans.enforced('export'))
    check("...and stays available to Free", plans.can(free, 'export'))
    check("runs per month is not enforced yet", not plans.enforced('runs_per_month'))
    allowed, _ = plans.within_limit(free, 'runs_per_month', 10 ** 6)
    check("...so a huge run count is still allowed", allowed)

    # ------------------------------------------------- teams limit, both ways
    import copy
    from scheduler_wrapper import DEFAULT_CONFIG
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    check("the shipped default counts 22 teams", plans.team_count(cfg) == 22)
    check("teams is not enforced today", not plans.enforced('teams'))
    allowed, _ = plans.check_team_limit(auth.get_user_by_id(boss.user_id), cfg)
    check("...so a large league runs on Free", allowed)

    # Switch it on temporarily to prove the gate works when a number is agreed.
    plans.ENFORCED.add('teams')
    plans.PLANS[plans.FREE]['limits']['teams'] = 12
    try:
        free_user = auth.get_user_by_id(boss.user_id)      # boss is back on Free
        allowed, payload = plans.check_team_limit(free_user, cfg)
        check("a 22-team season is refused at a cap of 12", allowed is False)
        check("...with the upgrade payload, naming both numbers",
              payload['error'] == 'upgrade_required'
              and '12' in payload['message'] and '22' in payload['message'])

        small = copy.deepcopy(DEFAULT_CONFIG)
        small['divisions'] = {'A': dict(small['divisions']['A'], team_count=8)}
        allowed, _ = plans.check_team_limit(free_user, small)
        check("an 8-team league still runs free", allowed)

        pro_user = auth.get_user_by_id(mem_id) if False else None
        auth.set_plan(boss.user_id, plans.PRO, changed_by=boss)
        allowed, _ = plans.check_team_limit(auth.get_user_by_id(boss.user_id), cfg)
        check("Pro is unaffected by the cap", allowed)
        auth.set_plan(boss.user_id, plans.FREE, changed_by=boss)
    finally:
        plans.ENFORCED.discard('teams')
        plans.PLANS[plans.FREE]['limits']['teams'] = plans.UNLIMITED

    # ---------------------------------------------------------------- pages
    check("/pricing is public", appmod.app.test_client().get('/pricing').status_code == 200)
    body = free_user.get('/pricing').get_data(as_text=True)
    check("pricing shows both plans and no payment button",
          '$199 CAD/year' in body and 'coming soon' in body)
    me = free_user.get('/api/plan').get_json()
    check("the account can read its own plan", me['plan'] in plans.VALID_PLANS)

    shutil.rmtree(tmp, ignore_errors=True)
    print("\n  %d/%d passed" % (sum(RESULTS), len(RESULTS)))
    return 0 if all(RESULTS) else 1


if __name__ == '__main__':
    sys.exit(main())
