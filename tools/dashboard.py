#!/usr/bin/env python3
"""Build a self-contained HTML dashboard from a garak midojo report.

Reads a garak report .jsonl (MidojoProbe run) and writes a single HTML file
with the run data embedded: no network, no external assets, safe to share.

Usage:
    python3 tools/dashboard.py [report.jsonl] [-o out.html]

Without a report path, the newest *.report.jsonl in garak's default runs
directory is used (~/.local/share/garak/garak_runs, or $GARAK_RUNS_DIR).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


def runs_dir() -> Path:
    override = os.getenv("GARAK_RUNS_DIR")
    if override:
        return Path(override)
    data_home = os.getenv("XDG_DATA_HOME") or (Path.home() / ".local" / "share")
    return Path(data_home) / "garak" / "garak_runs"


def latest_report() -> Path:
    reports = sorted(
        runs_dir().glob("*.report.jsonl"), key=lambda p: p.stat().st_mtime
    )
    if not reports:
        raise SystemExit(f"no *.report.jsonl found in {runs_dir()}")
    return reports[-1]


def load_entries(path: Path) -> list[dict]:
    entries = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                entries.append(json.loads(line))
    return entries


def _notes(outputs: list[dict], index: int) -> dict:
    return (outputs[index].get("notes") or {}) if index < len(outputs) else {}


def _timeline(outputs: list[dict], index: int, calls: list[dict]) -> list[dict]:
    """Interleave reasoning blocks with calls, in the order the API returned.

    The generator records ``reasoning_steps`` with ``after_calls``; when only
    the flattened ``reasoning`` string is present, it leads the generation.
    """
    notes = _notes(outputs, index)
    steps = notes.get("reasoning_steps")
    if not steps:
        reasoning = notes.get("reasoning")
        return ([{"kind": "think", "text": reasoning}] if reasoning else []) + [
            {"kind": "call", "call": call} for call in calls
        ]
    timeline = []
    for position in range(len(calls) + 1):
        for step in steps:
            if step.get("after_calls") == position:
                timeline.append({"kind": "think", "text": step.get("text")})
        if position < len(calls):
            timeline.append({"kind": "call", "call": calls[position]})
    return timeline


def build_data(entries: list[dict]) -> dict:
    meta = {}
    attempts = []
    evals = []

    for entry in entries:
        kind = entry.get("entry_type")
        if kind == "init":
            meta["garak_version"] = entry.get("garak_version")
            meta["run_id"] = entry.get("run", {}).get("id") if isinstance(entry.get("run"), dict) else entry.get("run")
            meta["started"] = entry.get("start_time")
        elif kind == "start_run setup":
            # setup entries carry flat keys; older reports nest them
            plugins = entry.get("plugins", {})
            target_type = entry.get("plugins.target_type") or plugins.get("target_type")
            target_name = entry.get("plugins.target_name") or plugins.get("target_name")
            meta["target"] = target_name or target_type
            meta["target_type"] = target_type
        elif kind == "eval":
            evals.append(
                {
                    "detector": entry.get("detector"),
                    "probe": entry.get("probe"),
                    "passed": entry.get("passed"),
                    "failed": entry.get("fails"),
                    "total": entry.get("total_evaluated"),
                }
            )
        elif kind == "attempt" and entry.get("detector_results"):
            # post-detector attempt entries carry the scores; the pre-detector
            # duplicates have an empty detector_results
            notes = entry.get("notes", {})
            midojo = notes.get("midojo", {})

            def body_view(body):
                if body is None:
                    return None
                return {
                    "text": body.get("text"),
                    "structured_content": body.get("structured_content"),
                }

            def event_view(event):
                """One contract ToolCallEvent, as recorded per execution."""
                result = event.get("result") or {}
                return {
                    "tool": event.get("tool"),
                    "args": event.get("arguments"),
                    "injected": event.get("injected"),
                    "outcome": event.get("outcome"),
                    "response_text": result.get("text"),
                    "structured": result.get("structured_content"),
                    "is_error": result.get("is_error"),
                    # the result before the injector rewrote it
                    "upstream": body_view(event.get("original_result")),
                }

            prompt = [
                {"role": t.get("role"), "text": (t.get("content") or {}).get("text")}
                for t in (entry.get("prompt", {}) or {}).get("turns", [])
            ]
            outputs = entry.get("outputs") or []
            scores = (
                entry.get("detector_results", {}).get("midojo.MidojoDetector") or []
            )

            executions = midojo.get("executions") or []
            generations = []
            for index in range(max(len(outputs), len(executions), 1)):
                execution = executions[index] if index < len(executions) else {}
                delivery = execution.get("delivery") or {}
                coverage = execution.get("coverage") or {}
                generations.append(
                    {
                        "answer": outputs[index].get("text")
                        if index < len(outputs)
                        else None,
                        "timeline": _timeline(
                            outputs,
                            index,
                            [event_view(e) for e in execution.get("events") or []],
                        ),
                        "score": scores[index] if index < len(scores) else None,
                        "delivered": delivery.get("delivered"),
                        "coverage_errors": coverage.get("errors") or [],
                    }
                )
            attempts.append(
                {
                    "seq": entry.get("seq"),
                    "case_id": midojo.get("case_id"),
                    "channel": midojo.get("channel"),
                    "goal": entry.get("goal"),
                    "criteria": midojo.get("success"),
                    "unsupported": midojo.get("unsupported"),
                    "prompt": prompt,
                    "generations": generations,
                    "error": notes.get("midojo_error"),
                }
            )

    attempts.sort(key=lambda a: a.get("seq") or 0)
    prompts = [a["prompt"] for a in attempts]
    shared_prompt = prompts[0] if prompts and all(p == prompts[0] for p in prompts) else None
    if shared_prompt:
        for attempt in attempts:
            attempt["prompt"] = []
    hits = sum(
        1 for a in attempts if any(g.get("score") == 1.0 for g in a["generations"])
    )
    return {
        "prompt": shared_prompt,
        "meta": meta,
        "summary": {
            "attacks": len(attempts),
            "hits": hits,
            "asr": round(100.0 * hits / len(attempts), 1) if attempts else 0.0,
            "evals": evals,
        },
        "attempts": attempts,
    }


TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>garak IPI run dashboard</title>
<style>
  :root {
    --bg: #0f1115; --panel: #171a21; --panel2: #1d212b; --line: #2a2f3c;
    --text: #dbe2f0; --dim: #8b93a7; --accent: #7aa2f7;
    --hit: #f7768e; --miss: #9ece6a; --inject: #e0af68; --relay: #73daca;
    --override: #bb9af7; --add: #1d2f24; --del: #33202a; --tool: #8fb8ff;
  }
  * { box-sizing: border-box; }
  body { margin: 0; background: var(--bg); color: var(--text);
         font: 13px/1.5 -apple-system, "Segoe UI", Roboto, sans-serif; }
  .wrap { max-width: 980px; margin: 0 auto; padding: 20px 14px 60px; }
  h1 { font-size: 16px; margin: 0 0 2px; }
  .meta { color: var(--dim); font-size: 12px; margin-bottom: 14px; }
  .stats { display: flex; gap: 10px; margin-bottom: 18px; }
  .stat { background: var(--panel); border: 1px solid var(--line); border-radius: 8px;
          padding: 6px 12px; }
  .stat .n { font-size: 18px; font-weight: 600; line-height: 1.2; }
  .stat .l { color: var(--dim); font-size: 11px; }

  details > summary { list-style: none; cursor: pointer; }
  details > summary::-webkit-details-marker { display: none; }
  .caret { color: var(--dim); font-size: 10px; width: 10px; display: inline-block; }

  details.card { background: var(--panel); border: 1px solid var(--line);
                 border-radius: 10px; margin-bottom: 8px; }
  details.card > summary { display: flex; align-items: center; gap: 8px;
                           padding: 9px 12px; flex-wrap: wrap; border-radius: 10px; }
  details.card > summary:hover { background: var(--panel2); }
  details.card[open] > summary { border-bottom: 1px solid var(--line);
                                 border-radius: 10px 10px 0 0; }
  .id { font-weight: 600; font-family: ui-monospace, Menlo, monospace; }
  .body { padding: 8px 12px 12px; }

  .badge { font-size: 10px; font-weight: 700; letter-spacing: .4px; padding: 2px 7px;
           border-radius: 999px; font-family: ui-monospace, Menlo, monospace; }
  .badge.hit  { background: rgba(247,118,142,.16); color: var(--hit); }
  .badge.miss { background: rgba(158,206,106,.14); color: var(--miss); }
  .badge.err  { background: rgba(224,175,104,.16); color: var(--inject); }
  .badge.n    { background: rgba(122,162,247,.12); color: var(--accent); }

  .crit { font-family: ui-monospace, Menlo, monospace; font-size: 12px;
          color: var(--dim); margin-bottom: 6px; word-break: break-word; }
  .crit b { color: var(--accent); font-weight: 600; }
  .crit .add { color: var(--miss); }

  details.sub { margin: 4px 0; }
  details.sub > summary { color: var(--dim); font-size: 11px; letter-spacing: .6px;
                          text-transform: uppercase; }
  details.sub > summary:hover { color: var(--text); }

  details.gen { border: 1px solid var(--line); border-radius: 8px; margin: 6px 0;
                background: var(--panel2); }
  details.gen > summary { display: flex; align-items: center; gap: 8px;
                          padding: 6px 10px; font-family: ui-monospace, Menlo, monospace;
                          font-size: 12px; }
  details.gen > summary:hover { color: var(--accent); }
  details.gen > .body { padding: 8px 10px; }

  .call { display: grid; grid-template-columns: minmax(0, 310px) minmax(0, 1fr);
          gap: 16px; padding: 0 0 12px; margin-bottom: 12px; min-width: 0;
          border-bottom: 1px solid var(--line); }
  .call:last-child { border-bottom: 0; padding-bottom: 0; margin-bottom: 0; }
  @media (max-width: 780px) { .call { grid-template-columns: minmax(0, 1fr); } }
  .answer { border: 1px solid var(--line); border-radius: 8px; padding: 10px 12px;
            margin-top: 12px; background: var(--panel); }
  .think { color: var(--dim); font-style: italic; border-left: 2px solid var(--line);
           padding-left: 10px; margin: 4px 0 0; }
  details.think-block { border-bottom: 1px solid var(--line); padding-bottom: 10px;
                        margin-bottom: 10px; }
  details.think-block > summary { color: var(--dim); }
  .call-h { display: flex; align-items: baseline; gap: 8px; flex-wrap: wrap;
            min-width: 0; font-family: ui-monospace, Menlo, monospace; font-size: 12px; }
  .tool { font-weight: 600; color: var(--tool); }
  .args { color: var(--dim); font-family: ui-monospace, Menlo, monospace;
          font-size: 11px; margin-top: 2px; word-break: break-word;
          white-space: pre-wrap; }
  .dim { color: var(--dim); }
  .b { font-size: 9px; font-weight: 700; padding: 1px 6px; border-radius: 999px;
       letter-spacing: .5px; text-transform: uppercase; }
  .b.inject   { background: rgba(224,175,104,.18); color: var(--inject); }
  .b.relay    { background: rgba(115,218,202,.14); color: var(--relay); }
  .b.err      { background: rgba(247,118,142,.18); color: var(--hit); }

  .resp { font-family: ui-monospace, Menlo, monospace; font-size: 12px;
          white-space: pre-wrap; word-break: break-word; color: var(--text);
          max-height: 240px; overflow: auto; margin-top: 4px; }
  .diff { font-family: ui-monospace, Menlo, monospace; font-size: 12px;
          max-height: 300px; overflow: auto; margin-top: 4px; }
  .diff .r { padding: 0 6px; white-space: pre-wrap; word-break: break-word; }
  .diff .add { background: var(--add); color: #b9f6ca; }
  .diff .del { background: var(--del); color: #ffc9d4; }
  .diff .ctx { color: var(--dim); }
  .diff .mark { display: inline-block; width: 10px; user-select: none; }
  .diff details > summary { color: var(--dim); font-size: 11px; padding: 0 6px; }
  .diff details > summary:hover { color: var(--accent); }
  .none { color: var(--dim); font-style: italic; }
  footer { color: var(--dim); font-size: 11px; margin-top: 24px; }
</style>
</head>
<body>
<div class="wrap">
  <h1>garak IPI dashboard</h1>
  <div class="meta" id="meta"></div>
  <div class="stats" id="stats"></div>
  <div id="prompt"></div>
  <div id="attempts"></div>
  <footer>self-contained export · no external resources</footer>
</div>
<script>
const DATA = __DATA__;

const esc = s => String(s ?? "").replace(/[&<>"']/g,
  c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const j = o => esc(JSON.stringify(o));
const compact = o => { try { return JSON.stringify(o); } catch { return String(o); } };

const pretty = (text, indent = true) => {
  if (text == null) return null;
  try { return JSON.stringify(JSON.parse(text), null, indent ? 2 : 0); }
  catch { return text; }
};
const asJson = text => {
  if (text == null) return null;
  try { return JSON.stringify(JSON.parse(text), null, 2); }
  catch { return null; }
};

function fmtCriteria(c) {
  if (c == null) return '<span class="none">none</span>';
  if (Array.isArray(c)) return c.map(fmtCriteria).join(" ∧ ");
  const parts = [];
  for (const [k, v] of Object.entries(c)) {
    if (k === "tool_called") {
      const extra = [];
      if (v.args) extra.push(j(v.args));
      if (v.result_field) extra.push(`· ${esc(v.result_field.path)}=${j(v.result_field.equals)}`);
      parts.push(`tool_called <b>${esc(v.name)}</b> ${extra.join(" ")}`);
    }
    else if (k === "output_contains") parts.push(`~${j(v)}`);
    else if (k === "output_contains_all") parts.push(`all(${v.map(j).join(", ")})`);
    else if (k === "output_contains_any") parts.push(`any(${v.map(j).join(", ")})`);
    else if (k === "any_of") parts.push("(" + v.map(fmtCriteria).join(" ∨ ") + ")");
    else if (k === "all_of") parts.push("(" + v.map(fmtCriteria).join(" ∧ ") + ")");
    else if (k === "not") parts.push("¬" + fmtCriteria(v));
    else parts.push(`${esc(k)}(${j(v)})`);
  }
  return parts.join(" ");
}

/* --- diff ------------------------------------------------------------ */
const CONTEXT_KEEP = 2;   /* unchanged/deleted runs longer than this fold */

function lineDiff(before, after) {
  const a = before.split("\n"), b = after.split("\n");
  if (a.length * b.length > 4_000_000) return null;
  const dp = Array.from({ length: a.length + 1 }, () => new Uint32Array(b.length + 1));
  for (let i = a.length - 1; i >= 0; i--)
    for (let k = b.length - 1; k >= 0; k--)
      dp[i][k] = a[i] === b[k] ? dp[i + 1][k + 1] + 1
                               : Math.max(dp[i + 1][k], dp[i][k + 1]);
  const out = [];
  let i = 0, k = 0;
  while (i < a.length && k < b.length) {
    if (a[i] === b[k]) { out.push(["ctx", a[i]]); i++; k++; }
    else if (dp[i + 1][k] >= dp[i][k + 1]) { out.push(["del", a[i]]); i++; }
    else { out.push(["add", b[k]]); k++; }
  }
  while (i < a.length) out.push(["del", a[i++]]);
  while (k < b.length) out.push(["add", b[k++]]);
  return out;
}

function row(kind, line) {
  const mark = { add: "+", del: "-", ctx: " " }[kind];
  return `<div class="r ${kind}"><span class="mark">${mark}</span>${esc(line) || " "}</div>`;
}

function renderDiff(before, after) {
  const rows = lineDiff(before, after);
  if (rows == null) return `<div class="resp">${esc(after)}</div>`;

  // split into unchanged runs and change blocks, so a -/+ pair is never
  // separated and long stretches of context stay out of the way
  const segments = [];
  for (const r of rows) {
    const kind = r[0] === "ctx" ? "ctx" : "change";
    const last = segments[segments.length - 1];
    if (last && last.kind === kind) last.rows.push(r);
    else segments.push({ kind, rows: [r] });
  }

  const parts = segments.map(seg => {
    if (seg.kind === "ctx") {
      return seg.rows.length > CONTEXT_KEEP
        ? `<details><summary>\u22ef ${seg.rows.length} unchanged</summary>${seg.rows.map(r => row(...r)).join("")}</details>`
        : seg.rows.map(r => row(...r)).join("");
    }
    const dels = seg.rows.filter(r => r[0] === "del");
    const adds = seg.rows.filter(r => r[0] === "add");
    if (dels.length >= 4)   // wholesale replacement: keep the intent visible
      return `<details><summary>\u2212 ${dels.length} lines</summary>${dels.map(r => row(...r)).join("")}</details>`
             + adds.map(r => row(...r)).join("");
    return seg.rows.map(r => row(...r)).join("");
  });
  return `<div class="diff">${parts.join("")}</div>`;
}

/* Tampering shown once per event: the channel that actually changed. The
   events carry the pre-rewrite result, so no overlay reconstruction is
   needed. */
function renderTamper(call) {
  const upstreamText = call.upstream?.text;
  const textChanged = upstreamText != null && upstreamText !== call.response_text;
  const upstreamStructured = call.upstream?.structured_content;
  const structuredChanged = upstreamStructured != null
    && JSON.stringify(upstreamStructured) !== JSON.stringify(call.structured);
  if (textChanged) {
    const b = asJson(upstreamText), a = asJson(call.response_text);
    return renderDiff(b ?? pretty(upstreamText, false), a ?? pretty(call.response_text, false));
  }
  if (structuredChanged) {   // tampering visible only in the structured channel
    return renderDiff(JSON.stringify(upstreamStructured, null, 2),
                      JSON.stringify(call.structured, null, 2));
  }
  return null;
}

function renderCall(call) {
  const tamper = call.injected ? renderTamper(call) : null;
  const raw = pretty(call.response_text) ?? "";
  const body = tamper ?? `<div class="resp">${esc(raw)}</div>`;
  const badge = call.injected ? "inject" : "relay";
  const failed = call.outcome === "error" ? ' <span class="b err">error</span>' : "";
  return `<div class="call">
    <div class="call-l">
      <div class="call-h"><span class="tool">${esc(call.tool)}</span>
        <span class="b ${badge}">${badge}</span>${failed}</div>
      <div class="args">${esc(compact(call.args ?? {}))}</div>
    </div>
    <div class="call-r">${body}</div>
  </div>`;
}

function scoreBadge(score) {
  return score === 1.0 ? '<span class="badge hit">HIT</span>'
    : score === 0.0 ? '<span class="badge miss">MISS</span>'
    : '<span class="badge err">?</span>';
}

function renderThinking(text) {
  const chars = text.length > 999 ? `${Math.round(text.length / 1000)}k` : text.length;
  return `<details class="sub think-block"><summary>thinking · ${chars} chars</summary>
    <div class="resp think">${esc(text)}</div></details>`;
}

function renderGeneration(g, i) {
  const calls = g.timeline.filter(t => t.kind === "call").length;
  const body = g.timeline.length
    ? g.timeline.map(t => t.kind === "call"
        ? renderCall(t.call)
        : renderThinking(t.text)).join("")
    : '<div class="none">no calls</div>';
  const answer = g.answer
    ? `<div class="resp answer">${esc(g.answer)}</div>`
    : '<div class="none">no answer</div>';
  const flags = [
    `${calls} call${calls === 1 ? "" : "s"}`,
    g.delivered === false ? "not delivered" : null,
    (g.coverage_errors || []).length ? "incomplete coverage" : null,
  ].filter(Boolean).join(" · ");
  return `<details class="gen">
    <summary><span class="caret">▸</span><span>gen ${i + 1}</span>${scoreBadge(g.score)}
      <span class="dim">${esc(flags)}</span></summary>
    <div class="body">${body}${answer}</div>
  </details>`;
}

function renderAttempt(a) {
  const anyHit = a.generations.some(g => g.score === 1.0);
  const badge = (a.error != null || a.unsupported != null)
    ? '<span class="badge err">ERR</span>'
    : anyHit ? '<span class="badge hit">HIT</span>' : '<span class="badge miss">MISS</span>';
  const flagged = a.generations.filter(g => g.score === 1.0).length;
  const channel = a.channel ? ` · ${esc(a.channel)}` : "";
  const prompt = a.prompt.length
    ? `<details class="sub"><summary>prompt</summary>${a.prompt.map(t =>
        `<div class="resp">${esc(t.role)}: ${esc(t.text)}</div>`).join("")}</details>`
    : "";
  const generations = a.generations.map((g, i) => renderGeneration(g, i)).join("");
  const problem = a.unsupported != null
    ? `<div class="resp">${esc(a.unsupported)}</div>`
    : a.error != null ? `<div class="resp">${esc(a.error)}</div>` : "";
  return `<details class="card">
    <summary title="${esc(a.goal)}"><span class="caret">▸</span>
      <span class="badge n">${(a.seq ?? 0) + 1}</span>${badge}
      <span class="id">${esc(a.case_id)}</span>
      <span class="dim">${flagged}/${a.generations.length}${channel}</span></summary>
    <div class="body">
      <div class="crit">${fmtCriteria(a.criteria)}</div>
      ${prompt}${generations}${problem}
    </div>
  </details>`;
}

function rotateCarets() {
  document.querySelectorAll("details").forEach(d => {
    const c = d.querySelector(":scope > summary .caret");
    if (c) c.textContent = d.open ? "▾" : "▸";
  });
}

function render() {
  const m = DATA.meta;
  const started = (m.started ?? "").replace("T", " ").split(".")[0];
  const title = [m.target_type, m.garak_version && `garak ${m.garak_version}`]
    .filter(Boolean).join(" · ");
  document.getElementById("meta").innerHTML =
    `<span title="${esc(title)}">${esc(m.target ?? "")}${m.target && started ? " · " : ""}${esc(started)}</span>`;
  const s = DATA.summary;
  document.getElementById("stats").innerHTML = `
    <div class="stat"><div class="n">${s.attacks}</div><div class="l">attacks</div></div>
    <div class="stat"><div class="n" style="color:var(--hit)">${s.hits}</div><div class="l">hits</div></div>
    <div class="stat"><div class="n">${s.asr}%</div><div class="l">ASR</div></div>`;
  if (DATA.prompt && DATA.prompt.length) {
    document.getElementById("prompt").innerHTML =
      `<details class="sub"><summary>prompt</summary>${DATA.prompt.map(t =>
        `<div class="resp">${esc(t.role)}: ${esc(t.text)}</div>`).join("")}</details>`;
  }
  document.getElementById("attempts").innerHTML =
    DATA.attempts.map(renderAttempt).join("") || '<div class="none">no attempts found</div>';
  document.addEventListener("toggle", rotateCarets, true);
  rotateCarets();
}
render();
</script>
</body>
</html>
"""


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "report",
        type=Path,
        nargs="?",
        help="garak report .jsonl (default: newest in the garak runs directory)",
    )
    parser.add_argument("-o", "--output", type=Path, help="output .html (default: <report>.dashboard.html)")
    args = parser.parse_args()

    report = args.report or latest_report()
    if not report.exists():
        raise SystemExit(f"report not found: {report}")

    entries = load_entries(report)
    data = build_data(entries)

    # keep the embedded JSON out of any </script> hazard
    payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    html = TEMPLATE.replace("__DATA__", payload)

    out = args.output or report.with_suffix(".dashboard.html")
    out.write_text(html, encoding="utf-8")
    n = data["summary"]["attacks"]
    print(f"{out} ({n} attempts, {data['summary']['hits']} hits)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
