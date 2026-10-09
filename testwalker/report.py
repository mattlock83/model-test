"""Write portable JSON, JUnit and an interactive viewer with no external assets."""

import html
import json
import re
from pathlib import Path

from .junit import write_junit


def write_report(directory, report):
    payload = json.dumps(report, indent=2, ensure_ascii=False)
    (directory / "report.json").write_text(payload + "\n")
    # Escape script delimiters independently of HTML text, including </script> in browser evidence.
    embedded = (
        json.dumps(report, ensure_ascii=True)
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
    )
    values = {
        "VIEWER_STYLE": Path(__file__).with_name("viewer.css").read_text(),
        "VIEWER_SCRIPT": Path(__file__).with_name("viewer.js").read_text(),
        "TITLE": html.escape(report["model"]),
        "STATUS": html.escape(report["status"]),
        "REPORT_JSON": embedded,
        "ESCAPED_EVIDENCE": html.escape(payload),
        "SCOPE_NOTE": (
            "Input campaigns were disabled; this result covers graph journeys only."
            if report.get("scope", {}).get("input_mode") == "none"
            else "JUnit counts generated phases; inputs and shrinking attempts appear within each case."
        ),
    }
    template = Path(__file__).with_name("viewer.html").read_text()
    rendered = re.sub(
        r"__(TITLE|STATUS|REPORT_JSON|ESCAPED_EVIDENCE|SCOPE_NOTE|VIEWER_STYLE|VIEWER_SCRIPT)__",
        lambda match: values[match[1]],
        template,
    )
    (directory / "report.html").write_text(rendered)
    write_junit(directory, report)
