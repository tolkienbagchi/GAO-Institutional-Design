"""Write the review pages of the three shipped environments to ``build/review/``.

Run from the repository root: ``python scripts/make_example_pages.py [--fuzz N] [--seed S]``.
Each page is generated from ``examples/specs/<name>.json`` and ``examples/cards/<name>.json``
by ``gao_idl.review`` (auction, prisoner's dilemma, carbon market). The script reads the example
files and never writes to them; ``build/`` is ignored by git. The pages need no server: open them
in a browser.
"""

import argparse
import sys
from pathlib import Path

from gao_idl.authoring import load_cards, load_spec
from gao_idl.review import make_bundle, render_page

ROOT = Path(__file__).resolve().parents[1]
NAMES = ("auction", "pd", "vcm")


def main(argv: list[str] | None = None) -> int:
    """Write one page per shipped environment; print each path and size."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--fuzz", type=int, default=200, help="edge-biased runs per page")
    parser.add_argument("--seed", type=int, default=1, help="seed for the fuzzed runs")
    parser.add_argument("--out", type=Path, default=ROOT / "build" / "review")
    args = parser.parse_args(argv)
    args.out.mkdir(parents=True, exist_ok=True)
    for name in NAMES:
        spec = load_spec(ROOT / "examples" / "specs" / f"{name}.json")
        cards = load_cards(ROOT / "examples" / "cards" / f"{name}.json")
        bundle = make_bundle(spec, cards, fuzz=args.fuzz, seed=args.seed)
        path = args.out / f"{name}.html"
        page = render_page(bundle)
        path.write_text(page, encoding="utf-8")
        status = bundle.status
        print(
            f"{path}: {len(page.encode('utf-8')) // 1024} KB, {len(bundle.cards)} cards, "
            f"{status.runs} runs ({status.fuzzed} fuzzed), {len(status.mismatches)} mismatches"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
