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
from gao_idl.review import Verdicts, apply_verdicts, make_bundle
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
assert.ok(open.length >= 3);
r = P.decideAssumption(s, bundle, open[0].index, "agreed", "");
s = r.state;
r = P.decideAssumption(s, bundle, open[1].index, "disputed", " a missing bid is zero ");
s = r.state;
assert.ok(P.decideAssumption(s, bundle, 999, "agreed", "").error);
r = P.decideAssumption(s, bundle, open[2].index, "disputed", "   ");    // a dispute needs a note
assert.ok(r.error && r.error.includes("why you disagree"));
assert.strictEqual(r.state, s);
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

// Enter submits a note (also with Ctrl or Cmd); Shift+Enter is a new line
assert.strictEqual(P.submitsNote("Enter", true, false, false), true);
assert.strictEqual(P.submitsNote("Enter", false, true, false), true);
assert.strictEqual(P.submitsNote("Enter", false, false, false), true);
assert.strictEqual(P.submitsNote("Enter", false, false, true), false);
assert.strictEqual(P.submitsNote("Enter", true, false, true), true);
assert.strictEqual(P.submitsNote("n", true, false, false), false);

// restoring saved progress keeps only what still fits the bundle
const saved = JSON.parse(JSON.stringify(s));
saved.cards["no-such-card"] = { verdict: "confirmed", note: "" };
saved.cards[id(0)] = { verdict: "confirmed", note: "" };        // stale: dropped
saved.cards[id(2)] = { verdict: "rejected", note: "" };         // no note: dropped
saved.cards[id(3)] = { verdict: "sure", note: "" };             // unknown: dropped
saved.assumptions["999"] = { verdict: "agreed", note: "" };
saved.assumptions[String(open[2].index)] = { verdict: "disputed", note: " " };  // no note: dropped
saved.at = 1e9;
const back = P.restore(saved, bundle);
assert.deepStrictEqual(Object.keys(back.cards), [id(1)]);
assert.strictEqual(Object.keys(back.assumptions).length, 2);
assert.strictEqual(back.at, 0);
assert.strictEqual(back.reviewer, "Ann Example");
assert.deepStrictEqual(P.restore(null, bundle), P.initialState());
assert.deepStrictEqual(P.restore("junk", bundle), P.initialState());

// answers held by a server fill in where this browser has none, if they still fit
const fresh = P.initialState();
const hash = (i) => bundle.cards[i].outcome_hash;
const choice = bundle.spec.assumptions[open[0].index];
const held = {
  reviewer: "Ann Example",
  cards: [
    { id: id(1), verdict: "confirmed", note: "", outcome_hash: hash(1) },
    { id: id(2), verdict: "rejected", note: "not this", outcome_hash: hash(2) },
    { id: id(3), verdict: "confirmed", note: "", outcome_hash: "0".repeat(64) },   // other outcome
    { id: id(0), verdict: "confirmed", note: "", outcome_hash: hash(0) },          // stale card
    { id: "no-such-card", verdict: "confirmed", note: "", outcome_hash: hash(1) },
    { id: "__proto__", verdict: "confirmed", note: "", outcome_hash: hash(1) },
  ],
  assumptions: [
    { index: open[0].index, text: choice.text, verdict: "agreed", note: "" },
    { index: open[1].index, text: "other words", verdict: "agreed", note: "" },
    { index: 999, text: "x", verdict: "agreed", note: "" },
  ],
};
const merged = P.mergeServer(fresh, bundle, held);
assert.deepStrictEqual(Object.keys(merged.cards).sort(), [id(1), id(2)].sort());
assert.strictEqual(merged.cards[id(2)].note, "not this");
assert.deepStrictEqual(Object.keys(merged.assumptions), [String(open[0].index)]);
assert.strictEqual(merged.reviewer, "Ann Example");
assert.deepStrictEqual(fresh, P.initialState());                       // the input is untouched
// an answer given here is never replaced, and nothing new means the same object back
let mine = P.initialState();
mine.cards[id(1)] = { verdict: "rejected", note: "mine" };
const kept = P.mergeServer(mine, bundle, held);
assert.strictEqual(kept.cards[id(1)].note, "mine");
assert.strictEqual(P.mergeServer(merged, bundle, held), merged);
assert.strictEqual(P.mergeServer(fresh, bundle, null), fresh);
assert.strictEqual(P.mergeServer(fresh, bundle, { cards: "junk", assumptions: 3 }), fresh);

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
    # the page's export is a valid verdicts file, and it imports
    verdicts = Verdicts.model_validate(exported)
    spec = load_spec(ROOT / "examples" / "specs" / "auction.json")
    cards = load_cards(ROOT / "examples" / "cards" / "auction.json")
    new_cards, new_spec, problems = apply_verdicts(cards, spec, verdicts)
    assert problems == []
    assert [c.status for c in new_cards[:3]] == ["rejected", "confirmed", "proposed"]
    assert {c.reviewer for c in new_cards[:2]} == {"Ann Example"}
    assert sum(a.confirmed for a in new_spec.assumptions) == 1


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
