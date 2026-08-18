"""Check the password-reset mail configuration, from the host.

    python3 check_smtp.py                 # report settings, connect, authenticate
    python3 check_smtp.py you@example.com # ...and send a real test message

Exists because the reset form is deliberately silent: it shows the same page whether
or not an address has an account, and mail failures are logged rather than displayed,
so a misconfigured mailbox looks exactly like a working one. This says plainly what is
wrong.

Reads the same settings the app does, including ~/skedworx-secrets.env when
dispatch.cgi loads it, so run it the same way you run the app. The password is never
printed — only whether one is set and how long it is.
"""
import os
import smtplib
import ssl
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def load_secrets_file():
    """Mirror the loader in dispatch.cgi, so this sees the same settings."""
    path = os.path.join(os.path.expanduser('~'), 'skedworx-secrets.env')
    if not os.path.exists(path):
        return None
    mode = oct(os.stat(path).st_mode & 0o777)
    with open(path, encoding='utf-8') as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            k, _, v = line.partition('=')
            os.environ.setdefault(k.strip(), v.strip())
    return path, mode


def main():
    found = load_secrets_file()
    if found:
        path, mode = found
        print(f"  secrets file : {path}  (mode {mode})")
        if mode not in ('0o600', '0o400'):
            print("                 WARNING: readable by others. chmod 600 it.")
    else:
        print("  secrets file : none (~/skedworx-secrets.env), using dispatch.cgi or shell env")

    import auth
    import mailer

    settings = {k: auth.env(k) for k in
                ('SMTP_HOST', 'SMTP_PORT', 'SMTP_USER', 'SMTP_FROM', 'SMTP_SSL', 'BASE_URL')}
    password = auth.env('SMTP_PASSWORD')

    print()
    for key, value in settings.items():
        print(f"  {key:<11}: {value or '(not set)'}")
    print(f"  {'SMTP_PASSWORD':<11}: {'set, ' + str(len(password)) + ' chars' if password else '(not set)'}")
    print()

    problems = []
    if not settings['SMTP_HOST']:
        problems.append("SMTP_HOST is not set - nothing will ever be sent.")
    if not settings['SMTP_FROM']:
        problems.append("SMTP_FROM is not set - nothing will ever be sent.")
    if not settings['BASE_URL']:
        problems.append("BASE_URL is not set - reset links will be built from the "
                        "Host header, which the requester controls.")
    elif not settings['BASE_URL'].startswith('https://'):
        problems.append(f"BASE_URL is {settings['BASE_URL']!r} - reset links should be https.")
    if settings['SMTP_USER'] and not password:
        problems.append("SMTP_USER is set but SMTP_PASSWORD is not - login will fail.")

    if not mailer.smtp_configured():
        print("  RESULT: not configured. Reset links go to the error log only,")
        print("          so no account can be recovered.")
        for p in problems:
            print(f"    - {p}")
        return 1

    for p in problems:
        print(f"  WARNING: {p}")

    host = settings['SMTP_HOST']
    use_ssl = settings['SMTP_SSL'].strip().lower() in ('1', 'true', 'yes', 'on')
    port = int(settings['SMTP_PORT'] or (465 if use_ssl else 587))

    print(f"  connecting to {host}:{port} ({'SSL' if use_ssl else 'STARTTLS'})...")
    try:
        if use_ssl:
            server = smtplib.SMTP_SSL(host, port, context=ssl.create_default_context(), timeout=20)
        else:
            server = smtplib.SMTP(host, port, timeout=20)
        with server:
            if not use_ssl:
                server.starttls(context=ssl.create_default_context())
            print("  connected and secured.")
            if settings['SMTP_USER']:
                server.login(settings['SMTP_USER'], password)
                print("  authenticated.")
    except smtplib.SMTPAuthenticationError as exc:
        print(f"  FAILED to authenticate: {exc}")
        print("    The mailbox password is wrong, or the host wants the full address as")
        print("    the username. cPanel mailboxes usually do.")
        return 1
    except ssl.SSLError as exc:
        print(f"  FAILED TLS: {exc}")
        print("    Port 465 needs SKEDWORX_SMTP_SSL=1; port 587 uses STARTTLS without it.")
        return 1
    except OSError as exc:
        print(f"  FAILED to connect: {exc}")
        print("    Check the hostname, and whether outbound SMTP is blocked. Shared hosts")
        print("    often want 'localhost' rather than mail.<domain> from their own servers.")
        return 1

    if len(sys.argv) > 1:
        to = sys.argv[1]
        print(f"  sending a test message to {to}...")
        ok = mailer.send(to, "skedworx test message",
                         "This is a test from check_smtp.py.\n\n"
                         "If you received it, password reset email is working.\n")
        print("  sent." if ok else "  send failed - see the error above.")
        if not ok:
            return 1
        print("  Check the inbox, and the spam folder. If it landed in spam, add SPF")
        print("  and DKIM records for the domain in cPanel.")
    else:
        print("  Pass an address to send a real test:  python3 check_smtp.py you@example.com")

    return 0


if __name__ == '__main__':
    sys.exit(main())
