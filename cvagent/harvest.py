"""Harvest CV corpus (PDF + LaTeX) into a union inventory of distinct bullets.

Build step 1. Role-specific CV variants are NOT subsets of the master -- each is
hand-rewritten and carries material the master lacks -- so the bank must be built
from the union of every rendered CV, not from the master source alone.

LaTeX sources are ground truth where they exist (no wrapping, no hyphenation).
For PDF-only variants we recover structure from font weight: CV templates set
section headers, employer entries and project titles in a bold face and body
text in a regular one, so boldness separates a project title from the wrapped
continuation lines of a bullet -- which plain text extraction cannot do.

Output: data/harvest/inventory.json
"""

from __future__ import annotations

import json
import os
import re
import unicodedata
from dataclasses import dataclass, field, asdict
from pathlib import Path

from pdfminer.high_level import extract_pages
from pdfminer.layout import LTChar, LTTextContainer, LTTextLine

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "harvest"


def corpus_dir() -> Path:
    """Where the CV corpus lives.

    Set CVAGENT_CORPUS to point at a private corpus; otherwise fall back to the
    bundled synthetic fixtures. No personal path is baked into the code.
    """
    if env := os.environ.get("CVAGENT_CORPUS"):
        return Path(env).expanduser().resolve()
    for candidate in (ROOT / "fixtures", ROOT.parent / "resumes"):
        if candidate.exists():
            return candidate
    return ROOT / "fixtures"


RESUMES = corpus_dir()


def excluded_globs() -> list[str]:
    """Corpus paths to skip, e.g. retired CV generations.

    Comma-separated globs in CVAGENT_EXCLUDE, matched against the path relative
    to the corpus root. Retiring a generation this way keeps the files on disk
    and the decision visible, rather than depending on a silent deletion.
    """
    raw = os.environ.get("CVAGENT_EXCLUDE", "")
    return [g.strip() for g in raw.split(",") if g.strip()]


def is_excluded(path: Path, corpus: Path) -> bool:
    try:
        rel = path.relative_to(corpus)
    except ValueError:
        return False
    return any(rel.match(g) or any(p.match(g) for p in rel.parents) for g in excluded_globs())

SECTION_PATTERNS = [
    (re.compile(r"^summary$", re.I), "summary"),
    # Section names are themselves angle-dependent -- a research-angle CV renames
    # these to "RESEARCH & WORK EXPERIENCE" / "PATENTS, PUBLICATIONS & RECOGNITION".
    (re.compile(r"^(research\s*(&|and)\s*)?work\s+experience$", re.I), "work"),
    (re.compile(r"^(selected\s+)?.*projects?\s*\(personal\)$", re.I), "personal"),
    (re.compile(r"^education$", re.I), "education"),
    (re.compile(r"^(patents,?\s*)?publications?\s*(&|and)\s*(achievements?|recognition)$", re.I),
     "achievements"),
    (re.compile(r"^(technical\s+)?skills$", re.I), "skills"),
]

# Filename prefix identifying the CV owner, stripped from variant labels.
# Override with CVAGENT_NAME_PREFIX when filenames use a different convention.
NAME_PREFIX_RE = re.compile(
    os.environ.get("CVAGENT_NAME_PREFIX", r"^[a-z]+_(resume|master|cv)_?"), re.I
)
# Corpus generation folder, e.g. "2026_h1" -> "h1".
GENERATION_RE = re.compile(r"(?:19|20)\d{2}_(h[12])$", re.I)

DATE_RE = re.compile(
    r"(?:\w{3,9}\.?\s+\d{4}|\d{4})\s*[–—-]+\s*(?:Present|\w{3,9}\.?\s+\d{4}|\d{4})\s*$"
)
BULLET_GLYPHS = "•●▪·\u2022\u25aa"


# Templates differ in font family, so match on the weight suffix rather than the
# family name: e.g. NimbusRomNo9L-Medi (serif) and CMSSBX10 (Computer Modern Sans).
BOLD_FONT_RE = re.compile(r"Medi|Bold|BX\d|Semibold", re.I)
ITALIC_FONT_RE = re.compile(r"Ital|CMSSI|CMTI|Oblique", re.I)

# Some templates set project titles in regular weight, marked by a trailing colon.
COLON_TITLE_RE = re.compile(r"^[A-Z][^.]{4,90}:$")

# A right-aligned, date-only line belongs to the entry above it, not to content.
DATE_ONLY_RE = re.compile(
    r"^(?:\w{3,9}\.?\s+\d{4}|\d{4})\s*[–—-]+\s*(?:Present|\w{3,9}\.?\s+\d{4}|\d{4})$"
)


@dataclass
class Item:
    source: str
    variant: str
    section: str
    employer: str | None
    project: str | None
    text: str
    order: int


@dataclass
class Document:
    source: str
    variant: str
    items: list[Item] = field(default_factory=list)
    blocks: dict[str, list[str]] = field(default_factory=dict)
    projects: list[dict] = field(default_factory=list)


# --------------------------------------------------------------------------
# normalisation
# --------------------------------------------------------------------------

LIGATURES = {"ﬁ": "fi", "ﬂ": "fl", "ﬀ": "ff", "ﬃ": "ffi", "ﬄ": "ffl"}
SYMBOLS = {"\u2013": "-", "\u2014": "-", "\u2019": "'", "\u201c": '"', "\u201d": '"',
           "\u00d7": "x", "\u223c": "~", "\u2192": "->", "\u00a0": " "}


def clean_text(s: str) -> str:
    s = unicodedata.normalize("NFKC", s)
    for a, b in LIGATURES.items():
        s = s.replace(a, b)
    return re.sub(r"[ \t]+", " ", s.replace("\u00a0", " ")).strip()


def norm_key(s: str) -> str:
    """Aggressive normalisation for dedup/matching only -- never for output."""
    s = clean_text(s).lower()
    for a, b in SYMBOLS.items():
        s = s.replace(a, b)
    s = re.sub(r"[^\w\s%$+./]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


class Dehyphenator:
    """Undo LaTeX line-break hyphenation without destroying real compounds.

    A trailing hyphen before a line break is ambiguous: 'Lang-\\nfuse' must rejoin
    to 'Langfuse', but 'tactic-\\nrevealing' must keep its hyphen. The .tex sources
    hold the unhyphenated ground truth, so we join only when the merged form is a
    token actually observed in a .tex file.
    """

    def __init__(self, vocabulary: set[str]) -> None:
        self.vocab = vocabulary

    def join(self, left: str, right: str) -> str:
        head = left.rstrip("-").split()[-1].lower() if left.rstrip("-").split() else ""
        tail = right.split()[0].lower() if right.split() else ""
        merged = re.sub(r"[^\w]", "", head + tail)
        hyphened = re.sub(r"[^\w-]", "", f"{head}-{tail}")
        if merged in self.vocab and hyphened not in self.vocab:
            return left.rstrip("-") + right
        return left + right


def build_vocabulary(tex_files: list[Path]) -> set[str]:
    vocab: set[str] = set()
    for p in tex_files:
        for tok in re.findall(r"[A-Za-z][A-Za-z\-]*", strip_tex(p.read_text(encoding="utf-8"))):
            vocab.add(tok.lower())
    return vocab


# --------------------------------------------------------------------------
# LaTeX parsing (ground truth)
# --------------------------------------------------------------------------

TEX_STRIP = [
    (re.compile(r"(?<!\\)%.*$", re.M), ""),
    (re.compile(r"\\(?:textbf|textit|emph|texttt)\{(.*?)\}"), r"\1"),
    # Keep the true glyphs so the .tex and .pdf paths agree; the renderer maps back.
    (re.compile(r"\$\\times\$"), "×"), (re.compile(r"\$\\rightarrow\$"), "→"),
    (re.compile(r"\$\\sim\$"), "~"), (re.compile(r"\$\|\$"), "|"),
    (re.compile(r"\\&"), "&"), (re.compile(r"\\%"), "%"), (re.compile(r"\\\$"), "$"),
    (re.compile(r"\\#"), "#"), (re.compile(r"\\_"), "_"), (re.compile(r"\\ "), " "),
    (re.compile(r"\\[a-zA-Z]+\*?(\[[^\]]*\])?"), " "),
    (re.compile(r"[{}~]"), ""), (re.compile(r"[ \t]+"), " "),
]


def strip_tex(s: str) -> str:
    for pat, rep in TEX_STRIP:
        s = pat.sub(rep, s)
    return s


def parse_tex(path: Path, variant: str) -> Document:
    raw = path.read_text(encoding="utf-8")
    doc = Document(source=path.name, variant=variant)
    section, employer, project, order = "header", None, None, 0

    sec_re = re.compile(r"\\section\{(.+?)\}")
    entry_re = re.compile(r"\\entry\{(.+?)\}\{(.+?)\}")
    proj_re = re.compile(r"\\proj\{(.+?)\}")
    item_re = re.compile(r"\\item\s+(.+?)$")

    for line in raw.splitlines():
        if m := sec_re.search(line):
            name = strip_tex(m.group(1)).strip()
            section = next((k for p, k in SECTION_PATTERNS if p.match(name)), name.lower())
            employer = project = None
            doc.blocks.setdefault("headers", []).append(f"{section}={name}")
        elif m := entry_re.search(line):
            employer, project = clean_text(strip_tex(m.group(1))), None
            if section == "education":
                doc.blocks.setdefault("education", []).append(
                    f"{employer} | {clean_text(strip_tex(m.group(2)))}")
        elif m := proj_re.search(line):
            project = clean_text(strip_tex(m.group(1)))
            doc.projects.append({"section": section, "employer": employer, "title": project})
        elif m := item_re.search(line):
            if text := clean_text(strip_tex(m.group(1))):
                order += 1
                doc.items.append(Item(path.name, variant, section, employer, project, text, order))
        elif section in {"summary", "skills"}:
            if (text := clean_text(strip_tex(line))) and not line.lstrip().startswith("\\"):
                doc.blocks.setdefault(section, []).append(text)

    for key in ("summary", "skills"):
        if key in doc.blocks:
            doc.blocks[key] = [" ".join(doc.blocks[key])] if key == "summary" else doc.blocks[key]
    return doc


# --------------------------------------------------------------------------
# PDF parsing (font-weight aware)
# --------------------------------------------------------------------------


@dataclass
class Line:
    text: str
    bold: float
    italic: float
    x0: float
    top: float
    bullet: bool


def read_lines(path: Path) -> list[Line]:
    out: list[Line] = []
    for page_no, page in enumerate(extract_pages(str(path))):
        for element in page:
            if not isinstance(element, LTTextContainer):
                continue
            for ln in element:
                if not isinstance(ln, LTTextLine):
                    continue
                chars = [c for c in ln if isinstance(c, LTChar)]
                if not chars:
                    continue
                text = clean_text(ln.get_text())
                if not text:
                    continue
                body = [c for c in chars if c.get_text().strip()]
                if not body:
                    continue
                bold = sum(bool(BOLD_FONT_RE.search(c.fontname)) for c in body) / len(body)
                ital = sum(bool(ITALIC_FONT_RE.search(c.fontname)) for c in body) / len(body)
                bullet = text[0] in BULLET_GLYPHS or "CMSY" in body[0].fontname and text[0] not in "$~"
                out.append(Line(text, bold, ital, ln.x0, -(page_no * 10_000 + (-ln.y1)), bullet))
    out.sort(key=lambda l: (-l.top, l.x0))
    return out


def parse_pdf(path: Path, variant: str, dehyph: Dehyphenator) -> Document:
    doc = Document(source=path.name, variant=variant)
    lines = read_lines(path)
    body_x0 = min((l.x0 for l in lines), default=0.0)

    # \entry{}{} right-aligns its dates with \hfill, which pdfminer emits as a
    # separate line on the same baseline. A bold left-margin line sharing a
    # baseline with a date is an employer entry, not a project title.
    date_rows = [l.top for l in lines if DATE_ONLY_RE.match(l.text) and l.x0 > body_x0 + 100]

    def has_date_on_row(top: float) -> bool:
        return any(abs(top - row) < 3.0 for row in date_rows)

    section, employer, project, order = "header", None, None, 0
    pending: list[str] | None = None
    pending_ctx: tuple[str, str | None, str | None] | None = None

    def flush() -> None:
        nonlocal pending, pending_ctx, order
        if pending and pending_ctx:
            text = pending[0]
            for nxt in pending[1:]:
                text = dehyph.join(text, nxt) if text.endswith("-") else f"{text} {nxt}"
            if text := re.sub(r"\s+", " ", text).strip():
                order += 1
                sec, emp, proj = pending_ctx
                doc.items.append(Item(path.name, variant, sec, emp, proj, text, order))
        pending = pending_ctx = None

    for ln in lines:
        text = ln.text
        stripped = text.lstrip("".join(BULLET_GLYPHS) + " ")

        # 1. section header
        if ln.bold > 0.5 and (key := next((k for p, k in SECTION_PATTERNS if p.match(text)), None)):
            flush()
            section, employer, project = key, None, None
            doc.blocks.setdefault("headers", []).append(f"{key}={text}")
            continue

        # 2. bullet
        if ln.bullet:
            flush()
            pending, pending_ctx = [stripped.strip()], (section, employer, project)
            continue

        # 3. right-aligned date-only line: metadata for the entry above, not content
        if DATE_ONLY_RE.match(text) and ln.x0 > body_x0 + 100:
            continue

        at_margin = ln.x0 <= body_x0 + 2.0

        # 4. bold line at the left margin = employer entry or project title
        if ln.bold > 0.4 and at_margin:
            flush()
            if DATE_RE.search(text) or has_date_on_row(ln.top) or section == "education":
                employer, project = clean_text(DATE_RE.sub("", text)).rstrip("|").strip(), None
                if section == "education":
                    doc.blocks.setdefault("education", []).append(text)
            else:
                project = text
                doc.projects.append({"section": section, "employer": employer, "title": project})
            continue

        # 5. h1 template: project titles are regular weight, marked by a trailing colon
        if at_margin and section in {"work", "personal"} and COLON_TITLE_RE.match(text):
            flush()
            project = text.rstrip(":").strip()
            doc.projects.append({"section": section, "employer": employer, "title": project})
            continue

        # 4. everything else: bullet continuation, or block prose
        if pending is not None:
            pending.append(text)
        elif section in {"summary", "skills", "header", "education"}:
            doc.blocks.setdefault(section, []).append(text)

    flush()

    for key in ("summary", "header"):
        if key in doc.blocks:
            joined = doc.blocks[key][0]
            for nxt in doc.blocks[key][1:]:
                joined = dehyph.join(joined, nxt) if joined.endswith("-") else f"{joined} {nxt}"
            doc.blocks[key] = [re.sub(r"\s+", " ", joined).strip()]
    return doc


# --------------------------------------------------------------------------
# driver
# --------------------------------------------------------------------------


def variant_name(path: Path) -> str:
    """Derive a stable variant label from a filename.

    Strips an owner-name prefix, a trailing year/format suffix, and tags the
    corpus generation from the containing folder (e.g. `2026_h1` -> `@h1`) so
    that same-named variants from different generations do not collide.
    """
    stem = re.sub(NAME_PREFIX_RE, "", path.stem)
    stem = re.sub(r"_?(19|20)\d{2}(_h[12])?(_onepage|_1page)?$", "", stem)
    stem = re.sub(r"^(cv|resume)_?", "", stem).strip("_") or "master"
    gen = next((GENERATION_RE.search(p) for p in path.parts if GENERATION_RE.search(p)), None)
    return f"{stem}@{gen.group(1)}" if gen else stem


def harvest() -> dict:
    corpus = corpus_dir()
    tex_files = sorted(ROOT.glob("*.tex")) + [
        p for p in sorted(corpus.rglob("*.tex")) if not is_excluded(p, corpus)
    ]
    pdf_files = [p for p in sorted(corpus.rglob("*.pdf")) if not is_excluded(p, corpus)]
    dehyph = Dehyphenator(build_vocabulary(tex_files))

    docs = [parse_tex(p, variant_name(p)) for p in tex_files]
    have_tex = {d.variant for d in docs}
    # A source outside the generation folders (a corpus-root master) carries no
    # generation tag, so it must also claim its render, which does: `master` owns
    # `master@h2`. Tagged sources match exactly, so mle@h1 and mle@h2 stay distinct.
    untagged = {v for v in have_tex if "@" not in v}

    def superseded(variant: str) -> bool:
        return variant in have_tex or variant.split("@", 1)[0] in untagged

    docs += [parse_pdf(p, variant_name(p), dehyph) for p in pdf_files
             if not superseded(variant_name(p))]

    inventory: dict[str, dict] = {}
    for d in docs:
        for it in d.items:
            rec = inventory.setdefault(norm_key(it.text), {
                "text": it.text, "sources": [], "projects": set(),
                "sections": set(), "employers": set()})
            rec["sources"].append({"variant": it.variant, "source": it.source, "order": it.order})
            if it.project:
                rec["projects"].add(it.project)
            rec["sections"].add(it.section)
            if it.employer:
                rec["employers"].add(it.employer)
            if it.source.endswith(".tex"):
                rec["text"] = it.text          # .tex spelling is canonical

    for rec in inventory.values():
        for k in ("projects", "sections", "employers"):
            rec[k] = sorted(rec[k])
        rec["variants"] = sorted({s["variant"] for s in rec["sources"]})
        rec["n_variants"] = len(rec["variants"])

    return {
        "documents": [{"source": d.source, "variant": d.variant, "n_items": len(d.items),
                       "blocks": d.blocks, "projects": d.projects,
                       "items": [asdict(i) for i in d.items]} for d in docs],
        "inventory": sorted(inventory.values(), key=lambda r: (-r["n_variants"], r["text"])),
    }


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    result = harvest()
    (OUT / "inventory.json").write_text(
        json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"{'variant':<16}{'bullets':>8}{'projects':>10}   source")
    for d in result["documents"]:
        print(f"{d['variant']:<16}{d['n_items']:>8}{len(d['projects']):>10}   {d['source']}")
    inv = result["inventory"]
    shared = sum(1 for r in inv if r["n_variants"] > 1)
    print(f"\ndistinct bullets: {len(inv)}   shared: {shared}   unique-to-one: {len(inv) - shared}")
    print(f"wrote {OUT / 'inventory.json'}")


if __name__ == "__main__":
    main()
