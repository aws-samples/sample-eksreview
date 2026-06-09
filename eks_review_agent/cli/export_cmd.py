"""/export handler — JIRA CSV export from a saved report."""

from __future__ import annotations

from eks_review_agent.reports.export import export_report_to_jira_csv


def handle_export(user_input: str, agent) -> None:
    """Export the last (or specified) report as JIRA-importable CSV.

    Resolution order for which report to export:
        1. Explicit path passed on the command line.
        2. agent.state["last_report_path"] from the active session.
        3. List the most recent reports and prompt for one.
    """
    last_cluster = agent.state.get("last_reviewed_cluster")
    last_report = agent.state.get("last_report_path")
    export_arg = user_input[len("/export"):].strip()

    if export_arg:
        report_path = export_arg
        cluster = last_cluster or "unknown"
    elif last_report:
        report_path = last_report
        cluster = last_cluster or "unknown"
    else:
        # No report in this session — list available reports and bail
        from eks_review_agent.config import REPORTS_DIR
        reports = sorted(REPORTS_DIR.glob("*-assessment-*.md"), reverse=True)
        if not reports:
            print("  No reports found. Run a cluster review first.")
            return

        print(f"\n  Available reports ({len(reports)}):\n")
        for i, rp in enumerate(reports[:10], 1):
            print(f"    {i}. {rp.name}")
        print("\n  Usage: /export           (exports last report from this session)")
        print("         /export <path>    (exports a specific report file)")
        return

    result_msg = export_report_to_jira_csv(report_path, cluster)
    print(f"  {result_msg}")
