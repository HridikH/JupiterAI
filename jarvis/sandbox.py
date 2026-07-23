"""Sandboxed execution for coder output.

Default: scoped subprocess — cwd pinned to a per-project directory under
PROJECT_DIR, cleaned environment, wall-clock timeout, address-space rlimit.
That contains accidents (runaway loops, wrong-directory writes), which is the
realistic threat model for code you asked your own assistant to write.
It is NOT a security boundary against genuinely hostile code; set
USE_DOCKER=True for real isolation (python:3.12-slim + node:22-slim,
--network none, project dir bind-mounted) at the cost of image pulls and
~1s per-run container overhead.
"""
from __future__ import annotations

import re
import resource
import shutil
import subprocess
from pathlib import Path

from . import config

_FILE_RE = re.compile(r'<file path="([^"]+)">\n?(.*?)</file>', re.S)
_RUN_RE = re.compile(r"<run>(.*?)</run>", re.S)

# Fallbacks for models that answer in markdown despite the format contract
# (cloud models especially). A filename-ish line directly above a fence:
#   **index.html**  /  ### index.html  /  `index.html`  /  // index.html
_MD_NAMED_RE = re.compile(
    r"(?:^|\n)(?:\*\*|###?\s*|//\s*|#\s*)?`?([\w][\w./-]*\.[A-Za-z0-9]{1,5})`?"
    r"\s*(?:\*\*)?\s*:?\s*\n```[a-zA-Z0-9]*\n(.*?)```", re.S)
_MD_LONE_RE = re.compile(r"```([a-zA-Z0-9]*)\n(.*?)```", re.S)
_LANG_DEFAULT_NAME = {"html": "index.html", "python": "main.py", "py": "main.py",
                      "javascript": "app.js", "js": "app.js", "css": "style.css"}


class Sandbox:
    def __init__(self, project: str) -> None:
        safe = re.sub(r"[^a-zA-Z0-9_-]+", "-", project).strip("-") or "project"
        self.root = (config.PROJECT_DIR / safe).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _resolve(self, rel: str) -> Path:
        p = (self.root / rel).resolve()
        if not p.is_relative_to(self.root):
            raise ValueError(f"path escapes project dir: {rel}")
        return p

    def write(self, rel: str, content: str) -> Path:
        p = self._resolve(rel)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
        return p

    def read(self, rel: str) -> str:
        return self._resolve(rel).read_text()

    def run(self, command: str, timeout: int = config.SANDBOX_TIMEOUT_S) -> dict:
        if config.USE_DOCKER and shutil.which("docker"):
            return self._run_docker(command, timeout)
        return self._run_subprocess(command, timeout)

    def _run_subprocess(self, command: str, timeout: int) -> dict:
        def limits() -> None:
            mem = config.SANDBOX_MAX_MEM_MB * 1024 * 1024
            try:
                resource.setrlimit(resource.RLIMIT_AS, (mem, mem))
            except (ValueError, OSError):
                pass  # RLIMIT_AS is unreliable on macOS; timeout is the backstop
            resource.setrlimit(resource.RLIMIT_CPU, (timeout, timeout))

        env = {"PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin",
               "HOME": str(self.root), "LANG": "en_US.UTF-8"}
        try:
            proc = subprocess.run(
                command, shell=True, cwd=self.root, env=env,
                capture_output=True, text=True, timeout=timeout,
                preexec_fn=limits,
            )
            return {"exit_code": proc.returncode,
                    "stdout": proc.stdout[-4000:], "stderr": proc.stderr[-4000:]}
        except subprocess.TimeoutExpired:
            return {"exit_code": -1, "stdout": "", "stderr": f"timed out after {timeout}s"}

    def _run_docker(self, command: str, timeout: int) -> dict:
        image = "node:22-slim" if command.strip().startswith(("npm", "node", "npx")) \
            else "python:3.12-slim"
        proc = subprocess.run(
            ["docker", "run", "--rm", "--network", "none",
             "-m", f"{config.SANDBOX_MAX_MEM_MB}m",
             "-v", f"{self.root}:/work", "-w", "/work",
             image, "sh", "-c", command],
            capture_output=True, text=True, timeout=timeout + 30,
        )
        return {"exit_code": proc.returncode,
                "stdout": proc.stdout[-4000:], "stderr": proc.stderr[-4000:]}


def apply_coder_output(sandbox: Sandbox, text: str) -> tuple[list[str], str | None]:
    """Parse coder output; write files; return (written paths, run command or None).

    Accepts, in priority order:
    1. The contract format: <file path="...">...</file>
    2. Markdown fences preceded by a filename line (cloud-model habit)
    3. A lone fenced block (named by language) or raw HTML (-> index.html)"""
    written = []
    for m in _FILE_RE.finditer(text):
        rel, content = m.group(1), m.group(2)
        sandbox.write(rel, content)
        written.append(rel)

    # Salvage a final TRUNCATED <file> block (generation hit max tokens before
    # </file>): write what exists — a mostly-complete page beats nothing.
    remainder = _FILE_RE.sub("", text)
    t = re.search(r'<file path="([^"]+)">\n?(.*)\Z', remainder, re.S)
    if t and t.group(2).strip() and "</file>" not in t.group(2):
        try:
            sandbox.write(t.group(1), t.group(2))
            written.append(t.group(1))
            print(f"[sandbox] warning: {t.group(1)} was truncated mid-generation "
                  "(model hit its output limit); wrote the partial file")
        except ValueError:
            pass

    if not written:
        for m in _MD_NAMED_RE.finditer(text):
            rel, content = m.group(1), m.group(2)
            try:
                sandbox.write(rel, content)
                written.append(rel)
            except ValueError:
                pass  # path escaped the project dir — skip it

    if not written:
        for lang, content in _MD_LONE_RE.findall(text):
            name = _LANG_DEFAULT_NAME.get(lang.lower())
            if name is None and content.lstrip().lower().startswith("<!doctype"):
                name = "index.html"
            if name:
                sandbox.write(name, content)
                written.append(name)
                break  # one guessed file max — more would be filename roulette
        if not written and text.lstrip().lower().startswith("<!doctype"):
            sandbox.write("index.html", text.strip())
            written.append("index.html")

    run = _RUN_RE.search(text)
    return written, (run.group(1).strip() if run else None)
