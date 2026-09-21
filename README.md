# dakdol

The implementer of the LLM-development era: the hands. Receives a contract (what to build and the sentences that decide it), builds one slice in a fresh bounded process, runs the project's real checks, and reports what it verified, what it decided beyond the contract, and what it does not claim. It does not decide what comes next.

## Install

```
claude plugin marketplace add guinjaaaaaaaaaaaaaaaaaaaaakeop/dakdol
claude plugin install dakdol@dakdol
```

Codex: `codex plugin marketplace add guinjaaaaaaaaaaaaaaaaaaaaakeop/dakdol`, `codex plugin add dakdol@dakdol` (the skill is `$dakdol:build`).

## How it is called

chongdae (or any runner) writes a `chongdae/request@1` file and runs the argv this plugin declares as its `build` role (plugin.json `roles`; a project names it `"implementer": "dakdol:build"` in hunsu.json and the lock carries the argv):

```
python3 <plugin root>/build_worker.py --request <file> --response <file> [--host claude|codex] [--model M] [--effort E] [--max-turns N]
```

The worker starts a fresh host session with the `build` skill and the request, lets it read, edit, write and run checks inside the target, and saves its one JSON answer: `status`, `summary`, `verified` (the checks it ran, with exit codes), `decisions` (every behavior the contract left undecided that it had to settle — what, chosen, alternatives), `non-claims`, and `worker` — the call's own account (host, model, turns, cost, session) with the whole host stream kept next to the response as `<response>.transcript.jsonl`, so the runner's `performed_by` names the model and a report can be checked against what was actually read and run. The runner decides with its own checks afterwards; a non-empty `decisions` is not done until a human accepts it.

`build_worker.py --prompt-only --request <file> --response <file>` prints the exact prompt a call would send (the skill/eyes text, the request, the answer schema) and exits — for a session that dispatches its host's own subagent instead of this worker (a `native:` provider in chongdae). The subagent's answer is then written by the session; this worker never ran.

## Limits

- One slice per call; no memory between calls. A slice that spans calls resumes from the tree and the earlier attempts' reports that chongdae puts in the request (`attempts`, `touched`) — see the `build` skill, step 7.
- The model lists its decisions far more reliably than it stops for them; the runner enforces the stop, the skill only asks.
- Not built yet: a trace (section -> symbols -> tests) in the response so the link step needs no separate proposal; a verify-only mode.

## Versioning

Semver, and a version names one content: every change to the source — code, skill or command text, hooks, this README — bumps
the version in all three manifests (`plugin.json`, `.claude-plugin/plugin.json`, `.codex-plugin/plugin.json`) and the
marketplace entries before it is used anywhere. Hosts copy a plugin at install and do not look again while the version stands,
so an unbumped edit is a copy nobody can tell from the old one. **patch**: behavior or wording, every interface unchanged.
**minor**: a new command, skill, field, hook or artifact key; what exists keeps working. **major**: an artifact type, lock or
record that other products read changes shape. hunsu's `check` fails a linked plugin whose source differs from the installed
copy under one version; `hunsu install --refresh <plugin>` recopies after the bump.

## Self-check

`python test_dakdol.py` — a temp project; every stop, rejection and kind of finding fires once, with recorded provider responses where a model would have answered. No model calls.
