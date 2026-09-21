"""Runs one build request (chongdae/request@1) in a fresh host session and writes the response the runner reads.

  python build_worker.py --request FILE --response FILE [--host claude|codex] [--model M] [--effort E] [--max-turns N]

The session gets the `build` skill's text plus the request, may read/edit/write in the target and run its checks, and must
answer with one JSON object. The worker saves it; chongdae decides with its own checks afterwards. A missing or malformed
answer is saved as `status: failed` — never as done.
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
SKILL = HERE / "skills" / "build" / "SKILL.md"
SCHEMA = {"type": "object", "additionalProperties": False, "required": ["status", "summary", "verified", "non-claims", "decisions"],
          "properties": {"status": {"enum": ["done", "blocked", "failed"]}, "summary": {"type": "string"},
                         # Every choice the contract did not make. Enforced by the runner, not by asking the model to stop:
                         # a non-empty list means the slice is not done until a human accepts or rejects each choice.
                         "decisions": {"type": "array", "items": {"type": "object", "additionalProperties": False, "required": ["what", "chosen", "alternatives"],
                                                                  "properties": {"what": {"type": "string"}, "chosen": {"type": "string"},
                                                                                 "alternatives": {"type": "array", "items": {"type": "string"}}}}},
                         "verified": {"type": "array", "items": {"type": "object", "additionalProperties": False, "required": ["check", "exit"],
                                                                 "properties": {"check": {"type": "string"}, "exit": {"type": "integer"}}}},
                         "non-claims": {"type": "array", "items": {"type": "string"}}}}


def skill_text():
    text = SKILL.read_text(encoding="utf-8")
    return text.split("---", 2)[2].strip() if text.startswith("---") else text


def claude(prompt, args, target):
    tools = "Read,Edit,Write,Glob,Grep,Bash"
    cmd = ["claude", "-p", "--output-format", "stream-json", "--verbose", "--no-session-persistence", "--setting-sources", "",
           "--strict-mcp-config", "--tools", tools, "--allowedTools", tools, "--max-turns", str(args.max_turns),
           "--json-schema", json.dumps(SCHEMA), "--add-dir", target, "--permission-mode", "bypassPermissions"]
    if args.model:
        cmd += ["--model", args.model]
    if args.effort:
        cmd += ["--effort", args.effort]
    done = subprocess.run(cmd, input=prompt.encode("utf-8"), capture_output=True, cwd=target,
                          env=dict(os.environ, CLAUDE_CODE_DISABLE_AUTO_MEMORY="1", AGENT_WORKER="1"))
    stdout = done.stdout.decode("utf-8", "replace")
    out = parse_claude(done.returncode, stdout, done.stderr.decode("utf-8", "replace"))
    out["worker"] = worker_record(stdout, args.response, "claude-code", args.model)
    return out


def failed(summary, why):
    return {"status": "failed", "summary": summary, "verified": [], "non-claims": [why], "decisions": []}


def parse_claude(returncode, stdout, stderr):
    """The host's stream-json -> the response. Anything short of a well-formed answer is `failed`, never `done`."""
    result = None
    for line in stdout.split("\n"):
        if line.strip():
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if isinstance(event, dict) and event.get("type") == "result":
                result = event
    if returncode or not result or result.get("is_error"):
        return failed("host exited %d: %s" % (returncode, (result or {}).get("subtype") or stderr[-400:]),
                      "the host session did not finish; nothing it did is verified")
    out = result.get("structured_output")
    if out is None:
        try:
            out = json.loads(result.get("result", "").strip())
        except ValueError:
            return failed("no structured answer", "answer was not the required JSON")
    if not isinstance(out, dict) or out.get("status") not in ("done", "blocked", "failed"):
        return failed("answer has no valid status", "answer did not match the schema")
    for key, default in (("verified", []), ("decisions", []), ("non-claims", []), ("summary", "")):
        out.setdefault(key, default)
    return out


def worker_record(stdout_text, response_path, host, model=None, stderr_text=None):
    """Who did this call, from the host's own account of the session — model, turns, cost — with the whole stream kept next
    to the response as `<response>.transcript.jsonl`. The runner copies this into `performed_by`; an answer whose procedure is
    not on disk cannot be audited (a verdict of "accept, no findings" says nothing about what was read).
    Claude Code says it in its stream (`init`: model; `result`: turns, cost, session). Codex says it in the header it prints
    on stderr (`model:`, `session id:`, `reasoning effort:`); its stream is `--json` items on stdout."""
    rec = {"host": host, "model": model}
    if host == "codex":
        # `--json` gives the item stream and the thread id, not the header; the model is in the rollout Codex keeps for
        # that thread (~/.codex/sessions/**/rollout-*-<thread id>.jsonl, `turn_context.model`)
        m = re.search(r'"thread_id":\s*"([^"]+)"', stdout_text or "")
        if m:
            rec["session"] = m.group(1)
            home = os.environ.get("HUNSU_CODEX_DIR") or os.environ.get("CODEX_HOME") or os.path.join(os.path.expanduser("~"), ".codex")
            for dirpath, _, files in os.walk(os.path.join(home, "sessions")):
                for f in files:
                    if f.endswith(m.group(1) + ".jsonl"):
                        with open(os.path.join(dirpath, f), encoding="utf-8", errors="replace") as fh:
                            for line in fh:
                                mm = re.search(r'"turn_context".*?"model":\s*"([^"]+)"', line)
                                if mm:
                                    rec["model"] = rec["model"] or mm.group(1)
                                    ee = re.search(r'"effort":\s*"([^"]+)"', line)
                                    if ee:
                                        rec["effort"] = ee.group(1)
                                    break
        for key, name in (("model", "model"), ("session id", "session"), ("reasoning effort", "effort")):   # the header, when a host prints one
            mh = re.search(r"^%s:\s*(.+?)\s*$" % re.escape(key), stderr_text or "", re.M)
            if mh and not rec.get(name):
                rec[name] = mh.group(1)
        kept = "".join(x for x in (stderr_text, stdout_text) if x)
        if kept:
            path = re.sub(r"\.json$", "", response_path) + ".transcript.jsonl"
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
            with open(path, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(kept if kept.endswith("\n") else kept + "\n")
            rec["transcript"] = os.path.basename(path)
        return rec
    if stdout_text is None:
        return rec
    for line in stdout_text.split("\n"):
        try:
            event = json.loads(line) if line.strip() else None
        except ValueError:
            continue
        if not isinstance(event, dict):
            continue
        if event.get("type") == "system" and event.get("subtype") == "init":
            rec["model"] = event.get("model") or model
        elif event.get("type") == "result":
            rec["turns"], rec["cost_usd"], rec["session"] = event.get("num_turns"), event.get("total_cost_usd"), event.get("session_id")
    path = re.sub(r"\.json$", "", response_path) + ".transcript.jsonl"
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(stdout_text if stdout_text.endswith("\n") else stdout_text + "\n")
    rec["transcript"] = os.path.basename(path)
    return rec


def codex(prompt, args, target):
    tmp = tempfile.mkdtemp(prefix="dakdol-build-")
    schema_path, out_path = os.path.join(tmp, "schema.json"), os.path.join(tmp, "last.txt")
    Path(schema_path).write_text(json.dumps(SCHEMA), encoding="utf-8")
    sandbox = os.environ.get("AGENT_CODEX_SANDBOX")
    cmd = [shutil.which("codex") or "codex", "exec", "--json", "--skip-git-repo-check", "--output-schema", schema_path, "-o", out_path, "-C", target] + (["-s", sandbox] if sandbox else ["--approve-for-me"])
    if args.model:
        cmd += ["-m", args.model]
    if args.effort:
        cmd += ["-c", "model_reasoning_effort=%s" % json.dumps(args.effort)]
    cmd.append("-")
    done = subprocess.run(cmd, input=prompt.encode("utf-8"), capture_output=True, env=dict(os.environ, AGENT_WORKER="1"))
    try:
        out = json.loads(Path(out_path).read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        out = {"status": "failed", "summary": "codex exited %d without a JSON answer" % done.returncode, "verified": [], "non-claims": [], "decisions": []}
    out["worker"] = worker_record(done.stdout.decode("utf-8", "replace"), args.response, "codex", args.model, done.stderr.decode("utf-8", "replace"))
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--request", required=True)
    ap.add_argument("--response", required=True)
    ap.add_argument("--host", choices=["claude", "codex"], default="claude")
    ap.add_argument("--model", default=None)
    ap.add_argument("--effort", default=None)
    ap.add_argument("--max-turns", type=int, default=int(os.environ.get("DAKDOL_MAX_TURNS", 40)), help="turn budget for the session (env DAKDOL_MAX_TURNS)")
    ap.add_argument("--prompt-only", action="store_true", help="print the prompt this call would send and exit — for a session that dispatches the host's own subagent (a `native:` provider) instead of this worker")
    args = ap.parse_args()
    request = json.loads(Path(args.request).read_text(encoding="utf-8"))
    if request.get("artifact-type") != "chongdae/request@1":
        raise SystemExit("not a chongdae request: %s" % args.request)
    prompt = (skill_text() + "\n\n# Request\n\nWork only inside `target`. Do not write the response file; answer with the JSON object.\n\n"
              + json.dumps(request, ensure_ascii=False, indent=2))
    if args.prompt_only:
        print(prompt + "\n\n# Answer\n\nYour whole final message is one JSON object, nothing else, matching this schema:\n" + json.dumps(SCHEMA))
        return 0
    out = (claude if args.host == "claude" else codex)(prompt, args, request["target"])
    with open(args.response + ".tmp", "w", encoding="utf-8", newline="\n") as fh:   # LF on every host; the response is diffed and fingerprinted
        fh.write(json.dumps(out, ensure_ascii=False, indent=2) + "\n")
    os.replace(args.response + ".tmp", args.response)   # whole or absent: the runner polls for this file
    print("%s -> %s" % (out.get("status"), args.response))
    return 0


if __name__ == "__main__":
    sys.exit(main())
