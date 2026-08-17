"""Outbound email.

Only used for password resets, so this is deliberately a thin wrapper over smtplib
rather than a dependency. Configuration comes from the environment:

    SKEDDY_SMTP_HOST      smtp.wmsl.ca          (required to send)
    SKEDDY_SMTP_PORT      587                   (default 587, or 465 with SSL)
    SKEDDY_SMTP_USER      skeddy@wmsl.ca
    SKEDDY_SMTP_PASSWORD  ...
    SKEDDY_SMTP_FROM      "Skeddy <skeddy@wmsl.ca>"   (required to send)
    SKEDDY_SMTP_SSL       1 to connect with SSL instead of STARTTLS

Nothing here belongs in git: on shared hosting set these in dispatch.cgi, which is
untracked for exactly this reason.

When SMTP is not configured the message is written to stderr instead of being sent,
so local development works without a mail server. It goes to stderr and never to the
browser — putting a reset link in an HTTP response would let anyone take over any
account by asking for it.
"""
import smtplib
import ssl
import sys
from email.message import EmailMessage
from email.utils import formatdate, make_msgid

from auth import env


def smtp_configured():
    """True when there is enough configuration to actually send."""
    return bool(env('SMTP_HOST').strip() and env('SMTP_FROM').strip())


def _port(default):
    raw = env('SMTP_PORT').strip()
    try:
        return int(raw) if raw else default
    except ValueError:
        return default


def send(to, subject, body):
    """Send a plain-text message. Returns True when it was handed to the server.

    Never raises: a mail failure must not turn into a 500 on the reset page, because
    the page is deliberately identical whether or not the address has an account, and
    an error there would give that away.
    """
    if not smtp_configured():
        sys.stderr.write(
            "skeddy: SMTP is not configured, so this message was NOT sent.\n"
            f"skeddy:   To:      {to}\n"
            f"skeddy:   Subject: {subject}\n"
            f"{body}\n")
        return False

    msg = EmailMessage()
    msg['To'] = to
    msg['From'] = env('SMTP_FROM').strip()
    msg['Subject'] = subject
    msg['Date'] = formatdate(localtime=True)
    msg['Message-ID'] = make_msgid()
    msg.set_content(body)

    host = env('SMTP_HOST').strip()
    user = env('SMTP_USER').strip()
    password = env('SMTP_PASSWORD')
    use_ssl = env('SMTP_SSL').strip().lower() in ('1', 'true', 'yes', 'on')

    try:
        if use_ssl:
            server = smtplib.SMTP_SSL(host, _port(465),
                                      context=ssl.create_default_context(), timeout=20)
        else:
            server = smtplib.SMTP(host, _port(587), timeout=20)
        with server:
            if not use_ssl:
                server.starttls(context=ssl.create_default_context())
            if user:
                server.login(user, password)
            server.send_message(msg)
        return True
    except Exception as exc:                      # noqa: BLE001 - see docstring
        sys.stderr.write(f"skeddy: could not send mail to {to}: {exc!r}\n")
        return False


def send_password_reset(to, reset_url, ttl_minutes):
    """Send the reset link. Wording assumes the recipient may not have asked."""
    return send(
        to,
        "Reset your Skeddy password",
        "Someone asked to reset the password for your Skeddy account.\n\n"
        f"Open this link to choose a new one:\n\n{reset_url}\n\n"
        f"The link works once and expires in {ttl_minutes} minutes.\n\n"
        "If this wasn't you, you can ignore this email — your password has not\n"
        "changed and the link cannot be used without opening it.\n")
