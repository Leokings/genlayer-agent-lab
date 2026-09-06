"""Portable report exports; all report text is treated as untrusted input."""

from __future__ import annotations

import html
import json
from typing import Any
from xml.etree import ElementTree as ET

GRADE_NAMES = ("decision", "behavior", "outcome", "completion")


def _text(value: Any) -> str:
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, indent=2, default=str)
    return str(value if value is not None else "")


def to_html(report: dict[str, Any]) -> str:
    """Render a self-contained, script-free HTML document."""
    escape = lambda value: html.escape(_text(value), quote=True)  # noqa: E731
    grades = report.get("grades", {})
    grade_rows = "".join(
        "<tr><th>" + escape(name.title()) + "</th><td>"
        + escape(grades.get(name, {}).get("status", "inconclusive")) + "</td><td>"
        + escape(grades.get(name, {}).get("detail", "No grade available")) + "</td></tr>"
        for name in GRADE_NAMES
    )
    findings = "".join(f"<li><pre>{escape(item)}</pre></li>"
                       for item in report.get("findings", [])) or "<li>No findings recorded.</li>"
    events = "".join(
        f"<tr><td>{index}</td><td><pre>{escape(event)}</pre></td></tr>"
        for index, event in enumerate(report.get("events", []), start=1)
    ) or '<tr><td colspan="2">No events recorded.</td></tr>'
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>GenLayer Agent Lab — {escape(report.get('run_id', 'report'))}</title>
<style>
body{{font:16px/1.55 system-ui,sans-serif;color:#1c2635;background:#f3f5f8;margin:0}}
main{{max-width:1100px;margin:32px auto;padding:28px;background:white;border-radius:12px}}
h1{{margin-top:0}}table{{border-collapse:collapse;width:100%;margin:16px 0}}
th,td{{padding:12px;text-align:left;border-bottom:1px solid #d9e0ea;vertical-align:top}}
pre{{white-space:pre-wrap;overflow-wrap:anywhere;margin:0;font-size:13px}}
.notice{{padding:14px;background:#eef3fa;border-left:4px solid #366ca7}}
dl{{display:grid;grid-template-columns:140px 1fr;gap:8px}}dd{{margin:0;overflow-wrap:anywhere}}
</style></head><body><main>
<h1>GenLayer Agent Lab</h1>
<dl><dt>Run</dt><dd>{escape(report.get('run_id'))}</dd>
<dt>Scenario</dt><dd>{escape(report.get('scenario'))}</dd>
<dt>Agent</dt><dd>{escape(report.get('agent'))}</dd>
<dt>Status</dt><dd>{escape(report.get('status'))}</dd>
<dt>Verdict</dt><dd>{escape(report.get('verdict', 'inconclusive'))}</dd></dl>
<p class="notice">Decision lifecycle events in the lightweight profile are scripted.
The manifest distinguishes fixture responses from actual GLSim contract execution.
This local diagnostic report is not an independent safety certification.</p>
<h2>Evaluation</h2><table><thead><tr><th>Dimension</th><th>Status</th><th>Evidence</th>
</tr></thead><tbody>{grade_rows}</tbody></table>
<h2>Findings</h2><ul>{findings}</ul>
<h2>Event timeline</h2><table><thead><tr><th>#</th><th>Recorded event</th></tr></thead>
<tbody>{events}</tbody></table>
<h2>Final world state</h2><pre>{escape(report.get('world', {}))}</pre>
<h2>Run manifest</h2><pre>{escape(report.get('manifest', {}))}</pre>
</main></body></html>"""


def _xml_text(value: Any) -> str:
    # XML 1.0 cannot represent arbitrary control characters from agent/model output.
    return "".join(char if char in "\t\n\r" or "\x20" <= char <= "\ud7ff"
                   or "\ue000" <= char <= "\ufffd" or "\U00010000" <= char <= "\U0010ffff"
                   else "\ufffd" for char in _text(value))


def to_junit(report: dict[str, Any]) -> str:
    """Use failures for failed grades and errors for inconclusive/unfinished grades."""
    grades = report.get("grades", {})
    failures = sum(grades.get(name, {}).get("status") == "fail" for name in GRADE_NAMES)
    errors = sum(grades.get(name, {}).get("status") not in {"pass", "fail"}
                 for name in GRADE_NAMES)
    suite = ET.Element("testsuite", {
        "name": "GenLayer Agent Lab", "id": _xml_text(report.get("run_id", "")),
        "tests": str(len(GRADE_NAMES)), "failures": str(failures), "errors": str(errors),
        "skipped": "0",
    })
    properties = ET.SubElement(suite, "properties")
    for name, value in {
        "scenario": report.get("scenario"), "agent": report.get("agent"),
        "status": report.get("status"), "verdict": report.get("verdict"),
        "manifest": report.get("manifest", {}),
        "lifecycle": "Lightweight decision lifecycle events are scripted; inspect manifest.",
    }.items():
        ET.SubElement(properties, "property", {"name": name, "value": _xml_text(value)})
    for name in GRADE_NAMES:
        grade = grades.get(name, {})
        case = ET.SubElement(suite, "testcase", {
            "classname": "genlayer_agent_lab", "name": name,
        })
        status = grade.get("status", "inconclusive")
        detail = _xml_text(grade.get("detail", "No grade available"))
        if status != "pass":
            element = ET.SubElement(case, "failure" if status == "fail" else "error", {
                "message": detail, "type": "assertion" if status == "fail" else "inconclusive",
            })
            element.text = detail
        ET.SubElement(case, "system-out").text = detail
    return ET.tostring(suite, encoding="unicode", xml_declaration=True)


def export_report(report: dict[str, Any], format: str = "json") -> str:
    if format == "html":
        return to_html(report)
    if format == "junit":
        return to_junit(report)
    if format != "json":
        raise ValueError("Unsupported report format")
    return json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n"
