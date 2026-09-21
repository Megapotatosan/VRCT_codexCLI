# Codex / ChatGPT translation provider (`Codex_CLI`)

Translate with a ChatGPT account through the official
[Codex CLI](https://www.npmjs.com/package/@openai/codex) installed on the
same machine — no OpenAI API key.

This is a separate engine from `OpenAI_API`. They reach the same models,
but the authentication mechanism is different, and that difference is the
whole point of this provider:

| | `OpenAI_API` | `Codex_CLI` |
|---|---|---|
| Credential | API key, stored by VRCT | ChatGPT login, stored by the Codex CLI |
| Billing | OpenAI API usage | the user's ChatGPT plan |
| Setup | paste a key | click two buttons |

VRCT never sees, stores, or forwards a ChatGPT credential. Login and
credential storage belong entirely to the official CLI, which keeps them
under `CODEX_HOME` (default `~/.codex`) or in the OS credential store.

## Usage

1. Open VRCT and go to **Config → Translation**.
2. Find **Codex / ChatGPT**.
3. If it shows *Codex CLI: Not Installed*, click **Install Codex CLI** and
   confirm. VRCT installs Node.js LTS first if the machine doesn't have a
   usable runtime.
4. Click **Connect ChatGPT**. The official login opens in your browser.
5. Complete the login. The panel switches to *ChatGPT Account: Connected*.
6. Select **Codex / ChatGPT** as the translation engine and start
   translating.

You never need to open PowerShell or a Command Prompt, and you never need
to know what Node.js, npm, winget, or PATH are.

## Privacy

**This is not local translation.** Unlike CTranslate2, LM Studio, and
Ollama, text you translate with this engine leaves your machine and is
sent to OpenAI, under your ChatGPT account's data-handling settings.

VRCT's own diagnostics record the *shape* of each translation — provider,
route, cold/warm, character count, elapsed milliseconds, outcome — and
never the message text:

```
provider=codex route=one_shot state=warm chars=42 elapsed_ms=820 outcome=ok
```

Each `codex exec` runs with `--ephemeral`, so the CLI writes no session
files to disk, and with `--sandbox read-only` in a throwaway temporary
directory, so it cannot read your project files or run commands.

## How it works

```
VRChat → VRCT → Speech-to-Text → VRCT Translator
       → Codex Translation Provider → local Codex CLI
       → ChatGPT account authentication → Codex model
       → translation → VRCT → VRChat OSC
```

Two Python modules:

- `src-python/models/translation/translation_codex_cli.py` — the OS
  boundary. Finds `codex`/`node`/`npm`/`winget`, installs, asks the CLI
  for login status, runs one translation. No VRCT dependencies, so it is
  testable by mocking three seams: `_run`, `_which`, `_is_file`.
- `src-python/models/translation/translation_codex.py` — `CodexClient`,
  the VRCT-facing translation client.

`Codex_CLI` is registered in `CONNECTION_PROVIDER_REGISTRY` alongside
LM Studio and Ollama. All three have the same shape — no auth key, a
connection check that yields a model list — so no new provider layer was
added. The only Codex-specific controller code is the install/login flow
and splitting "not connected" into the states the UI needs.

### Installation

Only ever on an explicit click, never at startup:

```
Install Codex CLI → check codex → not found → check node/npm
  → winget install OpenJS.NodeJS.LTS (if needed)
  → npm install --global @openai/codex@latest
  → re-discover codex → verify `codex --version` → Connect ChatGPT
```

`npm` exiting 0 is not treated as success: the executable is discovered
again and `codex --version` must actually run. After installing, PATH in
the already-running VRCT process is stale, so discovery also checks known
install locations (`%APPDATA%\npm`, `C:\Program Files\nodejs`) rather than
relying on `shutil.which` alone — no restart required.

On Windows, `codex.cmd` is preferred over the extensionless Unix shim npm
drops in the same folder; handing the latter to `subprocess` fails with
*%1 is not a valid Win32 application*.

### Authentication

`codex login status` is the source of truth. It writes to **stderr**:

| Output | Exit | Treated as |
|---|---|---|
| `Logged in using ChatGPT` | 0 | connected |
| `Logged in using an API key - sk-…` | 0 | **not** connected |
| `Not logged in` | 1 | not connected |
| anything else | 0 | not connected (unknown) |

An API-key login is deliberately rejected. If it were accepted, a user who
selected this engine to use their ChatGPT plan would silently be billed
for API usage instead. For the same reason every child process runs with
`OPENAI_API_KEY`, `OPENAI_BASE_URL`, `AZURE_OPENAI_API_KEY` and friends
stripped from its environment, while `CODEX_HOME` is preserved.

Unrecognised output is classified as unknown rather than assumed to be
ChatGPT — if the CLI's wording changes, failing closed is the safe side.

### Runtime

One-shot `codex exec` per translation (Option A in the guideline). The
persistent app-server was not adopted for the first version: `codex
app-server` is still marked `[experimental]` in the CLI, and its
JSON-RPC surface can change between releases. Betting live translation on
an unstable protocol contradicts the priority order — *don't break VRCT*
and *stability* both rank above latency.

The seam for it exists: latency logging already records `route=one_shot`,
and `CodexClient.close()` is in place, so adding `route=persistent` with
one-shot as the documented fallback is an additive change.

Concurrency is bounded by a semaphore (2 concurrent processes), because
VRCT can translate mic, speaker, and chat simultaneously and must not
spawn processes without limit.

Timeouts are separate per purpose, so one never truncates another:

| Purpose | Budget |
|---|---|
| Translation | 30s |
| Probe (`--version`, `login status`) | 20s |
| Login (browser flow) | 600s |
| Installation | 900s |

### Structured output

`codex exec --output-schema` requests `{"translation": "..."}`. If the
installed CLI doesn't accept the flag, the client retries once as plain
text and stays in plain-text mode for the rest of the session. Set
`use_structured_output: false` in
`translation_settings/prompt/translation_codex.yml` to skip the probe.

### Conversation history

VRCT's own history is injected into the prompt, exactly as the other LLM
providers do it. Codex conversation sessions are not used as memory —
combined with `--ephemeral`, that keeps each translation independent of
whatever ran before it.

## Endpoints

| Endpoint | Purpose |
|---|---|
| `/get/data/codex_status` | `{installed, connected, auth_mode, version}` |
| `/run/codex_connection` | verify the engine is usable |
| `/run/codex_install` | start installation (async) |
| `/run/codex_login` | start `codex login` (async) |
| `/run/codex_logout` | `codex logout` |
| `/get/data/selectable_codex_model_list` | selectable models |
| `/get|set/data/selected_codex_model` | selected model |

`/run/codex_install` and `/run/codex_login` return `200 true` immediately
and push the real result on the same route when the background thread
finishes, so a 15-minute install cannot occupy a handler thread or freeze
the UI. The UI ignores the `true` ack and only clears its spinner on the
completion push, or on an error.

## Model selection

The first version offers `Automatic` only. There is no stable, machine-
readable way to enumerate the models a given ChatGPT account can reach
through Codex, and hardcoding names guarantees a list that goes stale.
With `Automatic`, `--model` is not passed and the user's
`~/.codex/config.toml` decides. The dropdown is wired like every other
engine's, so adding real choices later is a backend-only change.

## Languages

`Codex_CLI` reuses the `OpenAI_API` language set — the same models are
behind both, so a separate set would have no basis.

Cantonese has no dedicated entry. `model.getListLanguageAndCountry()`
intersects translation languages with the *transcription* language table,
so a language the transcription engine can't recognise would be a dead
option in the picker. In practice Cantonese runs as Chinese Traditional,
and the prompt's instruction to preserve conversational tone and slang
keeps it Cantonese in the output.

## Tests

```bash
python -m pytest src-python/test/test_translation_codex_installation.py src-python/test/test_translation_codex_auth.py src-python/test/test_translation_codex_provider.py src-python/test/test_controller_translation_codex_endpoints.py
```

No test installs anything, spawns a `codex` process, or touches the
network — the OS boundary is mocked at `_run`, `_which` and `_is_file`.

## Known limitations

- **Automatic installation is Windows-first.** It needs `winget` (Windows
  App Installer). On macOS and Linux an existing Node.js/npm is used; if
  there is none, the CLI must be installed manually.
- **Installing Node.js LTS may require elevation.** `winget install
  OpenJS.NodeJS.LTS` installs machine-wide and runs with
  `--disable-interactivity`, so on a machine where the user cannot elevate
  it fails cleanly with *Failed to install the required Node.js runtime*
  rather than prompting. Installing Node.js manually, or running VRCT
  elevated once, works around it.
- **Latency is higher than a local engine.** Every translation starts a
  process and makes a network round trip.
- Requires an internet connection and a ChatGPT account, and is subject to
  that account's usage limits and to Codex service availability.
- Model availability depends on the ChatGPT plan.
