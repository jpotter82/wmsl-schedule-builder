#!/usr/bin/env python3
"""CGI entry point, for shared hosting without Passenger ("Setup Python App").

Every request starts a fresh Python process, so nothing survives in memory between
them. That is fine here:

  * SKEDDY_SYNC_RUNS forces the scheduler to run inside the request rather than on a
    background thread, which would be killed the moment the process exits.
  * Run state is mirrored to .run_state.json by app.py, so /api/status and
    /api/results can read the result of a run performed by an earlier process.

Startup cost is roughly 0.4s to import the scheduler, on top of a ~1.6s worst-case
run, so even the slowest request stays well inside normal CGI limits.

Setup:
  chmod 755 dispatch.cgi
  # if 'python3' is not on PATH for CGI, replace the shebang above with an
  # absolute interpreter path, e.g. #!/home/USERNAME/virtualenv/wmsl/3.9/bin/python
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

os.environ.setdefault('SKEDDY_SYNC_RUNS', '1')

# Recommended on shared hosting: keep accounts, password hashes and the session
# key outside the web root, so they cannot be fetched over HTTP even if .htaccess
# is misconfigured. Create the directory (chmod 700) and uncomment.
# os.environ.setdefault('SKEDDY_DATA_DIR', '/home/USERNAME/skeddy-data')

# Surface tracebacks in the browser instead of a bare 500 while setting things up.
# Comment out once it is working.
import cgitb  # noqa: E402
cgitb.enable()

from wsgiref.handlers import CGIHandler  # noqa: E402
from app import app  # noqa: E402


class _ScriptNameFix(object):
    """Strip the CGI script name from PATH_INFO.

    With the .htaccess rewrite the request arrives as /dispatch.cgi/api/status while
    Flask should route /api/status. Clearing SCRIPT_NAME keeps url_for() emitting
    plain paths so the page's own fetch() calls resolve correctly.
    """

    def __init__(self, wsgi_app):
        self.wsgi_app = wsgi_app

    def __call__(self, environ, start_response):
        environ['SCRIPT_NAME'] = ''
        return self.wsgi_app(environ, start_response)


CGIHandler().run(_ScriptNameFix(app))
