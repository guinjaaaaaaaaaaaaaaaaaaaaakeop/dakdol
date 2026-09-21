"""Self-check for dakdol's worker: the host's stream is parsed into a response, and nothing short of a real answer becomes `done`.

  python test_dakdol.py
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import build_worker as w  # noqa: E402

# Recorded from a real run: the shape the host actually returns, trimmed.
GOOD = {"status": "done", "summary": "added count", "verified": [{"check": "python test_memo.py -k S4", "exit": 0}],
        "decisions": [{"what": "extra args", "chosen": "usage error", "alternatives": ["ignore"]}], "non-claims": ["count with extra args untested"]}


def stream(*events):
    return "\n".join(json.dumps(e) for e in events) + "\n"



class Skip(Exception):
    """Raised by a test that cannot run on this host; the runner reports it as SKIP, never as PASS."""

def test_structured_output_is_the_answer_and_missing_keys_get_defaults():
    out = w.parse_claude(0, stream({"type": "system"}, {"type": "result", "structured_output": GOOD}), "")
    assert out == GOOD
    out = w.parse_claude(0, stream({"type": "result", "structured_output": {"status": "done", "summary": "x"}}), "")
    assert out["decisions"] == [] and out["verified"] == [] and out["non-claims"] == []


def test_plain_json_in_result_text_is_accepted_but_prose_is_not():
    out = w.parse_claude(0, stream({"type": "result", "result": json.dumps(GOOD)}), "")
    assert out["status"] == "done" and out["decisions"] == GOOD["decisions"]
    out = w.parse_claude(0, stream({"type": "result", "result": "I added count and all tests pass."}), "")
    assert out["status"] == "failed" and "not the required JSON" in out["non-claims"][0]


def test_host_failure_no_result_and_bad_status_are_all_failed_never_done():
    assert w.parse_claude(1, "", "boom")["status"] == "failed"
    assert w.parse_claude(0, stream({"type": "system"}), "")["status"] == "failed"
    out = w.parse_claude(0, stream({"type": "result", "is_error": True, "subtype": "error_max_turns"}), "")
    assert out["status"] == "failed" and "error_max_turns" in out["summary"]
    out = w.parse_claude(0, stream({"type": "result", "structured_output": {"status": "maybe", "summary": "?"}}), "")
    assert out["status"] == "failed" and "schema" in out["non-claims"][0]
    out = w.parse_claude(0, stream({"type": "result", "structured_output": ["not", "an", "object"]}), "")
    assert out["status"] == "failed"


def test_skill_text_is_the_body_without_frontmatter():
    text = w.skill_text()
    assert not text.startswith("---") and "You are the implementer" in text and "decisions" in text



def test_worker_record_names_the_model_and_keeps_the_transcript():
    """The host does not tell anyone which model a session ran on except in its own stream: the init event. The worker
    keeps that (model, turns, cost, session) and the whole stream next to the response, so `performed_by` can name the
    model and a bare verdict can be audited. A Codex call, which has no such stream, records host and the model asked for."""
    import tempfile, shutil
    d = tempfile.mkdtemp(prefix="build-")
    try:
        resp = os.path.join(d, "sub", "T1.response.json")
        text = stream({"type": "system", "subtype": "init", "model": "claude-x-1", "session_id": "s1"},
                      {"type": "assistant", "message": {}},
                      {"type": "result", "num_turns": 4, "total_cost_usd": 0.05, "session_id": "s1", "structured_output": {}})
        rec = w.worker_record(text, resp, "claude-code")
        assert rec == {"host": "claude-code", "model": "claude-x-1", "turns": 4, "cost_usd": 0.05, "session": "s1", "transcript": "T1.response.transcript.jsonl"}, rec
        kept = open(os.path.join(d, "sub", "T1.response.transcript.jsonl"), encoding="utf-8").read()
        assert kept.count("\n") == 3 and '"model": "claude-x-1"' in kept.replace('"model":"', '"model": "'), kept
        assert w.worker_record(None, resp, "codex", "gpt-x") == {"host": "codex", "model": "gpt-x"}
        assert w.worker_record("not json\n", resp, "claude-code", "asked-for")["model"] == "asked-for", "no init event: the model asked for, not a guess"
    finally:
        shutil.rmtree(d, ignore_errors=True)

if __name__ == "__main__":
    failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            try:
                fn()
                print("PASS", name)
            except Skip as why:
                print("SKIP", name, "--", why)
            except (Exception, SystemExit) as err:   # a self-check that dies between tests lies by omission
                failed += 1
                print("FAIL", name, "--", "%s: %s" % (type(err).__name__, err))
    print("all passed" if not failed else "%d failed" % failed)
    sys.exit(1 if failed else 0)
