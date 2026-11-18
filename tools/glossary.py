"""The glossary, written once (this.i 4wnur86k): docs/glossary.toml is read here to write the
glossary page, docs/glossary.md, and to give the site assembler an abbreviation for every spelling
a term lists under `hover`, so the site shows each term's meaning on hover where it appears.

A test holds the committed page to what this module writes from the data, so the page a reader
browses on GitHub and the hover text on the site cannot say different things.
"""

import re
import sys
import tomllib
from pathlib import Path

from keri_bench.errors import BenchError

DATA = "docs/glossary.toml"
PAGE = "docs/glossary.md"
SCOPES = ("all", "reports")

E_GLOSSARY = "e.input.format.glossary.f"

_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")


def load(path) -> dict:
    """The glossary data at `path`, refused unless every term has a name and a meaning, a known
    scope, and hover spellings that are plain non-empty strings."""
    path = Path(path)
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise BenchError(E_GLOSSARY, f"{path} is not TOML ({exc}).") from None
    if not isinstance(data.get("intro"), str) or not data.get("section"):
        raise BenchError(E_GLOSSARY, f"{path} needs an intro and at least one section.")
    for section in data["section"]:
        for term in section.get("term", []):
            where = f"{path}, {section.get('title')!r}, {term.get('name')!r}"
            if not (isinstance(term.get("name"), str) and isinstance(term.get("meaning"), str)):
                raise BenchError(E_GLOSSARY, f"{where}: a term needs a name and a meaning.")
            if term.get("scope", "all") not in SCOPES:
                raise BenchError(E_GLOSSARY, f"{where}: scope must be one of {list(SCOPES)}.")
            hover = term.get("hover", [])
            if not (isinstance(hover, list) and all(isinstance(h, str) and h.strip()
                                                    for h in hover)):
                raise BenchError(E_GLOSSARY, f"{where}: hover must be a list of spellings.")
    return data


def render(data: dict) -> str:
    """The glossary page, one table per section."""
    lines = ["# Glossary", "", data["intro"], ""]
    for section in data["section"]:
        lines += [f"## {section['title']}", "", "| term | meaning |", "|---|---|"]
        for term in section.get("term", []):
            lines.append(f"| {term['name']} | {term['meaning']} |")
        lines.append("")
    lines += ["This page is written from `docs/glossary.toml` by `scripts/glossary`; edit that "
              "file, not this one.", ""]
    return "\n".join(lines)


def plain(text: str) -> str:
    """`text` as a hover title shows it: links reduced to their text, code marks and quotes
    dropped, and whitespace collapsed."""
    text = _LINK.sub(r"\1", text).replace("`", "").replace('"', "")
    return " ".join(text.split())


def abbreviations(data: dict, *, report: bool) -> list[str]:
    """One abbreviation definition per hover spelling, for a run report or for any other page.
    A spelling listed twice keeps its first meaning."""
    seen, out = set(), []
    for section in data["section"]:
        for term in section.get("term", []):
            if term.get("scope", "all") == "reports" and not report:
                continue
            for spelling in term.get("hover", []):
                if spelling not in seen:
                    seen.add(spelling)
                    out.append(f"*[{spelling}]: {plain(term['meaning'])}")
    return out


def main(argv: list[str] | None = None) -> int:
    """Write docs/glossary.md from docs/glossary.toml in the repository given as the only
    argument (default: this one)."""
    args = sys.argv[1:] if argv is None else argv
    repo = Path(args[0]) if args else Path(__file__).resolve().parent.parent
    try:
        page = render(load(repo / DATA))
    except BenchError as exc:
        print(f"scripts/glossary: {exc}", file=sys.stderr)
        return 1
    (repo / PAGE).write_text(page, encoding="utf-8")
    print(f"wrote {repo / PAGE}")
    return 0
