# job-hunter-bestoneever

A JD-aware CV customization agent built around one constraint:

> **The agent may re-select, re-order, and re-phrase content. It may never add a claim.**

Most CV tailoring tools generate text and hope it's true. This one **selects** from a bank
of pre-authored, human-approved bullets, so verification collapses from "is this
generated sentence faithful?" to "is this exact string in the bank?" — a dictionary
lookup. The safety guarantee lives at authoring time, not at runtime.

**Status:** early. Harvest, clustering, and the bank are implemented and tested; the JD
parser, coverage engine, and renderer are specified but not yet built.

- **New to the project?** Start with [`docs/CONCEPTS.md`](docs/CONCEPTS.md) — every term
  explained from first principles.
- **Want the design rationale?** [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — what was
  tried, what failed, and why the current shape won.

---

## The problem this solves

Maintaining a handful of fixed CV variants and picking the closest one for each
application has a measurable hit rate. On a hand-scored sample of 8 real job
descriptions, the best fixed variant was good enough to send **40%** of the time.
The other 60% needed different *project selection* and different *framing* — not
light edits.

Crucially, the misses weren't random. CV variants are usually cut along **role
families** (ML Engineer, Product DS, Research DS). But job descriptions vary along a
different axis — what kind of value the role hires you to produce:

| Angle | Hires you to | Signals in a JD |
|---|---|---|
| `decision_science` | turn data into decisions that move business metrics | causal inference, uplift, A/B, bandits, metric design |
| `production_ml` | ship and operate models at scale | MLOps, monitoring, CI/CD, pipelines, drift |
| `agent_llm` | build LLM/agent systems and integrations | orchestration, tool calling, structured outputs, observability |
| `client_delivery` | co-build with customers and explain it | consulting, customer-facing, training |
| `research` | produce defensible novel results | hypotheses, publications, evaluation design |
| `builder` | ship things fast, generalist | portfolio, "something you've shipped", speed |

A JD is a **blend** of angles, not a label. Cut your variants along role families and
you will have no variant for whole classes of role — which is exactly what the 40%
measurement showed.

## How it works

```
JD ─► parse to typed requirements ─► coverage vs. bank ─► apply decision
                                                             │
                            ┌────────────────────────────────┴───────────┐
                     score ≥ T_ship                              score < T_ship
                            │                                            │
                   ship fixed variant                         assemble from bank
                                                                         │
                                                    select records by *marginal*
                                                    coverage gain, render in the
                                                    dominant angle's phrasing
                                                                         │
                                                     verify ─► human approves ─► send
```

**Coverage, not cosine.** JD-to-CV embedding similarity is usable for *ranking* but
not for *thresholding* — the band drifts with document length and prose style, and a
gate needs an absolute cut that generalizes. Instead the JD is atomized into typed
requirements and each is adjudicated against the bank, with **mandatory evidence**: a
requirement can only be `met` if it names a specific bullet. No bullet, no credit.

**Atomization rules.** Split on enumeration, never on composition:

| Pattern | Example | Rule |
|---|---|---|
| AND-list | "monitoring, drift detection, and retraining" | split into 3 |
| OR-list | "PyTorch or TensorFlow" | one group; any member satisfies |
| Composition | "deploy deep learning models to production" | **do not split** |

Splitting "deploy DL models to production" into `deep learning` + `deployment` would
let the score be satisfied by halves. That's the inflation this project exists to
prevent.

## What's implemented

### `cvagent/harvest.py`

Parses a CV corpus (LaTeX + PDF) into a union inventory of distinct bullets.

Role-specific variants are **not** subsets of the master — each is hand-rewritten and
carries material the master lacks — so the bank must be built from the union of every
document, not from the master alone.

LaTeX sources are ground truth. For PDF-only variants, structure is recovered from
**font weight**: CV templates set section headers, employer entries, and project
titles in a bold face and body text in a regular one. This matters more than it
sounds — in extracted PDF text there is no blank line between a bullet and the next
project title, so a text-only parser silently appends titles onto the preceding
bullet, corrupting it. Boldness separates them.

Also handles: line-break dehyphenation that preserves real compounds (`Lang-\nfuse`
→ `Langfuse`, but `tactic-\nrevealing` keeps its hyphen, resolved against a
vocabulary built from the `.tex` sources); multiple template families; right-aligned
dates emitted on their own line; and templates that set project titles in regular
weight with a trailing colon.

### `cvagent/bank.py`

Builds the bank from what has already been authored across the corpus. It does **not**
invent phrasing: a record/angle cell with no harvested bullet stays empty and is
reported for authoring. Every bullet in the bank was written by hand in some CV.

Records are keyed by `(angle, domain)`. **Domain is a second dimension, not part of the
angle** — two variants can share an angle and differ only in domain framing (a
production-ML CV aimed at finance-risk roles versus a generic one). Collapsing domain
into angle would make one silently overwrite the other. Resolution falls back
`(angle, domain)` → `(angle, null)` → `neutral`, and `neutral` always exists, so no
record is ever unrenderable.

Each record carries a `facts` allowlist — every figure its bullets state — which Stage 3
checks rendered numbers against. Fractions are extracted before general numerics, or
`1/8th` gets shredded into `1` and `8`, losing exactly the kind of ratio the allowlist
exists to protect.

Outputs `bank/bank.yaml` and a coverage matrix showing which record × angle cells are
filled and which need authoring.

### `cvagent/cluster.py`

Groups harvested bullets into canonical records.

Every variant renames its projects, so titles alone can't identify a record — two
headings for the same project may share one token while two unrelated projects share
several generic ones. Bullet *content* is far more distinctive, so clustering scores
IDF-weighted bullet overlap at 0.65 against title similarity at 0.35, seeded from the
master document.

Bullets with no project heading are handled too: a variant may compress an older role
by listing its bullets directly under the employer. Dropping those would silently lose
material from the bank, so each is scored against every record's single best-matching
bullet — comparing one bullet against a record's whole token union dilutes the signal —
and placed on content alone.

Emits a human-review report before anything is written to the bank, flagging
low-margin assignments and unmatched groups.

## Quick start

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt

# Uses the synthetic fixtures by default
.venv/bin/python -m cvagent.harvest
.venv/bin/python -m cvagent.cluster

# Point at your own corpus instead
CVAGENT_CORPUS=~/path/to/cvs .venv/bin/python -m cvagent.harvest
```

Outputs land in `data/harvest/`:

- `inventory.json` — every distinct bullet with its source variants
- `records_review.md` — human-review report, grouped by canonical record
- `records_draft.json` — machine-readable clustering

and in `bank/`:

- `bank.yaml` — the bank itself
- `coverage_review.md` — record × angle coverage matrix, and the gaps to author

### Configuration

| Variable | Default | Purpose |
|---|---|---|
| `CVAGENT_CORPUS` | `./fixtures` | Where the CV corpus lives |
| `CVAGENT_MASTER` | `master` | Variant name of the document containing every record |
| `CVAGENT_NAME_PREFIX` | `^[a-z]+_(resume\|master\|cv)_?` | Filename prefix stripped from variant labels |
| `CVAGENT_EXCLUDE` | *(none)* | Comma-separated globs to skip, e.g. a retired CV generation |
| `CVAGENT_ANGLES` | `config/angles.yaml`, else `<corpus>/angles.yaml` | Variant → angle mapping |

Optional `config/record_ids.json` maps project titles to stable record ids
(`{"<project title>": "<id>"}`); unmapped titles get a slug. That file is gitignored —
project titles are personal content.

## Fixtures

`fixtures/` contains three synthetic CVs for a fictional person. They are not
decoration — they encode the hard cases:

- **master** — the union of all records, serif template
- **product@h2** — every project renamed, every bullet rewritten, sections renamed.
  Clustering must still map these to the same records.
- **legacy@h1** — a different template family (sans-serif), project titles in regular
  weight with trailing colons, dates on their own line. Also carries a deliberately
  stale figure so the numeric-conflict scan has something to find.

PDF fixtures are generated from the `.tex` sources, since the PDF parser is the part
that most needs testing:

```bash
scripts/build_fixtures.sh     # requires pdflatex
```

Tests that need PDFs skip cleanly when they haven't been built.

## Testing

```bash
.venv/bin/python -m pytest tests/ -q
```

The golden test asserts that the LaTeX parser (ground truth) and the font-aware PDF
parser produce identical output for the same document. That pair is what every
PDF-only variant's correctness rests on.

## Roadmap

- [x] Corpus harvest (LaTeX + PDF, font-aware)
- [x] Bullet clustering into canonical records
- [x] Bank v0 — per-(angle, domain) bullet variants, facts allowlist, coverage matrix
- [ ] JD parser → typed requirements
- [ ] Coverage engine + apply/don't-apply decision
- [ ] LaTeX renderer with page-budget degradation ladder
- [ ] Membership verification
- [ ] Cover letter generation
- [ ] Evaluation harness over a fixed JD set

## License

MIT
