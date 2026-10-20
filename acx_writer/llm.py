"""Claude Sonnet 5.5 client shared by every pipeline step."""

from __future__ import annotations

import json
import os
import random
import sys
import time
from pathlib import Path

try:
    import anthropic
except ImportError:  # pragma: no cover - runtime dependency check
    sys.exit("Missing dependency: pip install -r requirements.txt")

REPO_DIR = Path(__file__).resolve().parent.parent
KEY_FILE = REPO_DIR / "anthropic_key.txt"

MODEL = os.getenv("CLAUDE_MODEL", "claude-sonnet-5-5")

# Effort per kind of work (low | medium | high | xhigh | max). Thinking is
# adaptive, so Claude decides how much to think within each level. Planning
# and editing get more effort than routine extraction.
EFFORT = {
    "research": os.getenv("EFFORT_RESEARCH", "high"),
    "outline": os.getenv("EFFORT_OUTLINE", "high"),
    "write": os.getenv("EFFORT_WRITE", "medium"),
    "edit": os.getenv("EFFORT_EDIT", "medium"),
    "review": os.getenv("EFFORT_REVIEW", "high"),
    "marketing": os.getenv("EFFORT_MARKETING", "medium"),
    "extract": os.getenv("EFFORT_EXTRACT", "low"),
}

# Retries a cyber/frontier_llm refusal on another model inside the same call.
FALLBACK_BETA = "server-side-fallback-2026-07-01"
WEB_SEARCH_TOOL = {"type": "web_search_20260209", "name": "web_search", "max_uses": 8}

_client: anthropic.Anthropic | None = None


class ClaudeError(RuntimeError):
    pass


def load_api_key() -> str:
    key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if not key and KEY_FILE.exists():
        key = KEY_FILE.read_text().strip()
        if " - " in key:  # old files were saved as "label - sk-ant-..."
            key = key.split(" - ")[-1].strip()
    if not key:
        sys.exit("No Anthropic API key: set ANTHROPIC_API_KEY or create anthropic_key.txt")
    return key


def client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=load_api_key(), timeout=900.0, max_retries=4)
    return _client


def ask(
    prompt: str | list,
    *,
    kind: str,
    system: str | None = None,
    max_tokens: int = 32000,
    schema: dict | None = None,
    web_search: bool = False,
    label: str = "",
    retries: int = 4,
) -> str:
    """Send one prompt to Claude and return the text of the reply.

    `prompt` may be a string or a list of content blocks (images + text).
    With `schema`, the reply is guaranteed to be JSON matching it.
    """
    params: dict = {
        "model": MODEL,
        "max_tokens": max_tokens,
        "thinking": {"type": "adaptive"},
        "output_config": {"effort": EFFORT[kind]},
        "messages": [{"role": "user", "content": prompt}],
        "betas": [FALLBACK_BETA],
        "fallbacks": "default",
    }
    if system:
        params["system"] = system
    if schema:
        params["output_config"]["format"] = {"type": "json_schema", "schema": schema}
    if web_search:
        params["tools"] = [WEB_SEARCH_TOOL]

    tag = f" [{label}]" if label else ""
    for attempt in range(1, retries + 1):
        try:
            return _run(params)
        except (anthropic.RateLimitError, anthropic.InternalServerError,
                anthropic.APIConnectionError, anthropic.APITimeoutError) as e:
            wait = min(120, 10 * 2 ** (attempt - 1)) + random.uniform(0, 3)
            print(f"  ⚠ Claude{tag} {type(e).__name__}; retrying in {wait:.0f}s ({attempt}/{retries})")
            time.sleep(wait)
        except anthropic.BadRequestError as e:
            raise ClaudeError(f"Claude{tag} rejected the request: {e.message}") from e
        except ClaudeError as e:
            if attempt == retries:
                raise
            print(f"  ⚠ Claude{tag} {e}; retrying ({attempt}/{retries})")
    raise ClaudeError(f"Claude{tag} failed after {retries} attempts")


def ask_json(prompt: str | list, *, schema: dict, **kwargs) -> dict:
    return json.loads(ask(prompt, schema=schema, **kwargs))


def cached(shared: str, task: str) -> list:
    """Content blocks for calls that repeat a long `shared` text (e.g. the whole
    manuscript) with a different `task` each time: the shared part is cached."""
    return [
        {"type": "text", "text": shared, "cache_control": {"type": "ephemeral"}},
        {"type": "text", "text": task},
    ]


def _run(params: dict) -> str:
    messages = list(params["messages"])
    texts: list[str] = []
    # A web-search turn can pause; resume it until Claude finishes.
    for _ in range(5):
        with client().beta.messages.stream(**{**params, "messages": messages}) as stream:
            msg = stream.get_final_message()
        if msg.stop_reason == "refusal":
            category = getattr(msg.stop_details, "category", None)
            raise ClaudeError(f"refused the request (category: {category})")
        texts.extend(b.text for b in msg.content if b.type == "text")
        if msg.stop_reason == "max_tokens":
            raise ClaudeError(f"hit max_tokens ({params['max_tokens']}) before finishing")
        if msg.stop_reason != "pause_turn":
            break
        messages = messages + [{"role": "assistant", "content": msg.content}]
    # Search citations split one answer into many text blocks, so join without separators.
    text = "".join(texts).strip()
    if not text:
        raise ClaudeError("returned an empty reply")
    return text
