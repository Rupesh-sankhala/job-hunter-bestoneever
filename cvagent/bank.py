"""Build bank v0 from the harvested corpus (build step 2).

The bank is the single source of truth: every sentence any generated CV may
contain must already exist here, approved. Runtime never writes prose -- it
selects. That is what makes verification a membership lookup instead of a
similarity threshold.

v0 populates the bank from what has already been authored across the corpus and
leaves the gaps explicit. It does NOT invent phrasing: a record/angle cell with
no harvested bullet stays empty and is reported for authoring (build step 4).

Records are keyed by (angle, domain). Domain is a second dimension, not part of
the angle -- two variants can share an angle and differ only in domain framing.
Resolution falls back (angle, domain) -> (angle, null) -> neutral, and `neutral`
is always present, so no record is ever unrenderable.

Output: bank/bank.yaml, bank/coverage_review.md
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path

import yaml

from cvagent.harvest import ROOT, corpus_dir, norm_key

HARVEST = ROOT / "data" / "harvest"
CONFIG = ROOT / "config" / "angles.yaml"
OUT = ROOT / "bank"

ALL_ANGLES = [
    "neutral", "decision_science", "production_ml",
    "agent_llm", "client_delivery", "research", "builder",
]

# Figures that must survive verbatim into any render. Order matters: fractions
# are tried first, or "1/10th" is shredded into "1" and "10" -- losing exactly
# the kind of claim the allowlist exists to protect.
NUMERIC_RE = re.compile(
    r"\d+/\d+(?:th|nd|rd|st)?"                       # 1/10th
    r"|\$?\d[\d,.]*\s*[BMKk]\+?"                     # $10M+, 1B+, 7.8k, 860M
    r"|\d[\d,.]*\s*%"                                # 80%, 205%
    r"|\d[\d,.]*\s*[×x](?![A-Za-z])"                 # 5×, 4x
    r"|\d+\+?\s*(?:months?|years?)"                  # 6+ months
    r"|\d[\d,.]*\+"                                  # 100+, 13+
)


def load_config() -> dict:
    if not CONFIG.exists():
        raise SystemExit(f"missing {CONFIG}; it defines the variant->angle mapping")
    return yaml.safe_load(CONFIG.read_text(encoding="utf-8"))


def load_harvest() -> tuple[dict, dict]:
    inv = json.loads((HARVEST / "inventory.json").read_text(encoding="utf-8"))
    rec = json.loads((HARVEST / "records_draft.json").read_text(encoding="utf-8"))
    return inv, rec


# --------------------------------------------------------------------------
# identity (immutable -- byte-copied into every render, never generated)
# --------------------------------------------------------------------------

CENTER_RE = re.compile(r"\\begin\{center\}(.*?)\\end\{center\}", re.S)
ENTRY_RE = re.compile(r"\\entry\{(.+?)\}\{(.+?)\}")


def extract_identity(inv: dict) -> dict:
    from cvagent.harvest import strip_tex, clean_text

    master = next((d for d in inv["documents"] if d["variant"] == "master"), None)
    if master is None:
        raise SystemExit("no master document in the harvest")
    src = next(
        p for p in [*ROOT.glob("*.tex"), *corpus_dir().rglob("*.tex")]
        if p.name == master["source"]
    )
    raw = src.read_text(encoding="utf-8")

    name, contact = "", []
    if m := CENTER_RE.search(raw):
        # The header is "{\LARGE name}\\ line2 | line3 | ...": the explicit break
        # separates the name from the contact line, so split on it before on "|".
        lines = [clean_text(l) for l in strip_tex(m.group(1)).split("\n") if clean_text(l)]
        if lines:
            name = lines[0]
            contact = [p.strip() for p in "|".join(lines[1:]).split("|") if p.strip()]

    employers, education = [], []
    section = None
    for line in raw.splitlines():
        if s := re.search(r"\\section\{(.+?)\}", line):
            section = strip_tex(s.group(1)).strip().lower()
        elif e := ENTRY_RE.search(line):
            title, dates = clean_text(strip_tex(e.group(1))), clean_text(strip_tex(e.group(2)))
            (education if section == "education" else employers).append(
                {"label": title, "dates": dates}
            )

    return {
        "name": name,
        "contact": contact,
        "employers": employers,
        "education": education,
        "_note": "Immutable. Byte-copied into every render; verified in Stage 3.",
    }


# --------------------------------------------------------------------------
# records
# --------------------------------------------------------------------------


def facts_of(texts: list[str]) -> list[str]:
    """Numeric allowlist for a record: every figure any of its bullets states."""
    found: dict[str, None] = {}
    for t in texts:
        for m in NUMERIC_RE.findall(t):
            if (v := m.strip()) and any(c.isdigit() for c in v):
                found.setdefault(v, None)
    return sorted(found, key=lambda s: (-len(s), s))


def build_records(rec: dict, vmap: dict) -> tuple[list[dict], list[dict]]:
    records, gaps = [], []
    for r in rec["records"]:
        by_cell: dict[tuple[str, str | None], list[str]] = defaultdict(list)
        titles_by_cell: dict[tuple[str, str | None], list[str]] = defaultdict(list)

        for bullet in r["bullets"]:
            for variant in bullet["variants"]:
                if (cfg := vmap.get(variant)) is None:
                    continue
                cell = (cfg["angle"], cfg.get("domain"))
                if bullet["text"] not in by_cell[cell]:
                    by_cell[cell].append(bullet["text"])
        for t in r["titles"]:
            for variant in t["variants"]:
                if cfg := vmap.get(variant):
                    cell = (cfg["angle"], cfg.get("domain"))
                    if t["title"] not in titles_by_cell[cell]:
                        titles_by_cell[cell].append(t["title"])

        variants: dict[str, dict] = {}
        for (angle, domain), bullets in sorted(by_cell.items(), key=lambda kv: str(kv[0])):
            key = angle if not domain else f"{angle}/{domain}"
            titles = titles_by_cell.get((angle, domain), [])
            variants[key] = {
                "title": titles[0] if titles else None,
                "bullets": bullets,
                **({"title_alternatives": titles[1:]} if len(titles) > 1 else {}),
            }

        served = sorted({a for a, _ in by_cell})
        for angle in ALL_ANGLES:
            if angle not in served and angle != "neutral":
                gaps.append({"record": r["id"], "angle": angle})

        records.append({
            "id": r["id"],
            "canonical_title": r["canonical_title"],
            "section": r["section"],
            "employer": r["employer"],
            "facts": facts_of([b["text"] for b in r["bullets"]]),
            "serves": served,
            "variants": variants,
        })
    return records, gaps


def build_angles(inv: dict, cfg: dict) -> dict:
    """Per-angle document voice: summary, section headings, skills taxonomy."""
    vmap = cfg["variants"]
    docs = {d["variant"]: d for d in inv["documents"]}
    angles: dict[str, dict] = {}

    for variant, vcfg in vmap.items():
        doc = docs.get(variant)
        if not doc:
            continue
        key = vcfg["angle"] if not vcfg.get("domain") else f"{vcfg['angle']}/{vcfg['domain']}"
        blocks = doc["blocks"]
        headers = {}
        for h in blocks.get("headers", []):
            k, _, v = h.partition("=")
            headers[k] = v
        angles[key] = {
            "source_variant": variant,
            "summary": " ".join(blocks.get("summary", [])) or None,
            "headers": headers,
            "skills": blocks.get("skills", []),
        }

    for angle in cfg.get("unauthored", []):
        angles[angle] = {
            "source_variant": None,
            "summary": None,
            "headers": {},
            "skills": [],
            "_todo": "not yet authored -- build step 4",
        }
    return angles


def build_achievements(rec: dict, vmap: dict) -> list[dict]:
    by_text: dict[str, dict] = {}
    for loose in rec["loose"]:
        if loose["section"] != "achievements":
            continue
        cfg = vmap.get(loose["variant"])
        if not cfg:
            continue
        entry = by_text.setdefault(
            norm_key(loose["text"]), {"text": loose["text"], "angles": []}
        )
        key = cfg["angle"] if not cfg.get("domain") else f"{cfg['angle']}/{cfg['domain']}"
        if key not in entry["angles"]:
            entry["angles"].append(key)
    return sorted(by_text.values(), key=lambda e: (-len(e["angles"]), e["text"]))


# --------------------------------------------------------------------------
# report
# --------------------------------------------------------------------------


def coverage_report(bank: dict, cfg: dict) -> str:
    cells = sorted({k for r in bank["records"] for k in r["variants"]})
    ordered = [c for c in ["neutral"] if c in cells] + [c for c in cells if c != "neutral"]

    L = [
        "# Bank v0 — Coverage Review",
        "",
        "Built from what is **already authored** across the corpus. Nothing here is "
        "generated: every bullet was written by hand in some CV variant. Empty cells "
        "are gaps to author (build step 4), not defects.",
        "",
        f"- **{len(bank['records'])}** records · **{len(ordered)}** authored angle/domain cells",
        f"- Unauthored angles: `{'`, `'.join(cfg.get('unauthored', []))}`",
        "",
        "## Coverage matrix",
        "",
        "Cell = number of authored bullets.",
        "",
        "| record | " + " | ".join(f"`{c}`" for c in ordered) + " |",
        "|---|" + "---|" * len(ordered),
    ]
    for r in bank["records"]:
        row = [f"`{r['id']}`"]
        for c in ordered:
            n = len(r["variants"].get(c, {}).get("bullets", []))
            row.append(str(n) if n else "—")
        L.append("| " + " | ".join(row) + " |")

    L += ["", "## Records", ""]
    for r in bank["records"]:
        L += [f"### `{r['id']}` — {r['canonical_title']}", "",
              f"*serves:* `{'`, `'.join(r['serves'])}` · *facts:* "
              + (", ".join(f"`{f}`" for f in r["facts"]) or "none"), ""]
        for cell, v in r["variants"].items():
            L.append(f"**`{cell}`** — {v['title'] or '_(no title variant)_'}")
            L.append("")
            for b in v["bullets"]:
                L.append(f"- {b}")
            if alts := v.get("title_alternatives"):
                L.append(f"  <br/>*other titles:* {'; '.join(alts)}")
            L.append("")
        L.append("---")
        L.append("")

    missing = [a for a, v in bank["angles"].items() if not v.get("summary")]
    if missing:
        L += ["## Angles needing authoring", "",
              "These have no summary, headings, or skills taxonomy yet:", ""]
        L += [f"- `{a}`" for a in missing]
        L.append("")
    return "\n".join(L)


def main() -> None:
    cfg = load_config()
    inv, rec = load_harvest()
    vmap = cfg["variants"]

    records, gaps = build_records(rec, vmap)
    for r in records:
        if status := cfg.get("record_status", {}).get(r["id"]):
            r["status"] = status

    bank = {
        "version": 0,
        "identity": extract_identity(inv),
        "angles": build_angles(inv, cfg),
        "records": records,
        "achievements": build_achievements(rec, vmap),
    }

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "bank.yaml").write_text(
        yaml.safe_dump(bank, sort_keys=False, allow_unicode=True, width=100), encoding="utf-8")
    (OUT / "coverage_review.md").write_text(coverage_report(bank, cfg), encoding="utf-8")

    cells = sorted({k for r in records for k in r["variants"]})
    print(f"records: {len(records)}   authored cells: {len(cells)}")
    print(f"cells: {', '.join(cells)}\n")
    print(f"{'record':<24}" + "".join(f"{c.split('/')[0][:9]:>11}" for c in cells))
    for r in records:
        print(f"{r['id']:<24}" + "".join(
            f"{len(r['variants'].get(c, {}).get('bullets', [])) or '-':>11}" for c in cells))
    total = sum(len(v["bullets"]) for r in records for v in r["variants"].values())
    print(f"\nauthored bullets across cells: {total}")
    print(f"unauthored (record x angle) gaps: {len(gaps)}")
    print(f"\nwrote {OUT/'bank.yaml'} and {OUT/'coverage_review.md'}")


if __name__ == "__main__":
    main()
