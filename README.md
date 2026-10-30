# glia

A maintenance agent for a Markdown/Obsidian vault that can only **propose**. It suggests reversible
housekeeping (archive a finished project, park a quiet one, fix a frontmatter value, fold a stale
handoff note into an archive, file a loose note). A person approves each suggestion with a ✅. A separate
executor applies only the approved items, one git commit per item, on a `glia/*` branch of a dedicated
clone. What the agent may never touch is decided in Python (`glia/ladder.py`), not in a prompt.

Named for glial cells, the brain's maintenance crew.

## Why it exists

A notes vault holds things you would not hand to a model with write access: finances, legal documents,
personal context. Note text is also untrusted input. A captured web page can contain instructions
addressed to an agent. So glia keeps the trust boundary out of the prompt:

- The agent side has no write path. Every mutating tool records a proposal and returns.
- The executor is the only writer. It re-checks every approved item against the ladder and refuses
  forbidden paths and actions even when someone ticked them.
- Every applied item is a single commit with a `git revert <sha>` line, so one revert undoes one item.
- `scripts/prove-red.sh` disables each guard in turn and requires the test suite to fail. A guard whose
  removal leaves the suite green counts as a bug.

## How it works

```
agent (any MCP client, in a sandbox)
   │  POST /mcp, bearer token
   ▼
glia.gateway ── reads the clone, records proposals ──► state/proposals.jsonl
   │  finish()
   ▼
state/runs/<run_id>.json ──► glia.relay ──► Discord: one line per item, ✅ pre-seeded
                                   │  owner's ✅
                                   ▼
                     state/approvals/<run_id>.json
                                   │
                                   ▼
glia.apply --items ──► one commit per item on glia/<date> (refuses what the ladder forbids)
                                   │
                                   ▼
glia.mergegate ──► ff-merge (renames + frontmatter-only edits) | pr | refused
```

The merge gate only classifies the branch. Merging is still a manual step.

| Module | Role |
|---|---|
| `glia/ladder.py` | 12 guards, each `guard_<name>()` raising `Refused(name)`; `PROPOSE_ONLY` is a constant |
| `glia/gateway.py` | MCP server over HTTP (JSON replies), 10 tools, sha256-checked bearer token, source allowlist, binds only inside a network you name |
| `glia/apply.py` | approval executor (the only writer) and the replay harness |
| `glia/mergegate.py` | classifies a `glia/*` branch against `main` |
| `glia/digest.py` | renders a run as a text digest, `--dry` only |
| `glia/relay.py` | posts a run to a Discord channel once and turns the owner's ✅ into executor-shaped approvals; never executes |
| `glia/proposals.py` | JSONL proposal store and per-run snapshots |
| `glia/frontmatter.py` | edits YAML frontmatter line by line so the note body stays byte-identical |
| `glia/handoff_relink.py` | repoints `[[handoff-x]]` wikilinks at the archive heading after a handoff is collapsed |
| `glia/fakevault.py` | builds a small fixture vault (origin + clone) for tests and self-tests |

### Tools and the ladder

| Tool | Tier | Executor refusals, even when approved |
|---|---|---|
| `list_projects`, `read_note` | read (allowlisted folders only) | — |
| `propose`, `finish` | meta: free-form suggestion; close the run | — |
| `archive` | ask: whole folder to `Projects/_archive/`; open tasks that link it must be acknowledged | `open_task_archive` |
| `archive_task` | ask: only closed tasks move to `Tasks/_archive/` | `task_not_closed` |
| `park` | ask: stamp `parked: <date>` in frontmatter | — |
| `fix_frontmatter` | ask: set or unset frontmatter keys, body untouched | — |
| `collapse_handoff` | ask: copy the note verbatim into `archive-handoffs.md`, then remove it | `delete` if the content did not land |
| `move_loose_file` | ask: `git mv` inside the clone | `outside_vault` |
| *(no tool)* | never: delete anything; `Finances/`, `Departments/legal/`, `Context/concepts/`, `user.md`, any `CLAUDE.md`, agent tooling; writing or pushing `main` | `delete`, `finances`, `legal`, `concepts`, `user_md`, `claude_md`, `skill_library`, `main_push` |

The folder names are one vault's layout and live as constants in `glia/ladder.py`. Change them to fit yours.

## Running it

Python 3.12 (what CI runs) and PyYAML. Everything below works on the bundled fixture vault and touches
nothing else.

```bash
pip install -r requirements.txt pytest

python3 -m glia.gateway --selftest      # every mutating tool is blocked, the clone's tree is unchanged
python3 -m glia.gateway --fence-test    # ten executor writes the ladder must refuse
python3 -m glia.apply --replay fixtures/replay-demo.json --scratch
python3 -m glia.mergegate --check glia/replay                    # expects verdict=ff-merge
python3 -m glia.mergegate --check glia/replay --plus-body-edit   # one body edit flips it to pr
python3 -m glia.digest --from fixtures/sample-run.json --dry
```

The replay demo builds the fixture vault, makes an "expected" restructure by hand with plain git, then
applies the same changes as four approved items and checks that names and frontmatter match. To replay
a real restructure, point `source_repo` at your vault and give `base` and `expect` commits.

Serving the gateway to an agent:

```bash
mkdir -p ~/.config/glia
printf '%s' "$TOKEN" | sha256sum | cut -d' ' -f1 > ~/.config/glia/gateway.token.sha256
GLIA_PROPOSER_MODEL=<model name> python3 -m glia.gateway \
  --bind-net 198.51.100.0/24 --bind 198.51.100.10 --port 8792 \
  --allow-src 198.51.100.20 --token-sha256-file ~/.config/glia/gateway.token.sha256 \
  --clone ~/glia-vault
```

The addresses are documentation placeholders. The gateway refuses wildcard binds, addresses outside
`--bind-net`, and a `/0` network. `systemd/glia-gateway.service` is an example user unit with the same
flags.

Applying approved items to a dedicated clone:

```bash
python3 -m glia.apply --clone ~/glia-vault --branch glia/2026-10-15 --items approvals.json
```

## Tests

```bash
python3 -m pytest -q          # 100 tests on throwaway fixture vaults
bash scripts/prove-red.sh     # disables each of the 12 guards in a temp copy; expects RED-OK guards=12/12
ruff check .
shellcheck scripts/prove-red.sh
```

CI (`.github/workflows/ci.yml`) runs all of the above plus the self-test, fence test, replay demo,
merge-gate check and digest dry run.

## Limits

- `PROPOSE_ONLY` is a constant. The gateway contains an acting path that would call the executor
  directly, but the ladder blocks it while the constant is `True`, and it has no tests of its own.
- The gateway speaks plain HTTP with JSON replies: `initialize`, `ping`, `tools/list`, `tools/call`.
  No TLS, no SSE streaming, no sessions. Run it inside a private network or VPN.
- The relay is a library. It expects your bot to pass in Discord `api` and `post` helpers; this repo
  contains no bot and no network client of its own.
- Open-task detection matches wikilinks by note name. A task that names a project in plain text without
  a link is not seen.
- Frontmatter editing handles top-level keys only.
- The merge gate classifies; it does not merge or push.
- The bundled fixtures are synthetic. The run snapshot in `fixtures/sample-run.json` is hand-written.

## License

MIT. See `LICENSE`.
