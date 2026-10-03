"""Operational routes: liveness probe and crash-report bookkeeping.

Split from dashboard/routes/system.py (roadmap 3.5) -- pure code
move, zero behavior change.
"""
from flask import Blueprint, jsonify

bp = Blueprint("system_ops", __name__)


@bp.route("/healthz")
def healthz():
    """Identity probe (Roadmap 1.11): proves this port serves Focus Core.

    The launcher asks for this before attaching to a busy port, so a
    foreign app on 5000 is never mistaken for the dashboard. It
    reveals nothing but the app name -- no data, no auth needed.
    """
    return jsonify({"app": "focus-core", "status": "ok"})


@bp.route("/crash-report/handled", methods=["POST"])
def crash_report_handled():
    """Roadmap 2.5: any card action (copy / email / dismiss) retires
    the pending crash-report offer -- it is never shown twice for the
    same incident. The app sends nothing itself; this only records
    that the user has answered the offer."""
    from focuscore import crashreport
    crashreport.mark_handled()
    return ("", 204)
