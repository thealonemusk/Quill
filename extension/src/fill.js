/**
 * Fills -> DOM.
 *
 * Two things make this harder than `el.value = x`:
 *
 *   1. React (Greenhouse, Lever, Ashby, Workday, Glassdoor's smartapply) tracks
 *      input values on the node itself. Assigning `.value` bypasses the tracker
 *      and the framework reverts the field on the next render, so every write
 *      goes through the native prototype setter and then fires input+change.
 *   2. Workday has no <select>. Picking an option means: click the button, wait
 *      for the listbox to render, type into its searchBox if there is one, then
 *      click the matching [data-automation-id="promptOption"]. That is inherently
 *      async, so the whole filler is sequential and awaited.
 */
(function () {
  const TA = (window.__TA = window.__TA || {});

  const OPTION_SELECTOR = [
    '[data-automation-id="promptOption"]',
    '[data-automation-id="promptLeafNode"]',
    '[data-automation-id="selectWidget-option"]',
    '[role="option"]',
    ".select__option",
    '[class*="select__option"]',
    "li[data-value]",
  ].join(", ");

  const SEARCH_BOX_SELECTOR =
    '[data-automation-id="searchBox"], input[placeholder*="Search" i], input[role="combobox"], .select__input input, input[id*="react-select"]';

  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

  const norm = (s) => (s || "").replace(/\s+/g, " ").trim().toLowerCase();

  // ── Option matching ────────────────────────────────────────────────────────
  //
  // Substring matching both ways is how a sponsorship "No" clicked "Yes, I
  // will now or in the future require sponsorship" — "now" contains "no" —
  // and how a skill "C" committed "C#" or "CSS". An option is taken only when
  // it IS the answer, or one begins with the other as a whole word, or the
  // answer sits inside it as a whole word. A bare Yes/No only ever matches an
  // option that begins with that same word, and an option whose polarity
  // differs from the answer's (one negated, the other not) never matches.

  // Characters that end a word. `#`, `+`, `.` and `-` are deliberately not
  // among them: "C#", "C++", "Node.js" and "Objective-C" are different answers
  // from "C" or "Node".
  const WORD_DELIM = /[\s()[\],/|:;!?]/;

  function boundaryAfter(text, i) {
    if (i >= text.length) return true;
    const ch = text[i];
    if (WORD_DELIM.test(ch)) return true;
    // "No." / "No -" at the end of a phrase still ends the word.
    return (ch === "." || ch === "-") && (i + 1 >= text.length || /\s/.test(text[i + 1]));
  }

  function startsWithWord(text, word) {
    return !!word && text.startsWith(word) && boundaryAfter(text, word.length);
  }

  function containsWord(text, word) {
    if (!word) return false;
    let from = 0;
    for (;;) {
      const i = text.indexOf(word, from);
      if (i < 0) return false;
      if ((i === 0 || WORD_DELIM.test(text[i - 1])) && boundaryAfter(text, i + word.length)) return true;
      from = i + 1;
    }
  }

  function leadingYesNo(text) {
    if (startsWithWord(text, "yes")) return "yes";
    if (startsWithWord(text, "no")) return "no";
    return "";
  }

  const NEGATION = /(?:^|[^a-z])(?:no|not|never|none|cannot)(?![a-z])|n['’]t(?![a-z])/;
  const isNegated = (text) => NEGATION.test(text);

  /**
   * How well an option's text answers `target` (both normalised): 0 exact,
   * 1 the same leading Yes/No, 2 option starts with the answer, 3 answer
   * starts with the option, 4 answer is a whole word inside the option;
   * -1 is no match at all.
   */
  function matchRank(text, target) {
    if (!text || !target) return -1;
    if (text === target) return 0;
    if (TRANSIENT_OPTION.test(text)) return -1;
    const lead = leadingYesNo(target);
    if (lead) {
      if (leadingYesNo(text) !== lead) return -1;
      if (target === lead) return 1;
    }
    if (isNegated(text) !== isNegated(target)) return -1;
    if (startsWithWord(text, target)) return 2;
    if (startsWithWord(target, text)) return 3;
    if (containsWord(text, target)) return 4;
    return -1;
  }

  /**
   * The index of the option that answers `want`, or -1. `exactOnly` is for a
   * menu that cannot be tied to the widget being filled: there, only a row
   * that reads exactly as the answer may be clicked.
   *
   * There is no "first row" or "only row" fallback, on purpose. A row whose
   * text does not match is not an answer, however alone it is on screen.
   */
  function bestOptionIndex(texts, want, exactOnly) {
    const target = norm(want);
    if (!target) return -1;
    let best = -1;
    let bestRank = Infinity;
    texts.forEach((raw, i) => {
      const text = norm(raw);
      const rank = matchRank(text, target);
      if (rank < 0 || (exactOnly && rank !== 0)) return;
      // Among equals the most specific (shortest) option wins.
      if (rank < bestRank || (rank === bestRank && text.length < norm(texts[best]).length)) {
        best = i;
        bestRank = rank;
      }
    });
    return best;
  }

  TA.bestOptionIndex = bestOptionIndex;
  // Exposed for the test suite, like bestOptionIndex; hoisted from below.
  TA.chipCount = (el) => chipCount(el);

  function isVisible(el) {
    if (!el || !el.isConnected) return false;
    const rect = el.getBoundingClientRect();
    if (rect.width <= 0 || rect.height <= 0) return false;
    const style = window.getComputedStyle(el);
    return style.display !== "none" && style.visibility !== "hidden";
  }

  /** Write a value the way a user would, so React's value tracker stays in sync. */
  function setNativeValue(el, value) {
    const proto =
      el instanceof HTMLTextAreaElement
        ? HTMLTextAreaElement.prototype
        : el instanceof HTMLSelectElement
        ? HTMLSelectElement.prototype
        : HTMLInputElement.prototype;
    const descriptor = Object.getOwnPropertyDescriptor(proto, "value");
    if (descriptor && descriptor.set) {
      descriptor.set.call(el, value);
    } else {
      el.value = value;
    }
    el.dispatchEvent(new Event("input", { bubbles: true }));
    el.dispatchEvent(new Event("change", { bubbles: true }));
  }

  /**
   * Type into a field.
   *
   * `keepFocus` matters more than it looks. A plain text input wants the blur:
   * that is what makes a portal run its validation. A dropdown's search box
   * does not — Workday closes the listbox on blur, so blurring straight after
   * typing tore the menu down before the option could be clicked, and every
   * skill silently went nowhere.
   */
  function fillText(el, value, keepFocus) {
    if (!isVisible(el) || el.disabled || el.readOnly) return false;
    try {
      el.focus({ preventScroll: true });
    } catch (e) {
      /* some widgets refuse focus */
    }
    if (el.isContentEditable) {
      el.textContent = value;
      el.dispatchEvent(new InputEvent("input", { bubbles: true }));
      if (!keepFocus) el.blur();
      return true;
    }
    setNativeValue(el, "");
    setNativeValue(el, value);
    if (!keepFocus) el.dispatchEvent(new Event("blur", { bubbles: true }));
    return norm(el.value) === norm(value) || el.value.length > 0;
  }

  /**
   * Press Enter on a field, the way a person commits a typed value.
   *
   * `keyCode`/`which` are legacy and read-only, so they cannot be passed
   * through the constructor — but plenty of widgets still read them, so they
   * are defined onto the event by hand.
   */
  function pressEnter(el) {
    for (const type of ["keydown", "keypress", "keyup"]) {
      const ev = new KeyboardEvent(type, {
        key: "Enter",
        code: "Enter",
        bubbles: true,
        cancelable: true,
      });
      try {
        Object.defineProperty(ev, "keyCode", { get: () => 13 });
        Object.defineProperty(ev, "which", { get: () => 13 });
      } catch (e) {
        /* non-configurable in some engines; key/code still carry it */
      }
      el.dispatchEvent(ev);
    }
  }

  // A committed entry in a multi-value picker, however the portal renders it.
  //
  // `selectedItemList` is the *container* Workday puts its chips in, and a
  // loose `*="selectedItem"` matched it. The count was therefore 1 before a
  // skill was added and 1 after, so `chipCount(el) > before` was never true
  // and every committed value was reported as a failure — and then wiped,
  // because the caller clears the box when it believes nothing landed.
  const CHIP_SELECTOR = [
    '[data-automation-id="selectedItem"]',
    '[data-automation-id^="selectedItem"]:not([data-automation-id$="List"])',
    '[data-automation-id="selectedItemList"] > li',
    '[data-automation-id="pill"]',
    '[class*="multi-value"]',
    '[class*="chip"]',
    '[class*="pill"]',
    '[class*="token"]',
  ].join(", ");

  /** How many values this picker currently holds. */
  function chipCount(el) {
    try {
      const nodes = Array.from(widgetScope(el).querySelectorAll(CHIP_SELECTOR));
      // A node that holds other chips is the list, not an entry in it. And a
      // chip shows its value: an empty `class="chips-container"` matched
      // [class*="chip"] and counted as one, the selectedItemList bug again.
      return nodes.filter(
        (n) => !nodes.some((other) => other !== n && n.contains(other)) && (n.textContent || "").trim()
      ).length;
    } catch (e) {
      return 0;
    }
  }

  // ── Workday prompt widgets ─────────────────────────────────────────────────
  //
  // Workday's searchable pickers (Skills, Degree, Field of Study, Source) are
  // not "type into a box and click a row". Two facts make them work, both
  // taken from job_app_filler by Berel Levy (BSD-3-Clause,
  // github.com/berellevy/job_app_filler), which drives these widgets reliably:
  //
  //   1. The value is committed through React's own `onKeyDown` prop with a
  //      Tab key and the answer as `target.value`. The widget is React
  //      controlled, so a typed DOM value is state React never adopts.
  //   2. The menu is not inside the field. It is a popup at body level, tied
  //      back to its widget by `data-associated-widget`. Scanning the page for
  //      options finds some other control's menu as readily as this one's.
  //
  // Which is why typing and waiting never worked here no matter how long it
  // waited: nothing was listening to what was typed.

  const WD_CONTAINER = '[data-automation-id="multiSelectContainer"]';
  const WD_CHIP = '[data-automation-id="selectedItemList"] > li';
  const WD_OPTION = '[data-automation-id="promptOption"], [data-automation-id="promptLeafNode"]';
  const WD_POPUP = '[data-automation-widget="wd-popup"]';

  /**
   * Call React's handler on `el` through react-bridge.js, which runs in the
   * page's world. Reading `__reactProps$…` from here found nothing: a content
   * script is in an isolated world and never sees the page's JS properties,
   * so on a live tenant every prompt looked prop-less and Skills fell back to
   * typing. Returns "ok", "noprops", or "nobridge" when nothing answered.
   */
  let reactSeq = 0;
  function viaBridge(el, msg) {
    const token = `ta${Date.now().toString(36)}${(reactSeq += 1)}`;
    el.setAttribute("data-tempoapply-react", token);
    el.removeAttribute("data-tempoapply-react-result");
    try {
      document.dispatchEvent(
        new CustomEvent("tempoapply:react", { detail: JSON.stringify({ token, ...msg }) })
      );
      return el.getAttribute("data-tempoapply-react-result") || "nobridge";
    } finally {
      el.removeAttribute("data-tempoapply-react");
      el.removeAttribute("data-tempoapply-react-result");
    }
  }

  async function waitFor(test, timeoutMs) {
    const deadline = Date.now() + (timeoutMs || 800);
    while (Date.now() < deadline) {
      try {
        if (test()) return true;
      } catch (e) {
        /* keep waiting */
      }
      await sleep(80);
    }
    return false;
  }

  /** The multi-select container that owns `el`, if this is a Workday prompt. */
  function workdayContainer(el) {
    if (!el || !el.closest) return null;
    return el.closest(WD_CONTAINER) || el.querySelector?.(WD_CONTAINER) || null;
  }

  /**
   * The popup holding this widget's options.
   *
   * Tied by `data-associated-widget`, so a second picker open elsewhere on the
   * page cannot supply the rows this one gets clicked from.
   */
  function workdayPopup(container) {
    const popups = Array.from(document.querySelectorAll(WD_POPUP)).filter(isVisible);
    if (!popups.length) return { popup: null, owned: false };

    const input = container && container.querySelector("input");
    const ids = [
      container && container.id,
      input && input.id,
      input && input.getAttribute("aria-controls"),
      input && input.getAttribute("aria-owns"),
      container && container.getAttribute("aria-controls"),
    ].filter(Boolean);

    for (const id of ids) {
      let own = null;
      try {
        own = popups.find(
          (p) =>
            p.getAttribute("data-associated-widget") === id ||
            p.id === id ||
            p.querySelector(`[data-associated-widget="${CSS.escape(id)}"]`)
        );
      } catch (e) {
        own = null;
      }
      if (own) return { popup: own, owned: true };
    }

    // Workday appends the newest popup last. Taking it is a guess, so the
    // caller is told it is one — an unowned menu only gets clicked on a row
    // whose text actually matches, never on "it was the only row".
    return { popup: popups[popups.length - 1], owned: false };
  }

  /**
   * The rows this prompt is offering, waiting for them to arrive.
   *
   * The search runs on the server. Looking once, immediately, found an empty
   * menu and clicked nothing — the widget sat there showing results with
   * nothing selected, which is exactly what a search that never selects looks
   * like. If the popup cannot be tied to this widget, prompt rows anywhere
   * are still better than none, and the caller makes up for the uncertainty
   * by demanding a text match.
   */
  async function workdayRows(container, timeoutMs) {
    const deadline = Date.now() + (timeoutMs || 3000);
    let last = { rows: [], owned: false };
    while (Date.now() < deadline) {
      const { popup, owned } = workdayPopup(container);
      const scope = popup || document;
      const rows = Array.from(scope.querySelectorAll(WD_OPTION)).filter(isVisible);
      if (rows.length) return { rows, owned: owned && !!popup };
      last = { rows, owned };
      await sleep(120);
    }
    return last;
  }

  function workdayChips(container) {
    try {
      return container.querySelectorAll(WD_CHIP).length;
    } catch (e) {
      return 0;
    }
  }

  /** What this widget itself displays, normalised. */
  function shownIn(container) {
    try {
      return norm(container.innerText || container.textContent || "");
    } catch (e) {
      return "";
    }
  }

  /**
   * Feed values into a Workday prompt. Returns the number committed, or null
   * when this is not a Workday prompt and the generic path should run.
   */
  async function fillWorkdayPrompt(el, values) {
    const container = workdayContainer(el);
    if (!container) return null;
    const input = container.querySelector("input");
    if (!input) return null;
    if (viaBridge(input, { op: "probe" }) !== "ok") return null;

    // One line per value, so a live tenant that commits one skill of ten says
    // which step failed for the other nine. Recording only; nothing here
    // changes what gets clicked. Read it with TA.skillTrace in the console.
    const trace = [];
    TA.skillTrace = trace;

    let picked = 0;
    for (const value of values) {
      const step = { value, chipsBefore: 0, tab: "", committedByTab: false,
                     popup: "", rows: [], picked: -1, clicked: "", landed: false, chipsAfter: 0 };
      trace.push(step);
      const before = workdayChips(container);
      step.chipsBefore = before;
      const shownBefore = shownIn(container);
      // Evidence that THIS widget took `text`: a new chip in its own list, or
      // its own display newly reading the value. Never "some popup closed" —
      // the value goes in through React's handler, so the input is always
      // empty, and any menu closing anywhere made that check pass.
      const landed = (text) =>
        workdayChips(container) > before ||
        (!containsWord(shownBefore, norm(text)) && containsWord(shownIn(container), norm(text)));
      input.focus({ preventScroll: true });
      step.tab = viaBridge(input, { op: "keydown", key: "Tab", value });
      if (step.tab !== "ok") continue;

      // The Tab keydown runs the search. On most values it also commits, but
      // where the taxonomy has more than one hit it just leaves the menu open
      // — the row still has to be clicked.
      let ok = await waitFor(() => landed(value), 700);
      step.committedByTab = ok;

      if (!ok) {
        const { rows, owned } = await workdayRows(container, 3000);
        const texts = rows.map((r) => r.innerText || r.textContent);
        step.popup = rows.length ? (owned ? "owned" : "unowned") : "none";
        step.rows = texts.slice(0, 8).map((t) => norm(t).slice(0, 60));
        // A popup that cannot be tied to this widget may be another control's
        // menu, so only a row reading exactly as the answer is clicked there.
        const i = bestOptionIndex(texts, value, !owned);
        step.picked = i;
        const row = i >= 0 ? rows[i] : null;
        if (row) {
          const rowText = row.innerText || row.textContent || value;
          step.clicked = norm(rowText).slice(0, 60);
          row.scrollIntoView({ block: "nearest" });
          row.click();
          ok = await waitFor(() => landed(rowText) || landed(value), 1200);
        }
      }
      step.landed = ok;
      step.chipsAfter = workdayChips(container);
      if (ok) picked += 1;
      await sleep(150);
    }

    // The popup is dismissed rather than left hanging over the next field.
    document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
    try {
      console.info(`[Quill] skills: ${picked}/${values.length} committed`);
      console.table(trace.map((s) => ({ ...s, rows: s.rows.join(" | ") })));
    } catch (e) { /* the trace must never cost the fill */ }
    return picked;
  }

  function fillSelect(el, label) {
    if (!isVisible(el) || el.disabled) return false;
    const options = Array.from(el.options);
    const want = norm(label);
    let match =
      options.find((o) => norm(o.text) === want) ||
      options.find((o) => norm(o.value) === want && want) ||
      options[bestOptionIndex(options.map((o) => o.text), label)];
    if (!match) return false;
    el.focus({ preventScroll: true });
    setNativeValue(el, match.value);
    return true;
  }

  const isChecked = (el) => !!el.checked || el.getAttribute("aria-checked") === "true";

  /**
   * Tick a checkbox.
   *
   * The state is read back after a wait, not on the line after the click.
   * Workday's checkbox is React controlled and reports through `aria-checked`
   * a tick later, so reading `el.checked` immediately said false, and the old
   * fallback then forced `el.checked = true` by hand — which React owns and
   * reverts, leaving the box visibly unticked while the run reported success.
   */
  async function fillCheckbox(el) {
    if (!isVisible(el) || el.disabled) return false;
    if (isChecked(el)) return true;
    el.scrollIntoView({ block: "center" });
    el.click();
    if (await waitFor(() => isChecked(el), 600)) return true;

    // Some tenants only bind the handler to the visible label.
    const label = (el.labels && el.labels[0]) || el.closest("label");
    if (label) {
      label.click();
      if (await waitFor(() => isChecked(el), 600)) return true;
    }
    return isChecked(el);
  }

  // Workday writes a date as two spinbuttons, aria-label "Month" and "Year"
  // (plus "Day" where the day is asked for). The empty pair is what renders as
  // "MM/YYYY".
  const DATE_PART_LABEL = /^(month|day|year)$/i;

  function isDatePart(el) {
    if (!el || el.tagName !== "INPUT") return false;
    if (DATE_PART_LABEL.test((el.getAttribute("aria-label") || "").trim())) return true;
    const auto = el.getAttribute("data-automation-id") || "";
    return /dateSection(Month|Day|Year)/i.test(auto) || el.getAttribute("role") === "spinbutton";
  }

  /**
   * Write one part of a Workday date.
   *
   * Setting the value directly does not stick: the widget is a spinbutton and
   * React re-renders from its own state. Setting it one short and pressing
   * ArrowUp makes React do the increment itself, so the value it lands on is
   * state it owns. This is the trick from job_app_filler (BSD-3-Clause).
   */
  async function fillDatePart(el, value) {
    const n = parseInt(String(value).trim(), 10);
    if (!Number.isFinite(n)) return false;
    try {
      el.focus({ preventScroll: true });
      setNativeValue(el, String(n - 1));
      el.dispatchEvent(
        new KeyboardEvent("keydown", { key: "ArrowUp", code: "ArrowUp", bubbles: true })
      );
      el.click();
    } catch (e) {
      return false;
    }
    return valueHolds(el, (v) => parseInt(v, 10) === n);
  }

  // How long a written value must survive before it counts. React reverts a
  // value it does not own on its next render, so reading it back on the line
  // after the write always agrees with what was just written.
  const HOLD_MS = 250;

  async function valueHolds(el, test) {
    if (!test(el.value)) return false;    await sleep(HOLD_MS);
    return el.isConnected && test(el.value);
  }

  /**
   * Choose a radio option.
   *
   * Same rule as the checkbox: click, then wait for the state. Forcing
   * `el.checked = true` when the click did not register is the pattern React
   * reverts on its next render — the option shows green and nothing is
   * recorded.
   */
  async function fillRadio(el) {
    if (!isVisible(el) || el.disabled) return false;
    if (isChecked(el)) return true;
    el.scrollIntoView({ block: "center" });
    el.click();    if (await waitFor(() => isChecked(el), 600)) return true;

    const label = (el.labels && el.labels[0]) || el.closest("label");
    if (label) {
      label.click();
      if (await waitFor(() => isChecked(el), 600)) return true;
    }
    return isChecked(el);
  }

  /**
   * The options on screen right now.
   *
   * The `.iti__country-list` exclusion is load-bearing: roughly 240 hidden
   * phone-country rows sort first and consume the scan window, which makes
   * every dropdown look like it never opened.
   */
  function visibleOptions() {
    return Array.from(document.querySelectorAll(OPTION_SELECTOR)).filter(
      (o) => isVisible(o) && !o.closest(".iti__country-list")
    );
  }

  // Rows a listbox shows while it is still working. They are not answers, and
  // clicking one picks nothing.
  const TRANSIENT_OPTION =
    /^(loading|searching|please wait|no (matching )?(results|items|options)|no results found|start typing|type to search|search)\b/;

  // How long to keep waiting for a menu that has not reacted to the typing at
  // all. Past this it is a static list that simply does not hold the answer,
  // and waiting out the full deadline on every value is what once cost 15
  // seconds across ten skills.
  const STALE_GRACE_MS = 1200;

  function optionKey(texts) {
    return texts.join("|");
  }

  /**
   * Click the option whose text best matches `want`, from an open listbox.
   *
   * A match is taken the moment it appears. A *miss*, though, is only believed
   * once the menu has actually responded to what was typed — it must differ
   * from what was on screen when this started, and then hold still for one
   * more sample.
   *
   * Workday's prompts search server-side. The menu is already open holding the
   * previous value's rows, or a "Loading" row, and the real results land a beat
   * later. Deciding on the first non-empty render therefore answered from the
   * stale list and returned false for every skill — while a person typing the
   * same text watched the match appear and clicked it. That is the whole
   * "manual search works, the extension does not" report.
   */
  async function pickOpenOption(want, deadlineMs) {
    const target = norm(want);
    const deadline = Date.now() + (deadlineMs || 2500);
    const started = Date.now();
    let entryKey = null;
    let previous = null;

    while (Date.now() < deadline) {
      const options = visibleOptions();
      const texts = options.map((o) => norm(o.innerText || o.textContent));
      const key = optionKey(texts);
      if (entryKey === null) entryKey = key;

      if (options.length) {
        // Whole-word matching only: "No" must never land on "Yes, I will now
        // or in the future require sponsorship" because "now" contains "no".
        const i = bestOptionIndex(texts, target);
        if (i >= 0) {
          options[i].scrollIntoView({ block: "nearest" });
          options[i].click();
          return true;
        }
        // Believe a miss only once these rows are this query's own answer.
        const answered = key !== entryKey || Date.now() - started > STALE_GRACE_MS;
        if (answered && key === previous) break;
      }
      previous = key;
      await sleep(120);
    }

    // A settled list without a matching row is a miss. The lone row it shows
    // is a different answer however alone it is ("Other", a neighbour in the
    // taxonomy), and the field is left for the human rather than committed.
    return false;
  }

  /** Is any listbox rendered right now? Distinguishes "no match" from "no menu". */
  function anyOptionsVisible() {
    return visibleOptions().length > 0;
  }

  function closeListbox() {
    document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
    document.body.click();
  }

  /** The container this widget owns, so a search box can be scoped to it. */
  function widgetScope(el) {
    return (
      el.closest(
        '[data-automation-id="multiSelectContainer"], [data-automation-id*="formField"],' +
          ' [class*="select__control"], [class*="select__value-container"], fieldset,' +
          " .field, .form-field"
      ) || el
    );
  }

  /**
   * The box to type into for THIS widget.
   *
   * Scoped to the widget first, and that is the whole point: a page-wide
   * `querySelectorAll(SEARCH_BOX_SELECTOR).pop()` happily returns a search box
   * belonging to some other control — Workday usually has one on screen — and
   * then every value is typed into the wrong field and matches nothing.
   */
  function searchBoxFor(el) {
    const scope = widgetScope(el);
    const inner = Array.from(scope.querySelectorAll(SEARCH_BOX_SELECTOR)).filter(isVisible);
    if (inner.length) return inner[inner.length - 1];
    try {
      if (el.matches(SEARCH_BOX_SELECTOR) && isVisible(el)) return el;
    } catch (e) {
      /* el may not support matches */
    }
    // Only now consider a portal-rendered box, which Workday uses for prompts.
    const outer = Array.from(document.querySelectorAll(SEARCH_BOX_SELECTOR)).filter(isVisible);
    return outer.length ? outer[outer.length - 1] : null;
  }

  async function openWidget(el) {
    try {
      el.scrollIntoView({ block: "center" });
      el.focus({ preventScroll: true });
      el.click();
    } catch (e) {
      return false;
    }
    await sleep(350);
    return true;
  }

  /**
   * Drive a non-native dropdown: Workday's listbox button, react-select, or any
   * [role="combobox"]. Returns true only if an option was actually chosen.
   *
   * `value` may be a single string or a list of acceptable answers, best
   * first. The list matters because these options do not exist in the DOM
   * until the listbox opens, so the backend cannot know that this tenant
   * offers "Phone" and "Main" but no "Mobile".
   */
  async function fillCombobox(el, value) {
    if (!isVisible(el)) return false;
    const candidates = (Array.isArray(value) ? value : [value]).filter(Boolean);
    if (!candidates.length) return false;

    // A single-value Workday prompt — Degree, Field of Study, Source, phone
    // country code — commits the same React way as the Skills picker.
    for (const candidate of candidates) {
      const viaReact = await fillWorkdayPrompt(el, [candidate]);
      if (viaReact === null) break;         // not a Workday prompt
      if (viaReact > 0) return true;
    }

    if (!(await openWidget(el))) return false;

    for (const candidate of candidates) {
      const search = searchBoxFor(el);
      if (search) {
        fillText(search, candidate, true);
        await sleep(450);
      } else if (el.tagName === "INPUT") {
        fillText(el, candidate, true);
        await sleep(450);
      }
      if (await pickOpenOption(candidate, 2500)) {
        await sleep(150);
        return true;
      }
      // Re-open if rejecting the last attempt collapsed the listbox.
      if (!searchBoxFor(el)) await openWidget(el);
    }

    closeListbox();
    await sleep(150);
    return false;
  }

  /**
   * Feed several values into one picker (Workday's Skills field).
   * Each value is typed, matched and committed before the next one starts.
   */
  async function fillMultiselect(el, values) {
    // Workday first: its prompts ignore typed text entirely.
    const viaReact = await fillWorkdayPrompt(el, values);
    if (viaReact !== null) return viaReact > 0;

    let picked = 0;
    // Some pickers have no listbox at all — they are tag inputs that commit on
    // Enter. Waiting 1.5s for a menu that will never render costs 15 seconds
    // across ten skills, so once we have established there is no menu we stop
    // waiting for one.
    let hasListbox = true;
    for (const value of values) {
      // The box is resolved against this widget every round. Taking the last
      // search box on the page — computed before the picker was even opened —
      // meant that whenever another control had one visible, every skill was
      // typed into the wrong input.
      let box = searchBoxFor(el);
      if (!box) {
        await openWidget(el);
        box = searchBoxFor(el);
      }
      if (!box && el.tagName === "INPUT") box = el;
      if (!box) continue;

      const before = chipCount(el);
      fillText(box, value, true);          // never blur: that closes the menu
      await sleep(450);

      // Prefer the portal's own taxonomy entry when it offers one...
      let ok = false;
      if (hasListbox) {
        // A generous deadline costs nothing now: a miss is settled by the menu
        // going quiet, not by running the clock out. What the deadline buys is
        // room for a slow taxonomy search on the far side of a server.
        ok = await pickOpenOption(value, 2500);
        if (!ok && !anyOptionsVisible()) hasListbox = false;
      }

      // ...otherwise commit the typed text with Enter, which is how a person
      // adds a skill to a tag input: type it, press Enter, type the next one.
      // Only where no menu is showing, though. With rows on screen Enter
      // commits whichever one is highlighted — react-select's first row — so
      // a value the menu does not hold would land as some other answer.
      if (!ok && !anyOptionsVisible()) {
        pressEnter(box);
        await sleep(400);
        ok = chipCount(el) > before;
      }

      if (ok) {
        picked += 1;
      } else {
        // Leave nothing half-typed — a stray value commits itself on blur.
        fillText(box, "", true);
      }
      await sleep(200);
    }

    const box = searchBoxFor(el);
    if (box) fillText(box, "", true);
    closeListbox();
    return picked > 0;
  }

  /** Put the resume bytes into a file input the way a file picker would. */
  function fillFile(el, file) {
    if (!file) return false;
    try {
      const dt = new DataTransfer();
      dt.items.add(file);
      el.files = dt.files;
      el.dispatchEvent(new Event("input", { bubbles: true }));
      el.dispatchEvent(new Event("change", { bubbles: true }));
      // Some boards listen for a drop on the dropzone rather than on the input.
      const zone = el.closest('[class*="dropzone" i], [class*="drop-zone" i], [data-automation-id*="attachment" i]');
      if (zone) {
        zone.dispatchEvent(new DragEvent("drop", { bubbles: true, dataTransfer: dt }));
      }
      return el.files && el.files.length > 0;
    } catch (e) {
      return false;
    }
  }

  function base64ToFile(b64, filename, mime) {
    const binary = atob(b64);
    const bytes = new Uint8Array(binary.length);
    for (let i = 0; i < binary.length; i += 1) bytes[i] = binary.charCodeAt(i);
    return new File([bytes], filename, { type: mime || "application/pdf" });
  }

  TA.base64ToFile = base64ToFile;

  /** Answers this dropdown will accept, best first. `values` is optional. */
  function comboCandidates(fill) {
    if (Array.isArray(fill.values) && fill.values.length) return fill.values;
    return [fill.value];
  }

  /**
   * Apply every fill in order. `resumeFile` may be null — file fills are then
   * reported as failures so the widget can tell the user to attach it by hand.
   */
  TA.applyFills = async function applyFills(fills, elements, resumeFile, onProgress) {
    const applied = [];
    const failed = [];

    for (const fill of fills) {
      if (fill.action === "skip") continue;
      const el = elements.get(String(fill.idx));
      if (!el || !el.isConnected) {
        failed.push({ ...fill, why: "element gone" });
        continue;
      }

      let ok = false;
      try {
        switch (fill.action) {
          case "text":
            // A Workday date part will not take a typed value, so the write is
            // checked rather than assumed and redone through the spinbutton.
            if (isDatePart(el)) {
              ok = await fillDatePart(el, fill.value);
              // The typed fallback has to survive React's next render, not
              // just read back on the line after it was written.
              if (!ok) {
                ok = fillText(el, fill.value) &&
                  (await valueHolds(el, (v) => norm(v) === norm(fill.value)));
              }
            } else {
              ok = fillText(el, fill.value);
            }
            break;
          case "select":
            ok = fillSelect(el, fill.value);
            if (!ok) ok = await fillCombobox(el, comboCandidates(fill));
            break;
          case "combobox":
            ok = await fillCombobox(el, comboCandidates(fill));
            break;
          case "multiselect":
            ok = await fillMultiselect(el, fill.values && fill.values.length ? fill.values : [fill.value]);
            break;
          case "checkbox":
            ok = await fillCheckbox(el);
            break;
          case "radio":
            ok = await fillRadio(el);
            break;
          case "file":
            ok = fillFile(el, resumeFile);
            break;
          default:
            ok = false;
        }
      } catch (e) {
        ok = false;
      }

      if (ok) {
        TA.mark(el, fill.confidence === "low" ? "low" : "ok");
        applied.push(fill);
      } else {
        TA.mark(el, "fail");
        failed.push({ ...fill, why: "could not set" });
      }
      if (onProgress) onProgress(applied.length, failed.length);
      await sleep(fill.action === "combobox" || fill.action === "select" || fill.action === "multiselect" ? 60 : 25);
    }

    return { applied, failed };
  };

  // Repeating sections start collapsed behind an Add button. Each kind is
  // matched by its Workday container id first, then by the section heading, so
  // a portal that does not use those ids still works.
  const SECTION_SPECS = {
    experience: {
      entry: '[data-automation-id^="workExperience-"], [data-automation-id^="work-experience-"]',
      section: '[data-automation-id="workExperienceSection"], [data-automation-id*="workExperience"]',
      heading: /work experience|employment/i,
    },
    education: {
      entry: '[data-automation-id^="education-"]',
      section: '[data-automation-id="educationSection"], [data-automation-id*="education"]',
      heading: /education|qualification/i,
    },
    website: {
      entry: '[data-automation-id^="websitePanelSet-"], [data-automation-id^="website-"]',
      section: '[data-automation-id="websiteSection"], [data-automation-id*="website"]',
      heading: /websites?|social|links?/i,
    },
  };

  // A label that appears exactly once per entry, used to count entries on a
  // tenant whose container ids we do not recognise.
  const ENTRY_ANCHORS = {
    experience: /^(job\s*title|company|employer)$/i,
    education: /^(school(\s+or\s+university)?|university|institution)$/i,
    website: /^(url|website)$/i,
  };

  function anchorCount(kind) {
    const pattern = ENTRY_ANCHORS[kind];
    if (!pattern || typeof TA.scrape !== "function") return 0;
    try {
      return TA.scrape().fields.filter((f) =>
        pattern.test(norm(f.label || f.group_label || ""))
      ).length;
    } catch (e) {
      return 0;
    }
  }

  /**
   * How many entries of this section are on the page.
   *
   * The id-based count is the accurate one, but it returns 0 on any tenant
   * that does not use `workExperience-N`. That zero used to make
   * `expandSections` click Add once and then break on its own runaway guard
   * (`countEntries(spec) <= i` is `0 <= 0`), so a second employer was never
   * opened and the profile's entry 2 silently vanished from the application.
   * Counting the per-entry anchor label recovers the real number.
   */
  function countEntries(spec, kind) {
    const byId = document.querySelectorAll(spec.entry).length;
    if (byId) return byId;
    return anchorCount(kind);
  }

  /** The Add / Add Another button that belongs to this section. */
  /** The accessible name of a control, however the page chose to provide it. */
  function buttonName(el) {
    const aria = el.getAttribute("aria-label") || "";
    const auto = el.getAttribute("data-automation-id") || "";
    return norm([el.innerText, aria, auto].filter(Boolean).join(" "));
  }

  function sectionContainer(spec) {
    // An entry id like workExperience-1 also matches a loose *="workExperience"
    // selector, so a naive querySelector can return a single entry and the Add
    // button search is then scoped inside it, where no Add button exists.
    const candidates = Array.from(document.querySelectorAll(spec.section))
      .filter((node) => !node.matches(spec.entry));
    if (candidates.length) {
      // Outermost wins, so the section is not confused with a panel inside it.
      return candidates.reduce((a, b) => (a.contains(b) ? a : b));
    }
    return (
      Array.from(document.querySelectorAll("section, div, fieldset")).find((node) => {
        const head = node.querySelector("h2, h3, h4, legend, [role='heading']");
        return head && spec.heading.test(head.innerText || "");
      }) || null
    );
  }

  function addButtonFor(spec) {
    const scope = sectionContainer(spec) || document;
    const buttons = Array.from(
      scope.querySelectorAll(
        'button, [role="button"], a[role="button"], [data-automation-id="Add"]'
      )
    ).filter(isVisible);
    // Exact match first, so "Add Another" wins over an unrelated "Add to list".
    return (
      buttons.find((b) => /^add( another)?$/.test(buttonName(b))) ||
      buttons.find((b) => /(^| )add( |$)/.test(buttonName(b))) ||
      null
    );
  }

  /**
   * Open as many entries as the profile can fill. Returns how many were added,
   * so the caller knows whether to re-scrape.
   */
  TA.expandSections = async function expandSections(needed) {
    let added = 0;
    for (const [kind, want] of Object.entries(needed || {})) {
      const spec = SECTION_SPECS[kind];
      if (!spec || !want) continue;
      let have = countEntries(spec, kind);
      let guard = 0;
      while (have < want && guard < want + 2) {
        guard += 1;
        const button = addButtonFor(spec);
        if (!button) break;
        button.click();
        added += 1;
        await sleep(700);
        const now = countEntries(spec, kind);
        // Stop only if the click genuinely opened nothing — measured against
        // the count before the click, not against the loop index.
        if (now <= have) break;
        have = now;
      }
    }
    if (added) await sleep(600);
    return added;
  };

  // ── Section picking ────────────────────────────────────────────────────────
  //
  // Filling one part of a long form at a time. Workday's My Experience step is
  // the case that needs it: the user wants to deal with Work Experience, check
  // it, then move on to Education — rather than have every block written at
  // once and audit the lot.

  const PICK_FIELD_SELECTOR =
    'input:not([type="hidden"]), textarea, select, [role="combobox"],' +
    ' [data-automation-id="multiSelectContainer"], button[aria-haspopup="listbox"]';

  /**
   * The smallest block around `el` that holds more than one field.
   *
   * Stopping at the first container with two or more fields is what keeps the
   * selection tight — walking to the outermost match would just reselect the
   * whole form, which is the thing being avoided.
   */
  function sectionAround(el) {
    let node = el;
    let fallback = null;
    while (node && node !== document.body && node !== document.documentElement) {
      const count = node.querySelectorAll ? node.querySelectorAll(PICK_FIELD_SELECTOR).length : 0;
      if (count >= 1 && !fallback) fallback = node;
      if (count >= 2) return node;
      node = node.parentElement;
    }
    return fallback || el;
  }

  function fieldCount(node) {
    try {
      return node.querySelectorAll(PICK_FIELD_SELECTOR).length;
    } catch (e) {
      return 0;
    }
  }

  /**
   * Let the user point at a section. Resolves with the chosen element, or
   * null if they cancelled.
   */
  let activePick = null;

  TA.pickSection = function pickSection() {
    return new Promise((resolve) => {
      const box = document.createElement("div");
      box.setAttribute("data-ta-picker", "");
      box.style.cssText = [
        "position:fixed", "z-index:2147483646", "pointer-events:none",
        "border:2px solid #2563eb", "background:rgba(37,99,235,.10)",
        "border-radius:6px", "transition:all .06s linear", "display:none",
      ].join(";");

      const tip = document.createElement("div");
      tip.setAttribute("data-ta-picker", "");
      tip.style.cssText = [
        "position:fixed", "z-index:2147483647", "pointer-events:none",
        "background:#111827", "color:#fff", "font:12px/1.4 system-ui,sans-serif",
        "padding:5px 9px", "border-radius:6px", "box-shadow:0 2px 8px rgba(0,0,0,.3)",
      ].join(";");
      tip.textContent = "Click a section to fill it · Esc to cancel";

      document.documentElement.append(box, tip);
      const previousCursor = document.documentElement.style.cursor;
      document.documentElement.style.cursor = "crosshair";

      let current = null;

      const cleanup = () => {
        activePick = null;
        document.removeEventListener("mousemove", onMove, true);
        document.removeEventListener("click", onClick, true);
        document.removeEventListener("keydown", onKey, true);
        box.remove();
        tip.remove();
        document.documentElement.style.cursor = previousCursor;
      };

      // So another frame winning the click can tear this one down — and
      // actually settle the promise, not just remove the overlay.
      activePick = () => {
        cleanup();
        resolve(null);
      };

      const onMove = (e) => {
        const target = document.elementFromPoint(e.clientX, e.clientY);
        // Never offer our own overlay or the panel as a section.
        if (!target || target.closest("[data-ta-picker], #tempoapply-widget-host")) return;
        const section = sectionAround(target);
        if (!section) return;
        current = section;
        const r = section.getBoundingClientRect();
        box.style.display = "block";
        box.style.left = `${r.left}px`;
        box.style.top = `${r.top}px`;
        box.style.width = `${r.width}px`;
        box.style.height = `${r.height}px`;
        const n = fieldCount(section);
        tip.textContent = `${n} field${n === 1 ? "" : "s"} · click to fill · Esc to cancel`;
        tip.style.left = `${Math.min(e.clientX + 14, window.innerWidth - 240)}px`;
        tip.style.top = `${Math.max(e.clientY - 34, 4)}px`;
      };

      // Capture phase, and swallow the event: the click is for choosing a
      // section, and must never reach the portal's own button underneath.
      const onClick = (e) => {
        e.preventDefault();
        e.stopPropagation();
        cleanup();
        resolve(current);
      };

      const onKey = (e) => {
        if (e.key !== "Escape") return;
        e.preventDefault();
        e.stopPropagation();
        cleanup();
        resolve(null);
      };

      document.addEventListener("mousemove", onMove, true);
      document.addEventListener("click", onClick, true);
      document.addEventListener("keydown", onKey, true);
    });
  };

  TA.cancelPick = function cancelPick() {
    if (activePick) {
      activePick();
      return;
    }
    document.querySelectorAll("[data-ta-picker]").forEach((n) => n.remove());
  };

  /** Outline a field so the user can see, at a glance, what was touched. */
  TA.mark = function mark(el, kind) {
    const target =
      el.type === "radio" || el.type === "checkbox"
        ? el.closest("label, fieldset, .field, .form-field") || el
        : el;
    target.classList.remove("ta-mark-ok", "ta-mark-low", "ta-mark-fail", "ta-mark-todo");
    target.classList.add(`ta-mark-${kind}`);
  };

  TA.clearMarks = function clearMarks() {
    document
      .querySelectorAll(".ta-mark-ok, .ta-mark-low, .ta-mark-fail, .ta-mark-todo")
      .forEach((el) => el.classList.remove("ta-mark-ok", "ta-mark-low", "ta-mark-fail", "ta-mark-todo"));
  };

  /** Flag required questions nobody answered, so they are easy to find. */
  TA.markTodo = function markTodo(labels, fields, elements) {
    const wanted = new Set(labels.map(norm));
    let first = null;
    for (const field of fields) {
      const text = norm(field.group_label || field.label || field.placeholder || field.name);
      if (!wanted.has(text)) continue;
      const el = elements.get(String(field.idx));
      if (!el || !el.isConnected) continue;
      if (el.classList.contains("ta-mark-ok") || el.classList.contains("ta-mark-low")) continue;
      TA.mark(el, "todo");
      if (!first) first = el;
    }
    return first;
  };

  TA.injectStyles = function injectStyles() {
    if (document.getElementById("ta-fill-styles")) return;
    const style = document.createElement("style");
    style.id = "ta-fill-styles";
    style.textContent = `
      .ta-mark-ok   { outline: 2px solid #10b981 !important; outline-offset: 1px !important;
                      border-radius: 4px; transition: outline-color .3s ease; }
      .ta-mark-low  { outline: 2px solid #f59e0b !important; outline-offset: 1px !important;
                      border-radius: 4px; }
      .ta-mark-fail { outline: 2px dashed #ef4444 !important; outline-offset: 1px !important;
                      border-radius: 4px; }
      .ta-mark-todo { outline: 2px dashed #f59e0b !important; outline-offset: 1px !important;
                      border-radius: 4px; }
    `;
    (document.head || document.documentElement).appendChild(style);
  };
})();
