#!/usr/bin/env python3
"""Structural parsing of shell command strings for the PreToolUse gate.

The gate only ever sees the text of a command. Matching that text with
regular expressions cannot tell *what a command executes*: wrappers such as
`bash -c '…'`, `$(…)`, `eval`, heredocs, quoting and shebang execution all
defeat pattern matching. This module reconstructs the parts of shell
grammar that matter for policy decisions so the gate can reason about each
simple command (a "segment") on its own:

- segments separated by `;`, `&&`, `||`, `|`, `&`, newlines and grouping
- words with quote removal, tracking which words contain runtime expansion
- output/input redirections, here-strings and heredoc bodies
- command / process substitutions (returned for recursive analysis)
- pipes (each segment records the segment feeding its stdin)

It deliberately does not execute or expand anything. Text produced at
runtime (`$VAR`, `$(…)`, `` `…` ``) is replaced by DYN in word text and the
word is flagged `dynamic`, so policy code can refuse to trust it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import re

# Placeholder for any runtime-expanded text inside a word.
DYN = "\x00"

_NAME_START = re.compile(r"[A-Za-z_]")
_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*\+?=")
_SHELL_KEYWORDS_PREFIX = frozenset(
    {"if", "then", "else", "elif", "do", "while", "until", "!", "time"}
)
_SHELL_KEYWORDS_SKIP = frozenset({"fi", "done", "esac", "for", "case", "select", "function", "in"})


class ShellParseError(ValueError):
    """Raised when a command cannot be parsed (unbalanced quotes, etc.)."""


@dataclass
class Word:
    text: str
    dynamic: bool = False
    globbed: bool = False

    @property
    def literal(self) -> bool:
        return not self.dynamic


@dataclass
class Segment:
    words: list[Word] = field(default_factory=list)
    redirects_out: list[Word] = field(default_factory=list)
    redirect_appends: list[bool] = field(default_factory=list)  # parallel to redirects_out
    redirects_in: list[Word] = field(default_factory=list)
    stdin_texts: list[str] = field(default_factory=list)
    stdin_dynamic: bool = False
    piped_from: Segment | None = None

    @property
    def assignments(self) -> list[Word]:
        out = []
        for w in self.words:
            if _ASSIGNMENT.match(w.text):
                out.append(w)
            else:
                break
        return out

    @property
    def argv(self) -> list[Word]:
        """Words after leading `NAME=value` assignments and shell keywords."""
        words = self.words[len(self.assignments) :]
        while words and words[0].literal and words[0].text in _SHELL_KEYWORDS_PREFIX:
            words = words[1:]
        if words and words[0].literal and words[0].text in _SHELL_KEYWORDS_SKIP:
            return []
        return words


@dataclass
class ParsedCommand:
    segments: list[Segment]
    substitutions: list[str]


class _Lexer:
    def __init__(self, text: str) -> None:
        self.s = text
        self.i = 0
        self.n = len(text)
        self.segments: list[Segment] = []
        self.substitutions: list[str] = []
        self.seg = Segment()
        self.buf: list[str] = []
        self.word_started = False
        self.word_dynamic = False
        self.word_glob = False
        self.word_quoted = False
        self.pending_redirect: str | None = None  # "out", "in", "herestring", "heredoc"
        self.pending_append = False
        self.pending_heredocs: list[tuple[Segment, str, bool, bool]] = []
        self.pipe_next = False

    # ------------------------------------------------------------------ words
    def _end_word(self) -> None:
        if not self.word_started:
            return
        word = Word("".join(self.buf), self.word_dynamic, self.word_glob)
        bare = not self.word_quoted and not self.word_dynamic
        self.buf = []
        self.word_started = False
        self.word_dynamic = False
        self.word_glob = False
        self.word_quoted = False

        pending = self.pending_redirect
        self.pending_redirect = None
        if pending == "out":
            self.seg.redirects_out.append(word)
            self.seg.redirect_appends.append(self.pending_append)
        elif pending == "in":
            self.seg.redirects_in.append(word)
        elif pending == "herestring":
            self.seg.stdin_texts.append(word.text)
            self.seg.stdin_dynamic = self.seg.stdin_dynamic or word.dynamic
        elif pending == "heredoc":
            delim = word.text
            strip_tabs = delim.startswith("-")
            if strip_tabs:
                delim = delim[1:]
            self.pending_heredocs.append((self.seg, delim, strip_tabs, bare))
        elif bare and word.text in ("{", "}"):
            self._end_segment()
        else:
            self.seg.words.append(word)

    def _add(self, ch: str) -> None:
        self.buf.append(ch)
        self.word_started = True

    def _end_segment(self, piped: bool = False) -> None:
        self._end_word()
        if self.seg.words or self.seg.redirects_out or self.seg.redirects_in:
            if self.pipe_next and self.segments:
                self.seg.piped_from = self.segments[-1]
            self.segments.append(self.seg)
        self.seg = Segment()
        self.pipe_next = piped

    # ------------------------------------------------------------- readers
    def _read_balanced(self, open_ch: str, close_ch: str) -> str:
        """Reads from after an opening bracket to its match, quote-aware."""
        depth = 1
        start = self.i
        while self.i < self.n:
            ch = self.s[self.i]
            if ch == "\\":
                self.i += 2
                continue
            if ch == "'":
                end = self.s.find("'", self.i + 1)
                if end < 0:
                    raise ShellParseError("unterminated single quote in substitution")
                self.i = end + 1
                continue
            if ch == '"':
                self.i += 1
                while self.i < self.n and self.s[self.i] != '"':
                    self.i += 2 if self.s[self.i] == "\\" else 1
                if self.i >= self.n:
                    raise ShellParseError("unterminated double quote in substitution")
                self.i += 1
                continue
            if ch == open_ch:
                depth += 1
            elif ch == close_ch:
                depth -= 1
                if depth == 0:
                    inner = self.s[start : self.i]
                    self.i += 1
                    return inner
            self.i += 1
        raise ShellParseError(f"unbalanced '{open_ch}'")

    def _read_backtick(self) -> str:
        start = self.i
        while self.i < self.n:
            ch = self.s[self.i]
            if ch == "\\":
                self.i += 2
                continue
            if ch == "`":
                inner = self.s[start : self.i]
                self.i += 1
                return inner.replace("\\`", "`")
            self.i += 1
        raise ShellParseError("unterminated backtick")

    def _dollar(self) -> None:
        """Handles `$` at self.i (already known to be `$`)."""
        nxt = self.s[self.i + 1] if self.i + 1 < self.n else ""
        if nxt == "(":
            self.i += 2
            if self.i < self.n and self.s[self.i] == "(":
                self.i += 1
                inner = self._read_balanced("(", ")")
                if self.i < self.n and self.s[self.i] == ")":
                    self.i += 1
            else:
                inner = self._read_balanced("(", ")")
            self.substitutions.append(inner)
            self._add(DYN)
            self.word_dynamic = True
        elif nxt == "{":
            self.i += 2
            inner = self._read_balanced("{", "}")
            if "$(" in inner or "`" in inner:
                self.substitutions.append(inner)
            self._add(DYN)
            self.word_dynamic = True
        elif nxt and (_NAME_START.match(nxt) or nxt.isdigit() or nxt in "@*#?$!-"):
            self.i += 1
            if _NAME_START.match(nxt):
                while self.i < self.n and (self.s[self.i].isalnum() or self.s[self.i] == "_"):
                    self.i += 1
            else:
                self.i += 1
            self._add(DYN)
            self.word_dynamic = True
        elif nxt == "'":
            self.i += 2
            self._ansi_c_quote()
        elif nxt == '"':
            self.i += 1
            self._double_quote()
        else:
            self._add("$")
            self.i += 1

    def _ansi_c_quote(self) -> None:
        escapes = {
            "n": "\n",
            "t": "\t",
            "r": "\r",
            "\\": "\\",
            "'": "'",
            '"': '"',
            "a": "\a",
            "e": "\x1b",
        }
        self.word_quoted = True
        self.word_started = True
        while self.i < self.n:
            ch = self.s[self.i]
            if ch == "\\" and self.i + 1 < self.n:
                esc = self.s[self.i + 1]
                if esc == "x":
                    m = re.match(r"[0-9a-fA-F]{1,2}", self.s[self.i + 2 :])
                    if m:
                        self.buf.append(chr(int(m.group(0), 16)))
                        self.i += 2 + len(m.group(0))
                        continue
                if esc in "01234567":
                    m = re.match(r"[0-7]{1,3}", self.s[self.i + 1 :])
                    self.buf.append(chr(int(m.group(0), 8)))
                    self.i += 1 + len(m.group(0))
                    continue
                self.buf.append(escapes.get(esc, "\\" + esc))
                self.i += 2
                continue
            if ch == "'":
                self.i += 1
                return
            self.buf.append(ch)
            self.i += 1
        raise ShellParseError("unterminated $'...' quote")

    def _double_quote(self) -> None:
        # self.i points at the opening quote
        self.i += 1
        self.word_quoted = True
        self.word_started = True
        while self.i < self.n:
            ch = self.s[self.i]
            if ch == '"':
                self.i += 1
                return
            if ch == "\\" and self.i + 1 < self.n:
                nxt = self.s[self.i + 1]
                if nxt in '"\\$`':
                    self.buf.append(nxt)
                    self.i += 2
                    continue
                if nxt == "\n":
                    self.i += 2
                    continue
                self.buf.append(ch)
                self.i += 1
                continue
            if ch == "$":
                self._dollar()
                continue
            if ch == "`":
                self.i += 1
                self.substitutions.append(self._read_backtick())
                self._add(DYN)
                self.word_dynamic = True
                continue
            self.buf.append(ch)
            self.i += 1
        raise ShellParseError("unterminated double quote")

    def _read_heredoc_bodies(self) -> None:
        """Consumes heredoc bodies following the newline at self.i - 1."""
        for seg, delim, strip_tabs, expands in self.pending_heredocs:
            lines: list[str] = []
            found = False
            while self.i < self.n:
                end = self.s.find("\n", self.i)
                line = self.s[self.i :] if end < 0 else self.s[self.i : end]
                self.i = self.n if end < 0 else end + 1
                check = line.lstrip("\t") if strip_tabs else line
                if check == delim:
                    found = True
                    break
                lines.append(check if strip_tabs else line)
            body = "\n".join(lines)
            if not found and not lines:
                raise ShellParseError(f"heredoc '{delim}' has no body")
            seg.stdin_texts.append(body)
            if expands and ("$" in body or "`" in body):
                seg.stdin_dynamic = True
                # Expansions inside an unquoted heredoc run at runtime.
                for m in re.finditer(r"\$\(", body):
                    sub = _Lexer(body[m.start() :])
                    sub.i = 2
                    try:
                        self.substitutions.append(sub._read_balanced("(", ")"))
                    except ShellParseError:
                        pass
                for m in re.finditer(r"`([^`]*)`", body):
                    self.substitutions.append(m.group(1))
        self.pending_heredocs = []

    # ----------------------------------------------------------------- main
    def parse(self) -> ParsedCommand:
        while self.i < self.n:
            ch = self.s[self.i]
            nxt = self.s[self.i + 1] if self.i + 1 < self.n else ""

            if ch in " \t":
                self._end_word()
                self.i += 1
            elif ch == "\n":
                self._end_segment()
                self.i += 1
                if self.pending_heredocs:
                    self._read_heredoc_bodies()
            elif ch == "#" and not self.word_started:
                end = self.s.find("\n", self.i)
                self.i = self.n if end < 0 else end
            elif ch == "\\":
                if nxt == "\n":
                    self.i += 2
                else:
                    if nxt:
                        self._add(nxt)
                        self.word_quoted = True
                    self.i += 2
            elif ch == "'":
                end = self.s.find("'", self.i + 1)
                if end < 0:
                    raise ShellParseError("unterminated single quote")
                self.buf.append(self.s[self.i + 1 : end])
                self.word_started = True
                self.word_quoted = True
                self.i = end + 1
            elif ch == '"':
                self._double_quote()
            elif ch == "$":
                self._dollar()
            elif ch == "`":
                self.i += 1
                self.substitutions.append(self._read_backtick())
                self._add(DYN)
                self.word_dynamic = True
            elif ch in "<>" and nxt == "(" and not self.word_started:
                self.i += 2
                self.substitutions.append(self._read_balanced("(", ")"))
                self._add(DYN)
                self.word_dynamic = True
            elif ch in "<>" or (ch == "&" and nxt == ">"):
                self._redirect()
            elif ch == ";":
                self._end_segment()
                self.i += 2 if nxt == ";" else 1
            elif ch == "|":
                if nxt == "|":
                    self._end_segment()
                    self.i += 2
                else:
                    self._end_segment(piped=True)
                    self.i += 2 if nxt == "&" else 1
            elif ch == "&":
                self._end_segment()
                self.i += 2 if nxt == "&" else 1
            elif ch in "()":
                self._end_segment()
                self.i += 1
            else:
                if ch in "*?[":
                    self.word_glob = True
                elif ch == "{":
                    close = self.s.find("}", self.i)
                    if close > 0 and "," in self.s[self.i : close]:
                        self.word_glob = True
                self._add(ch)
                self.i += 1

        if (
            self.pending_redirect in ("out", "in", "herestring", "heredoc")
            and not self.word_started
        ):
            raise ShellParseError("redirection without a target")
        self._end_segment()
        if self.pending_heredocs:
            raise ShellParseError("heredoc without a body")
        return ParsedCommand(self.segments, self.substitutions)

    def _redirect(self) -> None:
        # A word made only of digits immediately before the operator is an fd.
        if self.word_started and "".join(self.buf).isdigit() and not self.word_quoted:
            self.buf = []
            self.word_started = False
        else:
            self._end_word()
        s, i = self.s, self.i
        self.pending_append = s.startswith(">>", i) or s.startswith("&>>", i)
        if s.startswith("<<<", i):
            self.pending_redirect = "herestring"
            self.i += 3
        elif s.startswith("<<", i):
            self.pending_redirect = "heredoc"
            self.i += 2
            if self.i < self.n and s[self.i] == "-":
                self.buf.append("-")
                self.word_started = True
                self.i += 1
        elif s.startswith("&>>", i) or s.startswith("&>", i):
            self.pending_redirect = "out"
            self.i += 3 if s.startswith("&>>", i) else 2
        elif s.startswith(">&", i) or s.startswith("<&", i):
            self.i += 2
            m = re.match(r"\s*(\d+|-)", s[self.i :])
            if m:
                self.i += len(m.group(0))
            else:
                self.pending_redirect = "out"
        elif s.startswith(">>", i) or s.startswith(">|", i):
            self.pending_redirect = "out"
            self.i += 2
        elif s.startswith("<>", i):
            self.pending_redirect = "out"
            self.i += 2
        elif s[i] == ">":
            self.pending_redirect = "out"
            self.i += 1
        else:
            self.pending_redirect = "in"
            self.i += 1


def parse(text: str) -> ParsedCommand:
    """Parses a shell command string. Raises ShellParseError when malformed."""
    return _Lexer(text).parse()


def expand_braces(text: str, limit: int = 64) -> list[str]:
    """Expands simple `{a,b}` brace groups (enough to resolve evasion attempts)."""
    m = re.search(r"\{([^{}]*,[^{}]*)\}", text)
    if not m:
        return [text]
    out: list[str] = []
    for part in m.group(1).split(","):
        out.extend(expand_braces(text[: m.start()] + part + text[m.end() :], limit))
        if len(out) >= limit:
            break
    return out[:limit]
