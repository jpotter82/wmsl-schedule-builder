"""WSGI entry point for Phusion Passenger (cPanel "Setup Python App") and similar.

Passenger looks for this file at the application root and expects a WSGI callable
named `application`.

Synchronous runs are forced here. Passenger starts several worker processes and
recycles idle ones, while this app keeps run state in memory in the process that
started the job. On a background thread the status poll can land on a different
process -- one that never ran the scheduler -- and the UI appears to hang. Running
the work inside the request avoids that entirely, and costs nothing: a full
15-attempt run finishes in under two seconds.
"""
import os
import sys

# Passenger does not always start with the app root on sys.path.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

os.environ.setdefault('WMSL_SYNC_RUNS', '1')

from app import app as application  # noqa: E402
