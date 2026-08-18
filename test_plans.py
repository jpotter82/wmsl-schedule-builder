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

    check("Free is capped on teams", plans.limit(free, 'teams') == 12)
    check("...and unmetered on saved seasons",
          plans.limit(free, 'saved_seasons') is plans.UNLIMITED)
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
    free_client = client('member@example.com')          # back on Free
    for n in ('season-one', 'season-two', 'season-three'):
        check("a Free account saves %s" % n,
              free_client.post('/api/configs/' + n,
                               json={'divisions': {}, 'general': {}}).status_code == 200)

    # The cap is on the size of the league, so it has to be refused where the work
    # is asked for -- at the run, not at the save.
    big = {'divisions': {'A': {'team_count': 8}, 'B': {'team_count': 8},
                         'C': {'team_count': 6}}, 'general': {}}
    r = free_client.post('/api/run', json={'config': big})
    check("a 22-team run is refused with 402", r.status_code == 402)
    body = r.get_json()
    check("...as an upgrade prompt naming both numbers",
          body.get('error') == 'upgrade_required'
          and body.get('learn_more') == '/pricing'
          and '12' in body['message'] and '22' in body['message'])
    check("saving that same oversized season is still allowed",
          free_client.post('/api/configs/too-big', json=big).status_code == 200)

    auth.set_plan(member.user_id, plans.PRO, changed_by=boss)
    pro_client = client('member@example.com')
    r = pro_client.post('/api/run', json={'config': big})
    check("Pro is not refused for size", r.status_code != 402)

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
    # The shipped default has to sit under the cap, or a new account meets the
    # paywall on its first run before it has done anything at all.
    check("the shipped default fits inside the Free cap",
          plans.team_count(cfg) <= plans.PLANS[plans.FREE]['limits']['teams'])
    check("teams is the rule that is live", plans.enforced('teams'))
    boss_free = auth.get_user_by_id(boss.user_id)         # boss is back on Free
    allowed, _ = plans.check_team_limit(boss_free, cfg)
    check("...so the default season runs on Free", allowed)

    edge = copy.deepcopy(cfg)
    edge['divisions'] = {'A': dict(cfg['divisions']['A'], team_count=12)}
    check("exactly 12 teams is allowed", plans.check_team_limit(boss_free, edge)[0])
    edge['divisions'] = {'A': dict(cfg['divisions']['A'], team_count=13)}
    check("13 is not", plans.check_team_limit(boss_free, edge)[0] is False)

    auth.set_plan(boss.user_id, plans.PRO, changed_by=boss)
    big_cfg = copy.deepcopy(cfg)
    big_cfg['divisions'] = {'A': {'team_count': 22}}
    allowed, _ = plans.check_team_limit(auth.get_user_by_id(boss.user_id), big_cfg)
    check("Pro is unaffected by the cap", allowed)
    auth.set_plan(boss.user_id, plans.FREE, changed_by=boss)

    # An undecided limit must still refuse nothing.
    check("saved seasons stays dormant", not plans.enforced('saved_seasons'))
    allowed, _ = plans.within_limit(boss_free, 'saved_seasons', 10 ** 6)
    check("...so any number of saved seasons is allowed", allowed)

    # ---------------------------------------------------------------- pages
    check("/pricing is public", appmod.app.test_client().get('/pricing').status_code == 200)
    body = free_client.get('/pricing').get_data(as_text=True)
    check("pricing shows both plans and no payment button",
          '$199 CAD/year' in body and 'coming soon' in body)
    me = free_client.get('/api/plan').get_json()
    check("the account can read its own plan", me['plan'] in plans.VALID_PLANS)

    shutil.rmtree(tmp, ignore_errors=True)
    print("\n  %d/%d passed" % (sum(RESULTS), len(RESULTS)))
    return 0 if all(RESULTS) else 1


if __name__ == '__main__':
    sys.exit(main())
