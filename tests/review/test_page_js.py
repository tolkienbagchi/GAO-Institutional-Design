"""The state functions of ``page.js``, run under node when it is on the path.

Only the pure half of the script runs here (``module.exports``); the DOM glue does not. When node
is missing the test skips, and then ``page.js`` has not been executed by the test suite at all.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from gao_idl.authoring import load_cards, load_spec
from gao_idl.review import make_bundle
from gao_idl.review.page import asset

ROOT = Path(__file__).resolve().parents[2]
NODE = shutil.which("node")

SCRIPT = r"""
const assert = require("assert");
const fs = require("fs");
const P = require(process.argv[2]);
const bundle = JSON.parse(fs.readFileSync(process.argv[3], "utf8"));
const n = bundle.cards.length;
const id = (i) => bundle.cards[i].card.id;

// moving and deciding
let s = P.initialState();
assert.deepStrictEqual(s, { screen: 0, at: 0, cards: {}, assumptions: {}, reviewer: "" });
assert.strictEqual(P.moveCard(s, bundle, -1).at, 0);
assert.strictEqual(P.moveCard(s, bundle, 99).at, n - 1);
assert.strictEqual(P.goTo(s, 9).screen, P.SCREENS - 1);

let r = P.decideCard(s, bundle, "confirmed", "");          // card 0 is stale in this bundle
assert.ok(r.error && r.error.includes("cannot be confirmed"));
assert.strictEqual(r.state, s);
r = P.decideCard(s, bundle, "rejected", "   ");
assert.ok(r.error && r.error.includes("what should happen instead"));
r = P.decideCard(s, bundle, "rejected", "  the later name should win ");
assert.strictEqual(r.error, null);
s = r.state;
assert.strictEqual(s.at, 1);
assert.deepStrictEqual(s.cards[id(0)], { verdict: "rejected", note: "the later name should win" });
r = P.decideCard(s, bundle, "confirmed", "ignored? no: kept");
s = r.state;
assert.strictEqual(s.cards[id(1)].verdict, "confirmed");
s = P.advance(s, bundle);                                     // skip card 2
assert.strictEqual(s.at, 3);
s = P.moveCard(s, bundle, -1);
assert.strictEqual(s.at, 2);
r = P.decideCard(s, bundle, "maybe", "");
assert.ok(r.error);
s = P.clearCard(P.moveCard(s, bundle, -1), bundle);          // remove the answer on card 1
assert.strictEqual(s.cards[id(1)], undefined);
r = P.decideCard(s, bundle, "confirmed", "");
s = r.state;

// the last card leads to the finish screen
let end = P.moveCard(s, bundle, 99);
end = P.decideCard(end, bundle, "confirmed", "").state;
assert.strictEqual(end.screen, P.FINISH_SCREEN);
assert.strictEqual(end.at, n - 1);

// assumptions
const open = P.openAssumptions(bundle);
assert.ok(open.length >= 2);
r = P.decideAssumption(s, bundle, open[0].index, "agreed", "");
s = r.state;
r = P.decideAssumption(s, bundle, open[1].index, "disputed", " a missing bid is zero ");
s = r.state;
assert.ok(P.decideAssumption(s, bundle, 999, "agreed", "").error);
assert.ok(P.decideAssumption(s, bundle, open[0].index, "yes", "").error);
s = P.setReviewer(s, "  Ann Example ");

// the export (shape mirrored by gao_idl.review.verdicts.Verdicts)
const out = P.exportVerdicts(s, bundle);
const keys = ["bundle_version", "spec_title", "reviewer", "cards", "assumptions"];
assert.deepStrictEqual(Object.keys(out), keys);
assert.strictEqual(out.bundle_version, 1);
assert.strictEqual(out.spec_title, bundle.spec.title);
assert.strictEqual(out.reviewer, "Ann Example");
assert.deepStrictEqual(out.cards.map((c) => c.id), [id(0), id(1)]);
assert.deepStrictEqual(Object.keys(out.cards[0]), ["id", "verdict", "note", "outcome_hash"]);
assert.strictEqual(out.cards[0].outcome_hash, bundle.cards[0].outcome_hash);
assert.deepStrictEqual(Object.keys(out.assumptions[0]), ["index", "text", "verdict", "note"]);
assert.strictEqual(out.assumptions[1].note, "a missing bid is zero");
assert.strictEqual(out.assumptions[0].text, bundle.spec.assumptions[open[0].index].text);
assert.deepStrictEqual(P.cardVerdict(s, bundle, id(1)), out.cards[1]);
assert.strictEqual(P.cardVerdict(s, bundle, id(5)), null);
assert.deepStrictEqual(P.summary(s, bundle), {
  confirmed: 1, rejected: 1, unanswered: n - 2, agreed: 1, disputed: 1, open: open.length - 2,
});

// keys
assert.strictEqual(P.keyAction("y", false), "yes");
assert.strictEqual(P.keyAction("n", false), "no");
assert.strictEqual(P.keyAction("s", false), "skip");
assert.strictEqual(P.keyAction("ArrowLeft", false), "back");
assert.strictEqual(P.keyAction("ArrowRight", false), "next");
assert.strictEqual(P.keyAction("y", true), null);
assert.strictEqual(P.keyAction("q", false), null);
assert.strictEqual(P.keyAction("constructor", false), null);

// restoring saved progress keeps only what still fits the bundle
const saved = JSON.parse(JSON.stringify(s));
saved.cards["no-such-card"] = { verdict: "confirmed", note: "" };
saved.cards[id(0)] = { verdict: "confirmed", note: "" };        // stale: dropped
saved.cards[id(2)] = { verdict: "rejected", note: "" };         // no note: dropped
saved.cards[id(3)] = { verdict: "sure", note: "" };             // unknown: dropped
saved.assumptions["999"] = { verdict: "agreed", note: "" };
saved.at = 1e9;
const back = P.restore(saved, bundle);
assert.deepStrictEqual(Object.keys(back.cards), [id(1)]);
assert.strictEqual(Object.keys(back.assumptions).length, 2);
assert.strictEqual(back.at, 0);
assert.strictEqual(back.reviewer, "Ann Example");
assert.deepStrictEqual(P.restore(null, bundle), P.initialState());
assert.deepStrictEqual(P.restore("junk", bundle), P.initialState());

process.stdout.write(JSON.stringify(out));
"""


@pytest.mark.skipif(NODE is None, reason="node is not on the path: page.js was not executed")
def test_page_js_state_functions_under_node(tmp_path: Path) -> None:
    bundle = make_bundle(
        load_spec(ROOT / "examples" / "specs" / "auction.json"),
        load_cards(ROOT / "examples" / "cards" / "auction.json"),
    )
    data = bundle.model_dump(mode="json")
    data["cards"][0]["stale"] = True  # pretend the first card's outcome changed
    (tmp_path / "bundle.json").write_text(json.dumps(data))
    (tmp_path / "page.js").write_text(asset("page.js"))
    (tmp_path / "test.js").write_text(SCRIPT)
    done = subprocess.run(
        [
            str(NODE),
            str(tmp_path / "test.js"),
            str(tmp_path / "page.js"),
            str(tmp_path / "bundle.json"),
        ],
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert done.returncode == 0, done.stderr[-3000:]
    exported = json.loads(done.stdout)
    assert exported["cards"][0]["verdict"] == "rejected"


@pytest.mark.skipif(NODE is None, reason="node is not on the path: page.js was not executed")
def test_page_js_is_valid_script() -> None:
    done = subprocess.run(
        [str(NODE), "--check", "-"],
        input=asset("page.js"),
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert done.returncode == 0, done.stderr
