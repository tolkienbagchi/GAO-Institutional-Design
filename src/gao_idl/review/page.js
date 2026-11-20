/* gao-idl review page.
 *
 * Everything the reviewer reads is already in the HTML, rendered and escaped by Python. This
 * script only moves between screens and cards, records answers in memory (and, as a
 * convenience, in localStorage), and exports them. It never builds markup from strings: text
 * goes in with textContent.
 *
 * The first half is pure functions of (state, bundle) with no DOM access; the tests run them
 * under node when it is available. The second half is the DOM glue.
 */
(function (root) {
  "use strict";

  // ------------------------------------------------------------------ pure state

  var SCREENS = 5;
  var CARD_SCREEN = 3;
  var FINISH_SCREEN = 4;

  function copy(obj) {
    var out = {};
    Object.keys(obj).forEach(function (k) { out[k] = obj[k]; });
    return out;
  }

  function initialState() {
    return { screen: 0, at: 0, cards: {}, assumptions: {}, reviewer: "" };
  }

  function openAssumptions(bundle) {
    var out = [];
    bundle.spec.assumptions.forEach(function (a, i) {
      if (!a.confirmed) { out.push({ index: i, text: a.text }); }
    });
    return out;
  }

  function goTo(state, screen) {
    var next = copy(state);
    next.screen = Math.max(0, Math.min(SCREENS - 1, screen));
    return next;
  }

  function moveCard(state, bundle, delta) {
    var next = copy(state);
    var last = Math.max(0, bundle.cards.length - 1);
    next.at = Math.max(0, Math.min(last, state.at + delta));
    return next;
  }

  /* Advance past the current card; after the last card, go to the finish screen. */
  function advance(state, bundle) {
    if (state.at >= bundle.cards.length - 1) { return goTo(state, FINISH_SCREEN); }
    return moveCard(state, bundle, 1);
  }

  /* Record a verdict on the current card. Returns {state, error}; on error the state is
   * unchanged. A stale card cannot be confirmed; a rejection needs a note. */
  function decideCard(state, bundle, verdict, note) {
    var view = bundle.cards[state.at];
    var text = (note || "").trim();
    if (!view) { return { state: state, error: "There is no card here." }; }
    if (verdict !== "confirmed" && verdict !== "rejected") {
      return { state: state, error: "Unknown answer." };
    }
    if (verdict === "confirmed" && view.stale) {
      return {
        state: state,
        error: "This card no longer shows what the environment does, so it cannot be confirmed.",
      };
    }
    if (verdict === "rejected" && !text) {
      return { state: state, error: "Please write what should happen instead, then press Submit." };
    }
    var cards = copy(state.cards);
    cards[view.card.id] = { verdict: verdict, note: text };
    var next = copy(state);
    next.cards = cards;
    return { state: advance(next, bundle), error: null };
  }

  function clearCard(state, bundle) {
    var view = bundle.cards[state.at];
    var cards = copy(state.cards);
    if (view) { delete cards[view.card.id]; }
    var next = copy(state);
    next.cards = cards;
    return next;
  }

  /* Record "agreed" or "disputed" on the assumption with this index. Returns {state, error}. */
  function decideAssumption(state, bundle, index, verdict, note) {
    var item = bundle.spec.assumptions[index];
    if (!item || item.confirmed) { return { state: state, error: "There is no such open choice." }; }
    if (verdict !== "agreed" && verdict !== "disputed") {
      return { state: state, error: "Unknown answer." };
    }
    var text = (note || "").trim();
    if (verdict === "disputed" && !text) {
      return { state: state, error: "Please write why you disagree, then press Submit." };
    }
    var assumptions = copy(state.assumptions);
    assumptions[String(index)] = { verdict: verdict, note: text };
    var next = copy(state);
    next.assumptions = assumptions;
    return { state: next, error: null };
  }

  function setReviewer(state, name) {
    var next = copy(state);
    next.reviewer = String(name || "").trim();
    return next;
  }

  /* One card verdict as sent to the server and as listed in the export.
   * Mirrors gao_idl.review.verdicts.CardVerdict: {id, verdict, note, outcome_hash}. */
  function cardVerdict(state, bundle, id) {
    var view = null;
    bundle.cards.forEach(function (v) { if (v.card.id === id) { view = v; } });
    var given = state.cards[id];
    if (!view || !given) { return null; }
    return { id: id, verdict: given.verdict, note: given.note, outcome_hash: view.outcome_hash };
  }

  /* One assumption verdict. Mirrors AssumptionVerdict: {index, text, verdict, note}. */
  function assumptionVerdict(state, bundle, index) {
    var given = state.assumptions[String(index)];
    var item = bundle.spec.assumptions[index];
    if (!given || !item) { return null; }
    return { index: index, text: item.text, verdict: given.verdict, note: given.note };
  }

  /* The whole export. Mirrors gao_idl.review.verdicts.Verdicts:
   * {bundle_version: 1, spec_title, reviewer, cards: [CardVerdict], assumptions: [AssumptionVerdict]}
   * Cards in bundle order, assumptions by index; unanswered ones are left out. */
  function exportVerdicts(state, bundle) {
    var cards = [];
    bundle.cards.forEach(function (v) {
      var entry = cardVerdict(state, bundle, v.card.id);
      if (entry) { cards.push(entry); }
    });
    var assumptions = [];
    bundle.spec.assumptions.forEach(function (a, i) {
      var entry = assumptionVerdict(state, bundle, i);
      if (entry) { assumptions.push(entry); }
    });
    return {
      bundle_version: 1,
      spec_title: bundle.spec.title,
      reviewer: state.reviewer,
      cards: cards,
      assumptions: assumptions,
    };
  }

  function summary(state, bundle) {
    var out = { confirmed: 0, rejected: 0, unanswered: 0, agreed: 0, disputed: 0, open: 0 };
    bundle.cards.forEach(function (v) {
      var given = state.cards[v.card.id];
      if (given) { out[given.verdict] += 1; } else { out.unanswered += 1; }
    });
    openAssumptions(bundle).forEach(function (a) {
      var given = state.assumptions[String(a.index)];
      if (given) { out[given.verdict] += 1; } else { out.open += 1; }
    });
    return out;
  }

  /* Which action a key press means on the card screen; null when it means nothing there.
   * Keys typed into a text field are never actions. */
  function keyAction(key, inField) {
    if (inField) { return null; }
    var map = { y: "yes", Y: "yes", n: "no", N: "no", s: "skip", S: "skip",
                ArrowLeft: "back", ArrowRight: "next" };
    return Object.prototype.hasOwnProperty.call(map, key) ? map[key] : null;
  }

  /* Whether a key press in a note box submits it: Enter, also with Ctrl or Cmd; Shift+Enter is a
   * new line. In a card's box it records "No", in a choice's box "disagree", with the note. */
  function submitsNote(key, ctrlKey, metaKey, shiftKey) {
    return key === "Enter" && (Boolean(ctrlKey || metaKey) || !shiftKey);
  }

  /* A state rebuilt from whatever was saved, keeping only answers that still fit this bundle. */
  function restore(saved, bundle) {
    var state = initialState();
    if (!saved || typeof saved !== "object") { return state; }
    var ids = {};
    bundle.cards.forEach(function (v) { ids[v.card.id] = v; });
    var cards = saved.cards && typeof saved.cards === "object" ? saved.cards : {};
    Object.keys(cards).forEach(function (id) {
      var given = cards[id];
      if (!ids[id] || !given || typeof given !== "object") { return; }
      if (given.verdict !== "confirmed" && given.verdict !== "rejected") { return; }
      if (given.verdict === "confirmed" && ids[id].stale) { return; }
      var note = typeof given.note === "string" ? given.note : "";
      if (given.verdict === "rejected" && !note.trim()) { return; }
      state.cards[id] = { verdict: given.verdict, note: note };
    });
    var assumptions = saved.assumptions && typeof saved.assumptions === "object" ? saved.assumptions : {};
    Object.keys(assumptions).forEach(function (key) {
      var given = assumptions[key];
      var item = bundle.spec.assumptions[Number(key)];
      if (!item || item.confirmed || !given || typeof given !== "object") { return; }
      if (given.verdict !== "agreed" && given.verdict !== "disputed") { return; }
      var note = typeof given.note === "string" ? given.note : "";
      if (given.verdict === "disputed" && !note.trim()) { return; }
      state.assumptions[String(Number(key))] = { verdict: given.verdict, note: note };
    });
    if (typeof saved.reviewer === "string") { state.reviewer = saved.reviewer; }
    if (typeof saved.at === "number" && saved.at >= 0 && saved.at < bundle.cards.length) {
      state.at = Math.floor(saved.at);
    }
    if (typeof saved.screen === "number" && saved.screen >= 0 && saved.screen < SCREENS) {
      state.screen = Math.floor(saved.screen);
    }
    return state;
  }

  var pure = {
    SCREENS: SCREENS,
    CARD_SCREEN: CARD_SCREEN,
    FINISH_SCREEN: FINISH_SCREEN,
    initialState: initialState,
    openAssumptions: openAssumptions,
    goTo: goTo,
    moveCard: moveCard,
    advance: advance,
    decideCard: decideCard,
    clearCard: clearCard,
    decideAssumption: decideAssumption,
    setReviewer: setReviewer,
    cardVerdict: cardVerdict,
    assumptionVerdict: assumptionVerdict,
    exportVerdicts: exportVerdicts,
    summary: summary,
    keyAction: keyAction,
    submitsNote: submitsNote,
    restore: restore,
  };

  if (typeof module === "object" && module.exports) {
    module.exports = pure;
    return;
  }

  // ------------------------------------------------------------------ DOM glue

  function readJson(id) {
    return JSON.parse(document.getElementById(id).textContent);
  }

  function all(selector, scope) {
    return Array.prototype.slice.call((scope || document).querySelectorAll(selector));
  }

  function boot() {
    var bundle = readJson("gao-bundle");
    var config = readJson("gao-config");
    var storageKey = "gao-idl-review:" + config.key;
    var state = initialState();
    var statusLine = document.getElementById("status");

    try {
      var saved = window.localStorage.getItem(storageKey);
      if (saved) { state = restore(JSON.parse(saved), bundle); }
    } catch (e) { /* storage is a convenience only */ }

    document.documentElement.classList.add("js");

    function say(text) { statusLine.textContent = text || ""; }

    function save() {
      try {
        window.localStorage.setItem(storageKey, JSON.stringify(state));
      } catch (e) { /* storage is a convenience only */ }
    }

    function send(body) {
      if (!config.api) { return; }
      try {
        fetch(config.api, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        }).then(function (response) {
          if (!response.ok) { throw new Error("status " + response.status); }
        }).catch(function (err) {
          say("Could not send that answer (" + err.message + "). It is kept here and in the download.");
        });
      } catch (err) {
        say("Could not send that answer. It is kept here and in the download.");
      }
    }

    var cardArticles = all("article.card");
    var screens = all("section.screen");

    function cardText(view) {
      var given = state.cards[view.card.id];
      if (!given) { return "Your answer: none yet."; }
      if (given.verdict === "confirmed") { return "Your answer: yes, this is what I meant."; }
      return "Your answer: no. " + given.note;
    }

    function refresh() {
      screens.forEach(function (section, i) { section.hidden = i !== state.screen; });
      all("nav.screens button").forEach(function (button) {
        if (Number(button.dataset.screen) === state.screen) {
          button.setAttribute("aria-current", "page");
        } else {
          button.removeAttribute("aria-current");
        }
      });
      cardArticles.forEach(function (article, i) {
        article.hidden = i !== state.at;
        var view = bundle.cards[i];
        var answer = article.querySelector(".answer");
        if (answer && view) { answer.textContent = cardText(view); }
        var given = view ? state.cards[view.card.id] : null;
        all("button[data-act]", article).forEach(function (button) {
          var act = button.dataset.act;
          var pressed = (act === "yes" && given && given.verdict === "confirmed") ||
                        (act === "no" && given && given.verdict === "rejected");
          button.setAttribute("aria-pressed", pressed ? "true" : "false");
        });
      });
      all("li.assumption").forEach(function (item) {
        var given = state.assumptions[item.dataset.index];
        var answer = item.querySelector(".answer");
        if (answer) {
          answer.textContent = !given ? "Your answer: none yet." :
            given.verdict === "agreed" ? "Your answer: agree." : "Your answer: disagree. " + given.note;
        }
        all("button[data-verdict]", item).forEach(function (button) {
          var pressed = given && given.verdict === button.dataset.verdict;
          button.setAttribute("aria-pressed", pressed ? "true" : "false");
        });
        var opener = item.querySelector("button[data-open-note]");
        if (opener) {
          opener.setAttribute("aria-pressed", given && given.verdict === "disputed" ? "true" : "false");
        }
      });
      var counts = summary(state, bundle);
      var line = document.getElementById("summary");
      if (line) {
        line.textContent =
          "Cards: " + counts.confirmed + " yes, " + counts.rejected + " no, " +
          counts.unanswered + " not answered. Open choices: " + counts.agreed + " agreed, " +
          counts.disputed + " disagreed, " + counts.open + " not answered.";
      }
      var out = document.getElementById("export");
      if (out) { out.value = JSON.stringify(exportVerdicts(state, bundle), null, 2) + "\n"; }
      var reviewer = document.getElementById("reviewer");
      if (reviewer && document.activeElement !== reviewer) { reviewer.value = state.reviewer; }
    }

    function update(next) {
      state = next;
      save();
      refresh();
    }

    /* The note box of a card or a choice: hidden until "No" or "Disagree" asks for it. */
    function panelOf(scope) { return scope.querySelector(".note-panel"); }
    function openerOf(scope) {
      return scope.querySelector('button[data-act="no"], button[data-open-note]');
    }
    function closeNote(scope) {
      var panel = panelOf(scope);
      if (panel) { panel.hidden = true; }
      var opener = openerOf(scope);
      if (opener) { opener.setAttribute("aria-expanded", "false"); }
    }
    function openNote(scope, existing) {
      var panel = panelOf(scope);
      if (!panel) { return; }
      var field = panel.querySelector("textarea");
      if (field && !field.value && existing) { field.value = existing; }
      panel.hidden = false;
      var opener = openerOf(scope);
      if (opener) { opener.setAttribute("aria-expanded", "true"); }
      if (field) { field.focus(); }
    }
    function wireNote(scope, submit) {
      var panel = panelOf(scope);
      if (!panel) { return; }
      var field = panel.querySelector("textarea");
      var go = panel.querySelector("button[data-submit-note]");
      var cancel = panel.querySelector("button[data-cancel-note]");
      if (go) { go.addEventListener("click", submit); }
      if (cancel) {
        cancel.addEventListener("click", function () {
          closeNote(scope);
          say("");
          var opener = openerOf(scope);
          if (opener) { opener.focus(); }
        });
      }
      if (field) {
        field.addEventListener("keydown", function (event) {
          if (event.isComposing) { return; }
          if (event.key === "Escape") { event.preventDefault(); closeNote(scope); return; }
          if (!submitsNote(event.key, event.ctrlKey, event.metaKey, event.shiftKey)) { return; }
          event.preventDefault();
          submit();
        });
      }
    }

    function cardAct(act) {
      var article = cardArticles[state.at];
      if (!article) { return; }
      var noteField = article.querySelector("textarea");
      var view = bundle.cards[state.at];
      if (act === "no") {  // ask for the note first; "submit" records the No
        var before = view ? state.cards[view.card.id] : null;
        openNote(article, before && before.verdict === "rejected" ? before.note : "");
        say("");
        return;
      }
      if (act === "submit") { act = "no"; } else { closeNote(article); }
      if (act === "back") { update(moveCard(state, bundle, -1)); say(""); return; }
      if (act === "next" || act === "skip") { update(act === "skip" ? advance(state, bundle) : moveCard(state, bundle, 1)); say(""); return; }
      var verdict = act === "yes" ? "confirmed" : "rejected";
      var typed = verdict === "rejected" && noteField ? noteField.value : "";
      var result = decideCard(state, bundle, verdict, typed);
      if (result.error) {
        say(result.error);
        if (verdict === "rejected" && noteField) { noteField.focus(); }
        return;
      }
      closeNote(article);
      update(result.state);
      say((act === "yes" ? "Recorded yes for " : "Recorded no for ") + view.card.id + ".");
      send(cardVerdict(state, bundle, view.card.id));
      var shown = cardArticles[state.at];
      if (state.screen === CARD_SCREEN && shown) {
        var heading = shown.querySelector("h2");
        if (heading) { heading.focus(); }
      }
    }

    all("nav.screens button").forEach(function (button) {
      button.addEventListener("click", function () {
        update(goTo(state, Number(button.dataset.screen)));
        say("");
      });
    });
    all("button[data-next-screen]").forEach(function (button) {
      button.addEventListener("click", function () {
        update(goTo(state, Number(button.dataset.nextScreen)));
        say("");
      });
    });
    cardArticles.forEach(function (article) {
      all("button[data-act]", article).forEach(function (button) {
        button.addEventListener("click", function () { cardAct(button.dataset.act); });
      });
      wireNote(article, function () { cardAct("submit"); });
      var clear = article.querySelector("button[data-clear]");
      if (clear) {
        clear.addEventListener("click", function () {
          closeNote(article);
          var field = article.querySelector("textarea");
          if (field) { field.value = ""; }
          update(clearCard(state, bundle));
          say("Answer removed.");
        });
      }
    });
    function assumptionAct(item, verdict) {
      var index = Number(item.dataset.index);
      var noteField = item.querySelector("textarea");
      var result = decideAssumption(state, bundle, index, verdict, noteField ? noteField.value : "");
      if (result.error) {
        say(result.error);
        if (noteField) { noteField.focus(); }
        return;
      }
      closeNote(item);
      update(result.state);
      say("Recorded.");
      send(assumptionVerdict(state, bundle, index));
    }

    all("li.assumption").forEach(function (item) {
      all("button[data-verdict]", item).forEach(function (button) {
        button.addEventListener("click", function () { assumptionAct(item, button.dataset.verdict); });
      });
      var opener = item.querySelector("button[data-open-note]");
      if (opener) {
        opener.addEventListener("click", function () {
          var given = state.assumptions[item.dataset.index];
          openNote(item, given && given.verdict === "disputed" ? given.note : "");
          say("");
        });
      }
      wireNote(item, function () { assumptionAct(item, "disputed"); });
    });
    var reviewer = document.getElementById("reviewer");
    if (reviewer) {
      reviewer.addEventListener("input", function () { update(setReviewer(state, reviewer.value)); });
    }
    var download = document.getElementById("download");
    if (download) {
      download.addEventListener("click", function () {
        var text = JSON.stringify(exportVerdicts(state, bundle), null, 2) + "\n";
        try {
          var blob = new Blob([text], { type: "application/json" });
          var link = document.createElement("a");
          link.download = "verdicts.json";
          link.href = URL.createObjectURL(blob);
          document.body.appendChild(link);
          link.click();
          document.body.removeChild(link);
          setTimeout(function () { URL.revokeObjectURL(link.href); }, 1000);
          say("Downloaded verdicts.json. If nothing was saved, copy the text in the box below.");
        } catch (err) {
          say("This viewer blocked the download. Copy the text in the box below instead.");
        }
      });
    }
    document.addEventListener("keydown", function (event) {
      if (state.screen !== CARD_SCREEN || event.altKey || event.ctrlKey || event.metaKey) { return; }
      var tag = event.target && event.target.tagName;
      var inField = tag === "TEXTAREA" || tag === "INPUT" || tag === "SELECT";
      var act = keyAction(event.key, inField);
      if (!act) { return; }
      event.preventDefault();
      cardAct(act);
    });
    refresh();
  }

  root.GaoReview = pure;
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})(this);
