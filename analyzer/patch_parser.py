"""Parse Git unified diffs as text, retaining uncertainty on unsupported input."""

from __future__ import annotations

import re

from .contracts import ChangeStatus, FileChange, safe_path

_HUNK = re.compile(r"^@@ -([0-9]{1,12})(?:,([0-9]{1,12}))? \+([0-9]{1,12})(?:,([0-9]{1,12}))? @@(?: .*)?$")
_QUOTED = r'"(?:\\.|[^"\\])*"'
_ESCAPE = re.compile(r'\\([0-7]{3}|[abfnrtv"\\]|.)')
_ESCAPES = dict(zip('abfnrtv"\\', '\a\b\f\n\r\t\v"\\'))
_NO_NEWLINE = r"\ No newline at end of file"


def _decode(raw: str) -> str:
    if not raw.startswith('"'):
        if '"' in raw:
            raise ValueError("unexpected quote")
        return raw
    if not re.fullmatch(_QUOTED, raw):
        raise ValueError("invalid quoted path")
    def unescape(match: re.Match[bytes]) -> bytes:
        value = match[1].decode("ascii")
        if len(value) == 3 and value.isdigit():
            return bytes([int(value, 8)])
        return _ESCAPES[value].encode("ascii")

    try:
        body = raw[1:-1].encode("utf-8")
        return re.sub(_ESCAPE.pattern.encode("ascii"), unescape, body).decode("utf-8")
    except (KeyError, UnicodeError) as exc:
        raise ValueError("invalid path escape") from exc


def _path(raw: str, prefix: str = "") -> str | None:
    value = _decode(raw)
    if value == "/dev/null" and prefix:
        return None
    if not value.startswith(prefix) or not safe_path(value[len(prefix):]):
        raise ValueError("unsafe or unsupported path")
    return value[len(prefix):]


def _git_paths(raw: str) -> tuple[str, str]:
    # Git writes whitespace literally in many diff headers. Leave ambiguous
    # pairs unresolved. The file's detailed headers can still be inspected.
    candidates = []
    for match in re.finditer(r' (?=b/|"b/)', raw):
        try:
            old = _path(raw[:match.start()], "a/")
            new = _path(raw[match.end():], "b/")
        except ValueError:
            continue
        if old is not None and new is not None:
            candidates.append((old, new))
    if len(candidates) != 1:
        raise ValueError("invalid or ambiguous Git header")
    return candidates[0]


def _unknown() -> FileChange:
    return {
        "path": "", "old_path": None, "new_path": None, "status": "unknown",
        "old_lines": [], "new_lines": [], "added_text": [], "removed_text": [],
        "uncertain": True,
    }


def _hunk(lines: list[str], index: int, result: FileChange,
          previous: tuple[int, int]) -> tuple[int, tuple[int, int]]:
    match = _HUNK.fullmatch(lines[index])
    if match is None:
        result["uncertain"] = True
        return index + 1, previous
    old, new = int(match[1]), int(match[3])
    old_left = int(match[2]) if match[2] is not None else 1
    new_left = int(match[4]) if match[4] is not None else 1
    old_start = old if old_left else old + 1
    new_start = new if new_left else new + 1
    if old_start < previous[0] or new_start < previous[1]:
        result["uncertain"] = True
    ends = old_start + old_left, new_start + new_left
    index += 1
    can_mark_no_newline = False
    while index < len(lines):
        line = lines[index]
        if line == _NO_NEWLINE and can_mark_no_newline:
            can_mark_no_newline = False
            index += 1
            continue
        if not (old_left or new_left):
            break
        tag = line[:1]
        if tag not in (" ", "+", "-"):
            break
        if (tag != "+" and not old_left) or (tag != "-" and not new_left):
            break
        if tag == "-":
            result["old_lines"].append(old)
            result["removed_text"].append(line[1:])
        elif tag == "+":
            result["new_lines"].append(new)
            result["added_text"].append(line[1:])
        if tag != "+":
            old += 1
            old_left -= 1
        if tag != "-":
            new += 1
            new_left -= 1
        can_mark_no_newline = True
        index += 1
    if old_left or new_left:
        result["uncertain"] = True
    return index, ends


def _file(lines: list[str]) -> FileChange:
    result = _unknown()
    result["uncertain"] = False
    git_header = lines[0].startswith("diff --git ")
    git_paths: tuple[str | None, str | None] = (None, None)
    if git_header:
        try:
            git_paths = _git_paths(lines[0][11:])
        except ValueError:
            result["uncertain"] = True
    headers: dict[str, str | None] = {}
    kinds: set[str] = set()
    seen_hunk = False
    previous = (1, 1)
    index = int(git_header)
    while index < len(lines):
        line = lines[index]
        if line.startswith("@@"):
            seen_hunk = True
            index, previous = _hunk(lines, index, result, previous)
            continue
        if line == "GIT binary patch" or line.startswith("Binary files "):
            kinds.add("binary")
            break
        key = next((prefix for prefix in ("--- ", "+++ ", "rename from ", "rename to ",
                                          "copy from ", "copy to ") if line.startswith(prefix)), None)
        if key is not None and not seen_hunk:
            raw = line[len(key):]
            prefix = {"--- ": "a/", "+++ ": "b/"}.get(key, "")
            if prefix:
                raw = raw.split("\t", 1)[0]
            try:
                if key in headers:
                    raise ValueError("repeated header")
                headers[key] = _path(raw, prefix)
            except ValueError:
                result["uncertain"] = True
        elif not seen_hunk and line.startswith(("new file mode ", "deleted file mode ")):
            kinds.add("added" if line.startswith("new") else "deleted")
            if not re.fullmatch(r"(?:new|deleted) file mode [0-7]{6}", line):
                result["uncertain"] = True
        elif not seen_hunk and re.fullmatch(
            r"(?:index [0-9a-f]+\.\.[0-9a-f]+(?: [0-7]{6})?"
            r"|(?:dis)?similarity index (?:100|[0-9]{1,2})%"
            r"|(?:old|new) mode [0-7]{6})", line
        ):
            if line.startswith(("old mode", "new mode")):
                result["uncertain"] = True
        else:
            result["uncertain"] = True
        index += 1

    old, new = git_paths
    for label, status in (("rename", "renamed"), ("copy", "copied")):
        pair = (f"{label} from ", f"{label} to ")
        if any(key in headers for key in pair):
            kinds.add(status)
            details = headers.get(pair[0]), headers.get(pair[1])
            if not all(details) or (git_paths != (None, None) and git_paths != details):
                result["uncertain"] = True
            old, new = details
    if "--- " in headers or "+++ " in headers:
        if "--- " not in headers or "+++ " not in headers:
            result["uncertain"] = True
        explicit_old, explicit_new = headers.get("--- "), headers.get("+++ ")
        if ((old and explicit_old and old != explicit_old)
                or (new and explicit_new and new != explicit_new)):
            result["uncertain"] = True
        old, new = explicit_old, explicit_new
        if old is None and new is not None:
            kinds.add("added")
        if new is None and old is not None:
            kinds.add("deleted")
    if "added" in kinds:
        if "--- " in headers and headers["--- "] is not None:
            result["uncertain"] = True
        old = None
    if "deleted" in kinds:
        if "+++ " in headers and headers["+++ "] is not None:
            result["uncertain"] = True
        new = None

    status: ChangeStatus = "modified"
    if "binary" in kinds:
        status = "binary"
        result["uncertain"] = True
    elif len(kinds) == 1:
        status = next(iter(kinds))
    elif len(kinds) > 1:
        status = "unknown"
        result["uncertain"] = True
    elif not seen_hunk or old != new:
        status = "unknown"
        result["uncertain"] = True
    if seen_hunk and not {"--- ", "+++ "} <= headers.keys():
        result["uncertain"] = True
    if not (old or new) or (status == "added" and result["old_lines"]) or (
        status == "deleted" and result["new_lines"]
    ):
        result["uncertain"] = True
    result.update(path=new or old or "", old_path=old, new_path=new, status=status)
    return result


def parse_patch(text: str) -> list[dict]:
    """Return FileChange dictionaries and preserve a signal for unknown input."""
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    lines = [line.removesuffix("\r") for line in lines]
    starts = [index for index, line in enumerate(lines) if line.startswith("diff --")]
    if not starts:
        return [_file(lines)] if lines and lines[0].startswith("--- ") else [_unknown()]
    result = [_unknown()] if any(lines[:starts[0]]) else []
    for start, end in zip(starts, starts[1:] + [len(lines)]):
        result.append(_file(lines[start:end]) if lines[start].startswith("diff --git ") else _unknown())
    return result
