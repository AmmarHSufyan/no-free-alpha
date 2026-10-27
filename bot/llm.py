"""Call Claude through the Claude Code CLI in headless mode (`claude -p`), with a disk cache.

Every answer is cached by a hash of (model, system prompt, prompt, schema), so re-running a backtest
costs nothing and returns exactly the same decisions.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path

from . import config

CACHE_PATH = config.RUNS_DIR / "ai_cache.jsonl"


class LLMError(RuntimeError):
    pass


def find_claude() -> str:
    """Locate the claude executable; prefer the native .exe over npm's .cmd shim (cmd.exe mangles JSON args)."""
    if os.environ.get("CLAUDE_BIN"):
        return os.environ["CLAUDE_BIN"]
    found = shutil.which("claude")
    if not found:
        raise LLMError("Claude CLI not found on PATH. Install Claude Code or set CLAUDE_BIN.")
    native = Path(found).parent / "node_modules" / "@anthropic-ai" / "claude-code" / "bin" / "claude.exe"
    return str(native) if native.exists() else found


class ClaudeCLI:
    def __init__(self, model: str = "haiku", cache_path: Path = CACHE_PATH, timeout: int = 180, retries: int = 2):
        self.model, self.cache_path, self.timeout, self.retries = model, cache_path, timeout, retries
        self.bin = find_claude()
        self.cost_usd = 0.0
        self.calls = 0
        self.cache_hits = 0
        self._lock = threading.Lock()
        self._cache = self._load_cache()
        # An empty working directory (plus --safe-mode / --tools "") keeps the CLI away from this project's files.
        self._cwd = config.RUNS_DIR / "claude-cwd"
        self._cwd.mkdir(parents=True, exist_ok=True)

    def _load_cache(self) -> dict:
        cache = {}
        if self.cache_path.exists():
            for line in self.cache_path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    row = json.loads(line)
                    cache[row["key"]] = row["response"]
        return cache

    def _key(self, system: str, prompt: str, schema: dict) -> str:
        blob = json.dumps([self.model, system, prompt, schema], sort_keys=True)
        return hashlib.sha256(blob.encode()).hexdigest()

    def cached(self, system: str, prompt: str, schema: dict) -> bool:
        return self._key(system, prompt, schema) in self._cache

    def ask(self, system: str, prompt: str, schema: dict) -> dict:
        key = self._key(system, prompt, schema)
        with self._lock:
            if key in self._cache:
                self.cache_hits += 1
                return self._cache[key]
        out, cost = self._call(system, prompt, schema)
        with self._lock:
            self._cache[key] = out
            self.cost_usd += cost
            self.calls += 1
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            with self.cache_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps({"key": key, "model": self.model, "ts": time.time(), "cost_usd": cost, "response": out}) + "\n")
        return out

    def _call(self, system: str, prompt: str, schema: dict) -> tuple[dict, float]:
        cmd = [self.bin, "-p", "--model", self.model, "--output-format", "json", "--json-schema", json.dumps(schema),
               "--system-prompt", system, "--tools", "", "--safe-mode", "--strict-mcp-config", "--no-session-persistence"]
        # Allow running from inside a Claude Code session (it refuses to nest otherwise).
        env = {k: v for k, v in os.environ.items() if k not in ("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT")}
        last_err = ""
        for attempt in range(self.retries + 1):
            try:
                proc = subprocess.run(cmd, input=prompt, capture_output=True, text=True, encoding="utf-8",
                                      timeout=self.timeout, cwd=self._cwd, env=env)
                msg = json.loads(proc.stdout)
                if not msg.get("is_error") and isinstance(msg.get("structured_output"), dict):
                    return msg["structured_output"], float(msg.get("total_cost_usd") or 0)
                last_err = msg.get("result") or proc.stderr or "no structured_output"
            except (subprocess.TimeoutExpired, json.JSONDecodeError) as e:
                last_err = f"{type(e).__name__}: {e}"
            time.sleep(2 * (attempt + 1))
        raise LLMError(f"claude CLI failed after {self.retries + 1} attempts: {str(last_err)[:500]}")
