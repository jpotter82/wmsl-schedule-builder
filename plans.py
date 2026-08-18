"""Plans and entitlements.

The application never asks what plan someone is on. It asks whether they may do a
thing — `plans.can(user, 'export')` — or what their ceiling is —
`plans.limit(user, 'saved_seasons')`. That indirection is the whole point: today
the answer comes from `users.plan`, and when billing arrives it will come from a
subscription record instead, without a single caller changing.

Roles and plans are independent. `is_admin` controls access to application
administration; a plan controls commercial entitlements. An admin on Free gets
Free entitlements, deliberately, so the paid experience can be tested from an
ordinary account.

Nothing here knows about money. Prices live in PLAN_LABELS for display only, and
even that is informational until a payment processor owns it.
"""

FREE = 'free'
PRO = 'pro'

#: The plans a user may be on. Anything else is rejected at the boundary.
VALID_PLANS = (FREE, PRO)
DEFAULT_PLAN = FREE

#: Display copy. Deliberately separate from entitlements so pricing can change
#: without touching a single rule, and so no rule can accidentally depend on it.
PLAN_LABELS = {
    FREE: {'name': 'Free', 'price': '$0', 'blurb': 'Suitable for trying skedworx.'},
    PRO: {'name': 'Pro', 'price': '$199 CAD/year',
          'blurb': 'Full skedworx functionality.'},
}

#: Use for a limit that is deliberately not capped.
UNLIMITED = None

# ---------------------------------------------------------------------------
# The definitions. Everything the plans differ on lives in this one table, so
# changing what Free includes is an edit here and nowhere else.
#
# `enforced` is separate from the value on purpose. A limit can be defined and
# agreed on paper long before it should start refusing real users -- and until it
# is switched on, the capability stays available rather than becoming an invented
# restriction. See ENFORCEMENT_NOTES.
# ---------------------------------------------------------------------------
PLANS = {
    FREE: {
        'features': {
            'seed_replay': True,
            'run_history': True,
            'export': True,          # see ENFORCEMENT_NOTES: not yet a paywall
            'advanced_constraints': True,
        },
        'limits': {
            'saved_seasons': 1,
            'teams': UNLIMITED,
            'runs_per_month': UNLIMITED,
        },
    },
    PRO: {
        'features': {
            'seed_replay': True,
            'run_history': True,
            'export': True,
            'advanced_constraints': True,
        },
        'limits': {
            'saved_seasons': UNLIMITED,
            'teams': UNLIMITED,
            'runs_per_month': UNLIMITED,
        },
    },
}

#: Which rules actually refuse an action today. Everything absent from this set is
#: defined but dormant: `can()` and `limit()` still answer honestly, and callers
#: that gate on `enforced()` leave the capability alone.
#:
#: Kept deliberately short. A limit belongs here once its number is agreed, not
#: when it is first sketched.
ENFORCED = {
    'saved_seasons',
}

ENFORCEMENT_NOTES = {
    'export': (
        "Defined as a Pro feature in the plan sketch, but left on for Free and not "
        "enforced. Turning it off would mean a Free user can generate a schedule "
        "and never see it, which makes the free tier untestable -- and every "
        "existing account is being migrated to Free, including the one running a "
        "real season. Needs a product decision, not a default."
    ),
    'advanced_constraints': (
        "No agreed definition of which settings are 'advanced'. Splitting the "
        "config form on a guess would be an invented restriction."
    ),
    'runs_per_month': (
        "Not enforceable from the current run history, which keeps only the most "
        "recent 25 entries per account and discards the rest. Counting a month of "
        "runs needs that log to become authoritative first."
    ),
}


def plan_of(user):
    """The plan a user is on, falling back to Free for anything unrecognised.

    This is the single point that will change when billing arrives: the answer
    will come from a subscription record rather than a column, and nothing that
    calls `can()` or `limit()` will notice.
    """
    plan = getattr(user, 'plan', None)
    return plan if plan in VALID_PLANS else DEFAULT_PLAN


def is_valid_plan(plan):
    return plan in VALID_PLANS


def features(user):
    return dict(PLANS[plan_of(user)]['features'])


def limits(user):
    return dict(PLANS[plan_of(user)]['limits'])


def can(user, feature):
    """Whether a user's plan includes a feature.

    Answers what the plan says regardless of whether the rule is enforced yet --
    callers that would refuse an action should check `enforced(feature)` too, so a
    defined-but-dormant rule cannot start blocking people by accident.
    """
    return bool(PLANS[plan_of(user)]['features'].get(feature, False))


def limit(user, name):
    """A numeric ceiling, or None for unlimited."""
    return PLANS[plan_of(user)]['limits'].get(name, UNLIMITED)


def enforced(name):
    """Whether this rule currently refuses anything."""
    return name in ENFORCED


def within_limit(user, name, current_count):
    """(allowed, ceiling) for adding one more of something.

    Returns allowed=True when the limit is unlimited or not yet enforced, so an
    undecided number never turns into a refusal.
    """
    ceiling = limit(user, name)
    if ceiling is UNLIMITED or not enforced(name):
        return True, ceiling
    return current_count < ceiling, ceiling


def upgrade_message(what):
    """The copy shown when a Pro-only action is refused.

    One place, so every refusal reads the same and none of them mention billing
    mechanics that do not exist yet.
    """
    return {
        'error': 'upgrade_required',
        'title': 'This is a skedworx Pro feature',
        'message': what,
        'plan_price': PLAN_LABELS[PRO]['price'],
        'learn_more': '/pricing',
    }


def describe(user):
    """Plan summary for the UI: name, price, and the limits that apply."""
    plan = plan_of(user)
    label = PLAN_LABELS[plan]
    return {
        'plan': plan,
        'name': label['name'],
        'price': label['price'],
        'features': features(user),
        'limits': limits(user),
        'enforced': sorted(ENFORCED),
    }
