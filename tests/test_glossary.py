"""The glossary, written once (tools/glossary.py, this.i 4wnur86k)."""

from pathlib import Path

import pytest

from keri_bench.errors import BenchError
from tools import glossary, site_source

REPO = Path(__file__).resolve().parent.parent

DATA = '''intro = "Words."

[[section]]
title = "One"

[[section.term]]
name = "KEL"
hover = ["KEL", "KELs"]
meaning = "Key event log; see [the spec](https://example.com/s) and `code`."

[[section.term]]
name = "`verified`"
hover = ["verified"]
scope = "reports"
meaning = "The verifier said \\"valid\\"."

[[section]]
title = "Two"

[[section.term]]
name = "Again"
hover = ["KEL"]
meaning = "A second meaning for a spelling already defined."

[[section.term]]
name = "Plain"
meaning = "No hover."
'''


def write(tmp_path, text=DATA) -> Path:
    path = tmp_path / "glossary.toml"
    path.write_text(text)
    return path


def test_the_committed_glossary_page_is_what_the_data_writes():
    """docs/glossary.md is generated; edit docs/glossary.toml and run scripts/glossary."""
    data = glossary.load(REPO / glossary.DATA)
    assert (REPO / glossary.PAGE).read_text() == glossary.render(data)


def test_the_page_has_one_table_per_section(tmp_path):
    page = glossary.render(glossary.load(write(tmp_path)))
    assert page.startswith("# Glossary\n\nWords.\n\n## One\n\n| term | meaning |\n|---|---|\n")
    assert "| `verified` | The verifier said \"valid\". |\n\n## Two" in page
    assert page.endswith("edit that file, not this one.\n")


def test_hover_text_is_plain_and_scoped(tmp_path):
    data = glossary.load(write(tmp_path))
    assert glossary.abbreviations(data, report=False) == [
        "*[KEL]: Key event log; see the spec and code.",
        "*[KELs]: Key event log; see the spec and code."]
    assert glossary.abbreviations(data, report=True)[-1] == "*[verified]: The verifier said valid."


@pytest.mark.parametrize("text, problem", [
    ("not = [toml", "is not TOML"),
    ('[[section]]\ntitle = "x"\n', "needs an intro"),
    ('intro = "i"\n', "needs an intro"),
    ('intro = "i"\nsection = "x"\n', "array of tables"),
    ('intro = "i"\n[[section]]\n[[section.term]]\nname = "n"\nmeaning = "m"\n', "needs a title"),
    ('intro = "i"\n[[section]]\ntitle = " "\n', "needs a title"),
    ('intro = "i"\n[[section]]\ntitle = "x"\n[[section.term]]\nname = "n"\n', "name and a meaning"),
    ('intro = "i"\n[[section]]\ntitle = "x"\n[[section.term]]\nname = "n"\nmeaning = "m"\n'
     'scope = "some"\n', "scope must be"),
    ('intro = "i"\n[[section]]\ntitle = "x"\n[[section.term]]\nname = "n"\nmeaning = "m"\n'
     'hover = "n"\n', "hover must be"),
    ('intro = "i"\n[[section]]\ntitle = "x"\n[[section.term]]\nname = "n"\nmeaning = "m"\n'
     'hover = [" "]\n', "hover must be"),
])
def test_damaged_glossary_data_is_refused_with_a_code(tmp_path, text, problem):
    with pytest.raises(BenchError) as err:
        glossary.load(write(tmp_path, text))
    assert err.value.code == glossary.E_GLOSSARY and problem in err.value.message


def test_main_writes_the_page_and_reports_a_refusal(tmp_path, capsys):
    (tmp_path / "docs").mkdir()
    (tmp_path / glossary.DATA).write_text(DATA)
    assert glossary.main([str(tmp_path)]) == 0
    assert (tmp_path / glossary.PAGE).read_text().startswith("# Glossary")
    assert str(tmp_path / glossary.PAGE) in capsys.readouterr().out
    (tmp_path / glossary.DATA).write_text("intro = 1\n")
    assert glossary.main([str(tmp_path)]) == 1
    assert capsys.readouterr().err.startswith("scripts/glossary: ")


def test_main_defaults_to_this_repository(monkeypatch, tmp_path):
    written = {}
    monkeypatch.setattr(Path, "write_text", lambda self, text, **kw: written.update({self: text}))
    monkeypatch.setattr("sys.argv", ["scripts/glossary"])
    assert glossary.main() == 0
    assert written == {REPO / glossary.PAGE: (REPO / glossary.PAGE).read_text()}


def test_the_site_shows_the_glossary_on_hover_and_does_not_publish_its_data(tmp_path):
    repo = tmp_path / "repo"
    (repo / "docs").mkdir(parents=True)
    (repo / "results" / "run-a").mkdir(parents=True)
    (repo / "README.md").write_text("# Home\n\nA KEL that is verified.\n")
    (repo / glossary.DATA).write_text(DATA)
    (repo / "results" / "run-a" / "report.md").write_text("# keri-bench report\n\nverified\n")
    out = tmp_path / "out"
    site_source.assemble(repo, out)
    assert not (out / "glossary.toml").exists()
    home = (out / "index.md").read_text()
    assert home.endswith("\n\n*[KEL]: Key event log; see the spec and code.\n"
                         "*[KELs]: Key event log; see the spec and code.\n")
    assert "*[verified]" not in home
    assert "*[verified]: The verifier said valid." in (out / "results/run-a/report.md").read_text()
    site_source.check_markup(home, "README.md")
