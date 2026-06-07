"""Configuration: load ~/.devagent/config.toml, create a default on first run.

The config is the *only* place models are declared. Adding a model (GPT, another local
model, etc.) is a config edit, never a code change. Routing is by role, not by model name.
"""
from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

CONFIG_DIR = Path.home() / ".devagent"
CONFIG_PATH = CONFIG_DIR / "config.toml"

# Prices are $ per 1M tokens — editable ESTIMATES used only for cost/savings reporting.
# Update them to match current provider pricing; they never affect routing.
DEFAULT_CONFIG_TOML = """\
# devagent configuration. Declare any number of models; route by role.

# ── Models: local or cloud. Two protocols cover almost everything. ──
[models.qwen-local]
protocol  = "openai-compat"            # any OpenAI-compatible local server
base_url  = "http://localhost:8080/v1" # llama.cpp default is 8080; check your --port!
model_id  = "qwen3.6-27b"              # Qwen3.6 27B, 128K context — strong on well-scoped subtasks
tier      = "local"                    # local => ~$0
timeout_s = 180
api_key_env = ""                       # local servers need no key
disable_thinking = true                # Qwen3/reasoning models: turn off the thinking phase, else
                                       #   `content` comes back empty (answer lands in reasoning_content)
# Getting a local executor running (the model that does the bulk of the work):
#   • llama.cpp:  llama-server -m qwen3-27b.gguf -c 8192 --port 8080   → base_url .../v1 above
#   • Ollama (easiest on Windows):  `ollama serve` then `ollama pull qwen2.5-coder:7b`,
#       then set  base_url = "http://localhost:11434/v1"  and  model_id = "qwen2.5-coder:7b"
# Tip: subtasks are SMALL by design, so a 7B–14B *coder* model is usually enough and far faster
#      than a 27B general model. Verify reachability with `devagent status`.

# Frontier via the Claude CLI subscription — NO per-token API billing.
# Spawns `claude -p` using your logged-in Pro/Max auth. Run `claude auth status` to verify.
[models.claude-cli]
protocol  = "cli"
command   = "claude"
mode      = "claude"
model_id  = "sonnet"                   # use "opus" for the hardest decomposition/review
tier      = "cli"                      # marginal cost $0; reports API-equivalent cost
timeout_s = 300

[models.claude-cli-opus]
protocol  = "cli"
command   = "claude"
mode      = "claude"
model_id  = "opus"
tier      = "cli"
timeout_s = 400

# Codex CLI (experimental adapter — `codex exec`, sandbox read-only):
# [models.codex-cli]
# protocol = "cli"
# command  = "codex"
# mode     = "codex"
# model_id = "gpt-5.1-codex"
# tier     = "cli"

# Metered API alternatives (only if you prefer API billing over a subscription):
# [models.opus]
# protocol = "anthropic"
# model_id = "claude-opus-4-8"
# tier     = "frontier"
# api_key_env = "ANTHROPIC_API_KEY"

# ── Role -> ordered model chain (first = primary, rest = fallback). ──
[roles]
classifier = ["qwen-local", "claude-cli"]
executor   = ["qwen-local", "claude-cli"]   # local primary; CLI fallback if no local server is up
compressor = ["qwen-local", "claude-cli"]
planner    = ["claude-cli-opus", "claude-cli", "qwen-local"]   # decomposition; CLI subscription
reviewer   = ["claude-cli", "qwen-local"]
# embedder = ["embed-local"]                # OPT-IN semantic retrieval (gap #4): rank files by
#                                           #   MEANING, not just keywords — surfaces the right slice
#                                           #   on a 100k-LOC repo even with no shared terms. Declare
#                                           #   an embeddings model below and uncomment. Absent =>
#                                           #   lexical-only (default, fully offline & deterministic).
# [models.embed-local]                      # e.g. llama.cpp started with `--embeddings`, or Ollama:
#   protocol = "openai-compat"              #   `ollama pull nomic-embed-text`
#   base_url = "http://localhost:8080/v1"   # vectors are cached in the index, so a query embeds ONE
#   model_id = "nomic-embed-text"           #   string then does O(n) cosine — scales to large repos.
#   tier     = "local"

[fallback]
retries                = 2
backoff_s              = 1.5
circuit_break_after    = 3
local_timeout_fallback = true

[model_defaults]
temperature = 0.2
max_tokens  = 8192

# ── Parity envelope: keep every task inside the regime where local == frontier. ──
[envelope]
max_context_tokens = 12000             # ceiling of context fed to the local executor
max_file_lines     = 400               # window files larger than this (skeleton + focus)
max_subtask_files  = 3                 # a subtask touching more files must be decomposed

[limits]
blast_radius_warn    = 10              # warn when a change affects more than this many files
blast_radius_block   = 40              # confirm before proceeding above this (unless --yes)
token_budget_session = 0               # 0 = unlimited; else stop the session past this many tokens
cost_budget_usd      = 0               # 0 = unlimited; else stop when counterfactual cost exceeds

[gate]
run_types    = true                    # mypy
run_lint     = true                    # ruff
run_security = true                    # bandit
run_tests    = false                   # pytest (off by default; can be slow)
test_command = "pytest -q"

[compliance]
profiles = []                          # e.g. ["pci-dss", "soc2", "hipaa"] — merged into safety rules

# ── Org-workflow integration (V5). Default "null" = offline: intents are written to ──
# ── .devagent/integrations/outbox.jsonl, no network. Set a provider + creds to go live. ──
[integrations]
provider = "null"                      # null | github | jira | slack
# [integrations.github]
# repo = "owner/name"                  # blank => gh infers from the repo's cwd
# [integrations.jira]
# base_url  = "https://acme.atlassian.net"
# project   = "ORD"
# email     = "bot@acme.com"           # set for Atlassian Cloud (basic auth); omit for PAT
# token_env = "JIRA_API_TOKEN"
# [integrations.slack]
# webhook_env = "SLACK_WEBHOOK_URL"

[reporting]
counterfactual_model = "claude-cli"    # frontier model the savings/audit are measured against
audit_sample_rate    = 0.1             # fraction of tasks the quality audit samples
parity_target        = 0.9             # calibrate: min parity rate to keep a context-size bucket

[paths]
db = "~/.devagent/tasks.db"
"""


@dataclass
class ModelSpec:
    name: str
    protocol: str
    model_id: str
    tier: str = "cloud"
    base_url: str | None = None
    api_key_env: str = ""
    timeout_s: int = 180
    command: str = ""        # for protocol = "cli": the executable to spawn
    mode: str = "claude"     # for protocol = "cli": adapter ("claude" | "generic")
    extra_body: dict = field(default_factory=dict)  # extra request fields (openai-compat)

    @property
    def api_key(self) -> str | None:
        if self.api_key_env:
            return os.environ.get(self.api_key_env)
        return None


@dataclass
class Pricing:
    input: float = 0.0   # $ per 1M input tokens
    output: float = 0.0  # $ per 1M output tokens


@dataclass
class Config:
    models: dict[str, ModelSpec]
    roles: dict[str, list[str]]
    fallback: dict
    model_defaults: dict
    envelope: dict
    gate: dict
    reporting: dict
    pricing: dict[str, Pricing]
    db_path: Path
    limits: dict = field(default_factory=dict)
    raw: dict = field(default_factory=dict)

    def role_chain(self, role: str) -> list[str]:
        return self.roles.get(role, ["qwen-local"])


# Built-in default pricing (editable via [pricing.<model>] in config).
_DEFAULT_PRICING = {
    "qwen-local": Pricing(0.0, 0.0),
    "opus": Pricing(15.0, 75.0),
    "sonnet": Pricing(3.0, 15.0),
}


def ensure_config() -> Path:
    """Create the config dir + default config if missing. Returns the config path."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    if not CONFIG_PATH.exists():
        CONFIG_PATH.write_text(DEFAULT_CONFIG_TOML, encoding="utf-8")
    return CONFIG_PATH


def load_config() -> Config:
    ensure_config()
    data = tomllib.loads(CONFIG_PATH.read_text(encoding="utf-8"))

    models: dict[str, ModelSpec] = {}
    for name, m in data.get("models", {}).items():
        # Reasoning models (Qwen3, etc.) default to a thinking phase that fills
        # `reasoning_content` and can leave `content` empty — useless for the executor.
        # `disable_thinking = true` turns it off via chat_template_kwargs.
        extra_body = dict(m.get("extra_body", {}) or {})
        if m.get("disable_thinking"):
            ctk = dict(extra_body.get("chat_template_kwargs", {}))
            ctk["enable_thinking"] = False
            extra_body["chat_template_kwargs"] = ctk
        models[name] = ModelSpec(
            name=name,
            protocol=m["protocol"],
            model_id=m["model_id"],
            tier=m.get("tier", "cloud"),
            base_url=m.get("base_url"),
            api_key_env=m.get("api_key_env", ""),
            timeout_s=int(m.get("timeout_s", 180)),
            command=m.get("command", ""),
            mode=m.get("mode", "claude"),
            extra_body=extra_body,
        )

    pricing = dict(_DEFAULT_PRICING)
    for name, p in data.get("pricing", {}).items():
        pricing[name] = Pricing(float(p.get("input", 0.0)), float(p.get("output", 0.0)))

    db_path = Path(os.path.expanduser(data.get("paths", {}).get("db", "~/.devagent/tasks.db")))

    return Config(
        models=models,
        roles=data.get("roles", {}),
        fallback=data.get("fallback", {}),
        model_defaults=data.get("model_defaults", {}),
        envelope=data.get("envelope", {}),
        gate=data.get("gate", {}),
        reporting=data.get("reporting", {}),
        pricing=pricing,
        db_path=db_path,
        limits=data.get("limits", {}),
        raw=data,
    )
