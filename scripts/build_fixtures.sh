#!/usr/bin/env bash
# Render the synthetic .tex fixtures to PDF so the PDF parser can be tested.
# Requires pdflatex (e.g. `brew install --cask basictex`).
set -euo pipefail
cd "$(dirname "$0")/.."
command -v pdflatex >/dev/null || { echo "pdflatex not found; install BasicTeX or MacTeX" >&2; exit 1; }
find fixtures -name '*.tex' -print0 | while IFS= read -r -d '' tex; do
  dir=$(dirname "$tex")
  echo "rendering $tex"
  pdflatex -interaction=nonstopmode -halt-on-error -output-directory "$dir" "$tex" >/dev/null
  rm -f "${tex%.tex}".{aux,log,out}
done
echo "done: $(find fixtures -name '*.pdf' | wc -l | tr -d ' ') PDFs"
