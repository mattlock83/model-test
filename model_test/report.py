import html
import json


def write_report(directory, report):
    payload = json.dumps(report, indent=2, ensure_ascii=False)
    (directory / "report.json").write_text(payload + "\n")

    def escape(value):
        return html.escape(str(value))

    coverage = report["coverage"]
    rows = "".join(
        f"<tr><td>{escape(name)}</td><td>{value.get('verified', value.get('completed'))}"
        f" / {value['total']}</td></tr>"
        for name, value in coverage.items()
    )
    attempts = "".join(
        f"<details><summary>{escape(case['status'])} — {escape(case['source'])}</summary>"
        f"<pre>{escape(json.dumps(case, indent=2, ensure_ascii=False))}</pre></details>"
        for case in report["cases"]
    )
    failure = ""
    checkpoints = "".join(
        f"<details><summary>{escape(step.get('status', 'INCOMPLETE'))} — {escape(step['name'])}</summary>"
        f"<pre>{escape(json.dumps(step, indent=2, ensure_ascii=False))}</pre></details>"
        for step in report["steps"]
    )
    if report.get("counterexample"):
        failure = (
            "<h2>Counterexample</h2><pre>"
            + escape(json.dumps(report["counterexample"]["input"], indent=2))
            + '</pre><a href="replay.json">Replay input</a>'
        )
    (directory / "report.html").write_text(f"""<!doctype html><html lang="en"><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Model test — {escape(report["status"])}</title>
<style>body{{font:16px/1.6 system-ui;max-width:1000px;
margin:40px auto;padding:0 24px;color:#193b39;background:#f4f6f1}}
h1{{font-size:40px}}
section,details{{background:white;border:1px solid #d8e0d7;border-radius:8px;
padding:18px;margin:14px 0}}
pre{{white-space:pre-wrap;overflow-wrap:anywhere;font-size:12px}}
td{{padding:8px 30px 8px 0}}a{{color:#1a655c}}
summary{{cursor:pointer}}.status{{font-size:24px;font-weight:700}}</style>
<h1>{escape(report["model"])}</h1><p class="status">{escape(report["status"])}</p>
<p>{escape(report["url"])}</p><p>{escape(report.get("error", ""))}</p>
<section><h2>Selected scope and limits</h2>
<pre>{escape(json.dumps(report.get("limits", {}), indent=2))}</pre>
<p>{"Input campaigns were disabled; this result covers graph journeys only."
if report.get("scope", {}).get("input_mode") == "none" else "PASS applies to the selected testing scope."}</p>
<pre>{escape(json.dumps(report.get("exploration", {}), indent=2))}</pre></section>
<section><h2>Verified coverage</h2><table>{rows}</table>
<p>Edges count only after the destination passes independent verification.
Property resets do not earn graph coverage.
{"This run replays one input; full coverage was not attempted." if report["replay"] else ""}</p></section>
<section><h2>Jev usage</h2><pre>{escape(json.dumps(report["usage"], indent=2))}</pre>
<p>Navigation and read-only verification use separate questions. DONE is not a pass verdict.
The semantic judgments remain probabilistic; uncertain results are inconclusive.</p></section>
<section><h2>Journey checkpoints</h2>{checkpoints or "No checkpoints executed."}</section>
<section>{failure}<h2>Input attempts</h2>{attempts or "No property cases executed."}</section>
<details><summary>Full execution evidence</summary><pre>{escape(payload)}</pre></details>
<p><a href="report.json">Report JSON</a> · <a href="model.json">Exact business model</a></p></html>""")
