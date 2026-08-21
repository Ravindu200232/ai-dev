"""ArchitectAgent — fully LLM-driven, continuous full-app generation."""
import base64
import json
import logging
import os
import re
import secrets
import textwrap
import time
from pathlib import Path

from . import docsindex, themes
from .commands import CommandRunner
from .exports import check_named_imports, group_messages, strip_noncode
from .ollama_client import OllamaClient, is_cloud_model, max_context

log = logging.getLogger("architect")


CHARS_PER_TOKEN = 3.4


HISTORY_BUDGET = 0.62


SNAPSHOT_OPEN = ("## Current source on disk — this is the truth, ignore any "
                 "older copy of these files above")
SNAPSHOT_CLOSE = "## End of current source"
SNAPSHOT_RE = re.compile(
    re.escape(SNAPSHOT_OPEN) + r".*?" + re.escape(SNAPSHOT_CLOSE), re.S)

STUB_MARK = "[written earlier —"


OPEN_RE = re.compile(
    r"<(write_file|file)\s+path\s*=\s*[\"']([^\"'>]+)[\"']\s*>", re.I)
PARTIAL_OPEN_RE = re.compile(r"<(write_file|file)\b", re.I)


CMD_RE = re.compile(r"<run_command>(.*?)</run_command>", re.I | re.S)
FENCE_RE = re.compile(r"^\s*```[a-zA-Z0-9+#-]*\s*\n(.*?)\n?\s*```\s*$", re.S)


_DOUBLED_TAG_RE = re.compile(r"</\s*([A-Za-z][\w.]*)_\1\s*>")


def _fix_doubled_tags(text: str) -> str:
    """Repair `</div_div>` to `</div>`."""
    return _DOUBLED_TAG_RE.sub(r"</\1>", text or "")


def _strip_fence(text: str) -> str:
    """Models love wrapping file bodies in markdown fences."""
    m = FENCE_RE.match(text)
    return m.group(1) if m else text


def _strip_accidental_path_close(text: str, path: str | None) -> str:
    """Remove a leaked file-protocol/path closing tag from the end of a file body."""
    body = text or ""
    rel = (path or "").strip().replace("\\", "/").lstrip("./")
    if not rel:
        return body
    trimmed = body.rstrip()
    suffix_ws = body[len(trimmed):]
    candidates = (f"</{rel}>", f"</./{rel}>")
    for marker in candidates:
        if trimmed.endswith(marker):
            return trimmed[:-len(marker)].rstrip() + suffix_ws
    return body


def _clean_streamed_file_body(text: str, path: str | None) -> str:
    return _fix_doubled_tags(_strip_accidental_path_close(_strip_fence(text), path))


def _safe_flush_len(buf: str, tag: str) -> int:
    """How much of `buf` can be emitted without risking a tag split across chunk."""
    for k in range(min(len(tag) - 1, len(buf)), 0, -1):
        if buf.endswith(tag[:k]):
            return len(buf) - k
    return len(buf)


class _RefusalLoop(Exception):
    """Raised to end a turn that has stopped making progress."""


class FileStreamParser:
    """Incremental parser splitting a token stream into prose and file bodies."""

    def __init__(self, on_text, on_file_start, on_file_token, on_file_end):
        self.on_text = on_text
        self.on_file_start = on_file_start
        self.on_file_token = on_file_token
        self.on_file_end = on_file_end
        self.buf = ""
        self.mode = "text"
        self.tag = None
        self.path = None
        self.content = ""

    def feed(self, chunk: str):
        self.buf += chunk
        self._drain()

    def close(self):
        """Flush leftovers — a file left unterminated is still salvaged."""
        if self.mode == "file":
            if self.buf:
                self.content += self.buf
                self.on_file_token(self.buf)
            self.buf = ""
            self.on_file_end(self.path, _clean_streamed_file_body(self.content, self.path))
            self.mode, self.path, self.content = "text", None, ""
        elif self.buf:
            self.on_text(self.buf)
            self.buf = ""

    def _drain(self):
        while True:
            if self.mode == "text":
                m = OPEN_RE.search(self.buf)
                if m:
                    if m.start():
                        self.on_text(self.buf[:m.start()])
                    self.tag = m.group(1).lower()
                    self.path = m.group(2).strip()
                    self.buf = self.buf[m.end():]

                    if self.buf.startswith("\n"):
                        self.buf = self.buf[1:]
                    self.content = ""
                    self.mode = "file"
                    self.on_file_start(self.path)
                    continue

                p = PARTIAL_OPEN_RE.search(self.buf)
                if p:
                    if p.start():
                        self.on_text(self.buf[:p.start()])
                        self.buf = self.buf[p.start():]
                    return

                keep = _safe_flush_len(self.buf, "<write_file")
                if keep:
                    self.on_text(self.buf[:keep])
                    self.buf = self.buf[keep:]
                return

            close_tag = f"</{self.tag}>"
            idx = self.buf.find(close_tag)
            if idx >= 0:
                head = self.buf[:idx]
                if head:
                    self.content += head
                    self.on_file_token(head)
                self.buf = self.buf[idx + len(close_tag):]

                self.on_file_end(self.path, _clean_streamed_file_body(self.content, self.path))
                self.mode, self.path, self.content = "text", None, ""
                continue

            keep = _safe_flush_len(self.buf, close_tag)
            if keep:
                part = self.buf[:keep]
                self.content += part
                self.on_file_token(part)
                self.buf = self.buf[keep:]
            return
