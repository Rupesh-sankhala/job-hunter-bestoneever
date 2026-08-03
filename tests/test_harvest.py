"""Parser tests against the synthetic fixtures.

The LaTeX parser is ground truth. The golden test asserts the font-aware PDF
parser reproduces it exactly for the same document -- that pair is what every
PDF-only variant's correctness rests on.

PDF tests skip unless `scripts/build_fixtures.sh` has been run (needs pdflatex).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from cvagent.harvest import (
    Dehyphenator,
    build_vocabulary,
    clean_text,
    corpus_dir,
    norm_key,
    parse_pdf,
    parse_tex,
    variant_name,
)

FIXTURES = corpus_dir()
MASTER_TEX = FIXTURES / "alex_master_2026.tex"
MASTER_PDF = MASTER_TEX.with_suffix(".pdf")
TEX_FILES = sorted(FIXTURES.rglob("*.tex"))

needs_pdf = pytest.mark.skipif(
    not MASTER_PDF.exists(), reason="run scripts/build_fixtures.sh (requires pdflatex)"
)


@pytest.fixture(scope="module")
def master() -> object:
    return parse_tex(MASTER_TEX, "master")


@pytest.fixture(scope="module")
def dehyph() -> Dehyphenator:
    return Dehyphenator(build_vocabulary(TEX_FILES))


# --------------------------------------------------------------------------
# LaTeX parsing
# --------------------------------------------------------------------------


def test_master_yields_records(master):
    assert len(master.projects) >= 8
    assert len(master.items) >= 15
    assert all(i.text.strip() for i in master.items)


def test_sections_are_classified(master):
    sections = {i.section for i in master.items}
    assert {"work", "personal", "achievements"} <= sections


def test_every_work_bullet_has_a_project_and_employer(master):
    for item in master.items:
        if item.section == "work":
            assert item.project, f"orphan bullet: {item.text[:60]}"
            assert item.employer, f"bullet with no employer: {item.text[:60]}"


def test_latex_escapes_are_decoded(master):
    text = " ".join(i.text for i in master.items)
    assert "\\%" not in text and "\\$" not in text and "\\&" not in text
    assert "%" in text and "$" in text
    assert "×" in text, "math \\times should decode to the glyph, not be dropped"


def test_variant_renames_are_preserved():
    """A role-specific variant renames sections; the parser must still classify."""
    variant = next(p for p in TEX_FILES if "product" in p.name)
    doc = parse_tex(variant, variant_name(variant))
    assert {i.section for i in doc.items} >= {"work", "personal"}
    headers = " ".join(doc.blocks.get("headers", []))
    assert "Research & Work Experience" in headers, "renamed heading should be recorded"


# --------------------------------------------------------------------------
# normalisation
# --------------------------------------------------------------------------


def test_norm_key_is_stable_across_symbol_variants():
    assert norm_key("cut volume 4× while retaining 75% recall") == norm_key(
        "cut volume 4x while retaining 75% recall"
    )
    assert norm_key("A  b—c") == norm_key("a b-c")


def test_clean_text_resolves_ligatures():
    assert clean_text("conﬁdence") == "confidence"


def test_dehyphenation_preserves_real_compounds(dehyph):
    assert dehyph.join("with Stream-", "lyzer tracing") == "with Streamlyzer tracing"
    assert dehyph.join("signal-", "revealing actions") == "signal-revealing actions"


def test_variant_names_disambiguate_generations():
    a = variant_name(Path("fixtures/2026_h1/alex_resume_legacy_2026.pdf"))
    b = variant_name(Path("fixtures/2026_h2/alex_resume_legacy_2026.pdf"))
    assert a == "legacy@h1" and b == "legacy@h2"


def test_master_at_corpus_root_is_named_master():
    assert variant_name(MASTER_TEX) == "master"


# --------------------------------------------------------------------------
# PDF parsing (golden test)
# --------------------------------------------------------------------------


def _pairs() -> list[tuple[Path, Path]]:
    """Every variant that has both a .tex source and a rendered .pdf.

    Matched on the base label, since a master source may sit outside the
    generation folders (`master`) while its render is inside one (`master@h2`).
    """
    def base(v: str) -> str:
        return v.split("@", 1)[0]

    pdfs = sorted(FIXTURES.rglob("*.pdf"))
    exact = {variant_name(p): p for p in pdfs}
    by_base: dict[str, Path] = {}
    for p in pdfs:
        by_base.setdefault(base(variant_name(p)), p)
    out = []
    for tex in TEX_FILES:
        v = variant_name(tex)
        if pdf := exact.get(v) or by_base.get(base(v)):
            out.append((tex, pdf))
    return out


PAIRS = _pairs()
pair_test = pytest.mark.parametrize(
    "tex,pdf", PAIRS, ids=lambda p: variant_name(p) if isinstance(p, Path) else ""
)

# Variants whose .tex has been edited since its .pdf was rendered. The parser is
# correct on these; the files genuinely differ. Re-render and delete the entry.
STALE_RENDERS: set[str] = set()


@needs_pdf
@pair_test
def test_pdf_structure_matches_latex(tex: Path, pdf: Path, dehyph):
    """Parser correctness: no bullet dropped, merged, or truncated.

    Holds even for a stale render -- rewording a CV does not change how many
    bullets it has.
    """
    truth = parse_tex(tex, variant_name(tex))
    got = parse_pdf(pdf, variant_name(pdf), dehyph)
    assert len({norm_key(i.text) for i in truth.items}) == len(
        {norm_key(i.text) for i in got.items}
    )
    assert {norm_key(p["title"]) for p in truth.projects} == {
        norm_key(p["title"]) for p in got.projects
    }


@needs_pdf
@pair_test
def test_pdf_bullets_match_latex(tex: Path, pdf: Path, dehyph):
    if variant_name(pdf) in STALE_RENDERS:
        pytest.skip(f"{pdf.name} is a stale render of {tex.name}")
    truth = parse_tex(tex, variant_name(tex))
    got = parse_pdf(pdf, variant_name(pdf), dehyph)
    a = {norm_key(i.text) for i in truth.items}
    b = {norm_key(i.text) for i in got.items}
    assert a == b, f"{tex.name}\n  tex-only={a - b!r}\n  pdf-only={b - a!r}"


@needs_pdf
def test_no_undeclared_stale_renders(dehyph):
    """Every .tex/.pdf divergence must be declared.

    This is what stops a real parser regression from being written off as
    corpus drift: an undeclared divergence fails here.
    """
    drifted = {
        variant_name(pdf)
        for tex, pdf in PAIRS
        if {norm_key(i.text) for i in parse_tex(tex, variant_name(tex)).items}
        != {norm_key(i.text) for i in parse_pdf(pdf, variant_name(pdf), dehyph).items}
    }
    assert drifted == STALE_RENDERS, f"undeclared drift: {drifted - STALE_RENDERS}"


@needs_pdf
@pair_test
def test_employer_entries_are_not_mistaken_for_projects(tex: Path, pdf: Path, dehyph):
    """Employer entries right-align dates onto a separate PDF line, so they are
    easily misread as project titles."""
    got = parse_pdf(pdf, variant_name(pdf), dehyph)
    titles = {norm_key(p["title"]) for p in got.projects}
    assert not any(t.startswith("senior data scientist") for t in titles)
    assert not any(t.startswith("data scientist ") for t in titles)


@needs_pdf
@pair_test
def test_wrapped_bullets_are_reassembled(tex: Path, pdf: Path, dehyph):
    """Without font-aware parsing, a wrapped bullet is truncated at the break."""
    got = parse_pdf(pdf, variant_name(pdf), dehyph)
    assert max(len(i.text) for i in got.items) > 120


@pytest.mark.parametrize("pdf_path", sorted(FIXTURES.rglob("*.pdf")))
def test_every_fixture_pdf_yields_structure(pdf_path, dehyph):
    """No variant may silently parse to nothing."""
    doc = parse_pdf(pdf_path, variant_name(pdf_path), dehyph)
    assert len(doc.items) >= 5, f"{pdf_path.name}: {len(doc.items)} bullets"
    assert len(doc.projects) >= 3, f"{pdf_path.name}: {len(doc.projects)} projects"
