"""Portable JUnit XML with planned skips and self-contained triage evidence."""

import json
import re
from datetime import datetime
from xml.etree import ElementTree as ET


def xml_text(value):
    # Browser text and generated inputs can contain characters XML 1.0 forbids.
    return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff\ufffe\uffff]", "\ufffd", str(value))


def evidence(report, test):
    details = {
        "test": test,
        "model_hash": report["model_hash"],
        "seed": report["seed"],
        "url": report["url"],
        "exploration": report["exploration"],
        "limits": report["limits"],
        "artifacts": {name: name for name in ("report.html", "report.json", "model.json", "plan.json")},
    }
    if report.get("counterexample"):
        details["artifacts"]["replay.json"] = "replay.json"
    if test["kind"] == "run":
        details.update(
            stop=report.get("stop"),
            failure=report.get("failure"),
            coverage=report["coverage"],
            planning=report["planning"],
            global_audit=report.get("global_audit"),
            hooks=report["hooks"],
            cleanup_errors=report.get("cleanup_errors", []),
        )
    else:
        details["checkpoints"] = [report["steps"][i] for i in test.get("step_indices", [])]
        details["attempts"] = [report["cases"][i] for i in test.get("attempt_indices", [])]
    if test["status"] != "PASS":
        details["stop"] = report.get("stop")
        details["failure"] = report.get("failure") if test["status"] != "SKIPPED" else None
        details["blocked_by"] = [
            {"id": item["id"], "status": item["status"], "reason": item.get("reason")}
            for item in report["tests"]
            if item["status"] in {"FAIL", "INCONCLUSIVE"}
        ]
        for destination, source, range_key in (
            ("actions", "trace", "trace_range"),
            ("decisions", "decisions", "decision_range"),
        ):
            bounds = test.get(range_key, [0, 0])
            details[destination] = report.get(source, [])[bounds[0] : bounds[1]]
        details["hook_errors"] = [event for event in report["hooks"]["events"] if event.get("error")]
    return xml_text(json.dumps(details, indent=2, ensure_ascii=False))


def write_junit(directory, report):
    root = ET.Element("testsuites", name="testwalker")
    run_test = {
        "id": "run/outcome",
        "kind": "run",
        "status": report["status"],
        "reason": report.get("error"),
        "description": "Coverage, global requirements and run lifecycle",
    }
    totals = {"tests": 0, "failures": 0, "errors": 0, "skipped": 0}
    for kind in ("graph", "property", "run"):
        tests = [test for test in report["tests"] if test["kind"] == kind] if kind != "run" else [run_test]
        if not tests:
            continue
        counts = {
            "tests": len(tests),
            "failures": sum(test["status"] == "FAIL" for test in tests),
            "errors": sum(test["status"] == "INCONCLUSIVE" for test in tests),
            "skipped": sum(test["status"] == "SKIPPED" for test in tests),
        }
        suite = ET.SubElement(
            root,
            "testsuite",
            name=f"testwalker.{kind}",
            timestamp=report["started"],
            time=f"{sum(test.get('duration', 0) for test in tests):.6f}",
            **{key: str(value) for key, value in counts.items()},
        )
        props = ET.SubElement(suite, "properties")
        for name, value in {
            "model": report["model"],
            "model_hash": report["model_hash"],
            "seed": report["seed"],
            "report": "report.html",
            "inventory": "plan.json",
            "planning_complete": report["planning"]["complete"],
        }.items():
            ET.SubElement(props, "property", name=name, value=xml_text(value))
        for test in tests:
            case = ET.SubElement(
                suite,
                "testcase",
                name=xml_text(test.get("name", test["id"])),
                classname=f"testwalker.{kind}",
                time=f"{test.get('duration', 0):.6f}",
            )
            outcome = {"FAIL": "failure", "INCONCLUSIVE": "error", "SKIPPED": "skipped"}.get(test["status"])
            if outcome:
                attributes = {
                    "message": xml_text(test.get("reason") or report.get("error") or test["status"])
                }
                if outcome != "skipped":
                    attributes["type"] = "Defect" if outcome == "failure" else "Inconclusive"
                ET.SubElement(case, outcome, **attributes).text = evidence(report, test)
            else:
                ET.SubElement(case, "system-out").text = evidence(report, test)
        for key, value in counts.items():
            totals[key] += value
    root.attrib.update({key: str(value) for key, value in totals.items()})
    elapsed = datetime.fromisoformat(report["finished"]) - datetime.fromisoformat(report["started"])
    root.set("time", f"{elapsed.total_seconds():.6f}")
    ET.indent(root)
    ET.ElementTree(root).write(directory / "junit.xml", encoding="utf-8", xml_declaration=True)
