"""Write JSON, JUnit and a portable viewer with local evidence loaded on demand."""

import html
import json
import re
from pathlib import Path

from .junit import write_junit


def scope_note(report):
    if report.get("replay"):
        return (
            "This result covers one replay input; "
            "graph exploration and other property phases are outside this run."
        )
    scope = report.get("scope", {})
    if scope.get("input_mode") == "none":
        return "Input campaigns were disabled; this result covers graph journeys only."
    selection = scope.get("property_selection")
    if selection:
        note = (
            f"Property scope: {selection['selected_phases']} of {selection['available_phases']} "
            f"phases selected in model order, with up to {selection['selected_examples']} planned inputs. "
            "Total input attempts, including shrinking and reproduction, "
            f"are capped at {selection['input_limit']}."
        )
        if selection["selected_examples"] < selection["available_examples"]:
            note += " This is a limited input scope; unselected work is outside this run."
        return note
    return "JUnit counts generated phases; inputs and shrinking attempts appear within each case."


def write_report(directory, report):
    payload = json.dumps(report, indent=2, ensure_ascii=False)
    (directory / "report.json").write_text(payload + "\n")
    write_viewer(directory, report)
    write_junit(directory, report)


def script_json(value):
    # Escape script delimiters independently of HTML text, including </script> in browser evidence.
    return (
        json.dumps(value, ensure_ascii=True, separators=(",", ":"))
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
    )


def write_viewer(directory, report):
    """Keep large decision transcripts out of the initial HTML and DOM.

    Local scripts, rather than fetch(), also work when opening report.html via file://.
    The complete authoritative report.json and JUnit evidence are unchanged.
    """
    viewer = {key: value for key, value in report.items() if key != "decisions"}
    decisions = report.get("decisions", [])
    size = 128
    files = []
    if decisions:
        evidence = directory / "evidence"
        evidence.mkdir(exist_ok=True)
        for start in range(0, len(decisions), size):
            index = start // size
            path = evidence / f"decisions-{index:05d}.js"
            path.write_text(
                f"window.testwalkerDecisionChunks[{index}] = "
                f"{script_json(decisions[start : start + size])};\n"
            )
            files.append(path.relative_to(directory).as_posix())
    viewer["decision_chunks"] = {"size": size, "files": files}
    values = {
        "VIEWER_STYLE": Path(__file__).with_name("viewer.css").read_text(),
        "VIEWER_SCRIPT": Path(__file__).with_name("viewer.js").read_text(),
        "TITLE": html.escape(report["model"]),
        "STATUS": html.escape(report["status"]),
        "REPORT_JSON": script_json(viewer),
        "SCOPE_NOTE": html.escape(scope_note(report)),
    }
    template = Path(__file__).with_name("viewer.html").read_text()
    rendered = re.sub(
        r"__(TITLE|STATUS|REPORT_JSON|SCOPE_NOTE|VIEWER_STYLE|VIEWER_SCRIPT)__",
        lambda match: values[match[1]],
        template,
    )
    (directory / "report.html").write_text(rendered)
