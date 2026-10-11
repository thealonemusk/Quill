/**
 * DOM -> field descriptors.
 *
 * Runs in every frame. Three portal families need special care and the whole
 * scraper is shaped around them:
 *
 *   Workday    — no <select> at all. Every dropdown is a <button
 *                aria-haspopup="listbox"> or a multiSelectContainer, and the
 *                label lives on a `[data-automation-id*="formField"]` wrapper.
 *   Glassdoor  — the apply flow is an iframe (smartapply.indeed.com). We run in
 *                all frames, so the inner form is scraped like any other page.
 *   Greenhouse — the embedded board is an iframe too, and the newer boards use
 *   /Lever      react-select comboboxes rather than native selects.
 *
 * Everything else falls through the generic input/select/textarea path.
 */
(function () {
  const TA = (window.__TA = window.__TA || {});

  // Text a widget shows when it holds no answer yet.
  const PLACEHOLDER_TEXT = new Set([
    "", "-", "--", "select", "select one", "select...", "select an option",
    "please select", "choose", "choose one", "choose...", "none", "n/a",
    "select a value", "search", "type to search",
  ]);

  const WORKDAY_FORM_FIELD = '[data-automation-id*="formField"], [data-automation-id*="FormField"]';

  // Custom widgets that behave like a dropdown but are not a <select>.
  const CUSTOM_WIDGET_SELECTOR = [
    'button[aria-haspopup="listbox"]',
    'button[aria-haspopup="true"]',
    '[role="combobox"]',
    '[data-automation-id="multiSelectContainer"]',
    '[data-automation-id="selectWidget"]',
    '.select__control',
    '[class*="select__control"]',
    '[contenteditable="true"]',
  ].join(", ");

  const NATIVE_SELECTOR = 'input, textarea, select';

  const SKIP_TYPES = new Set(["hidden", "submit", "button", "reset", "image"]);

  function isVisible(el) {
    if (!el || !el.isConnected) return false;
    const style = window.getComputedStyle(el);
    if (style.display === "none" || style.visibility === "hidden") return false;
    if (parseFloat(style.opacity) === 0) return false;
    const rect = el.getBoundingClientRect();
    if (rect.width <= 0 || rect.height <= 0) return false;
    // Parked off the canvas (left: -9999px) — the classic spam trap, and the
    // one thing that marks an application as bot-filled. Compare in document
    // space so a field the user has merely scrolled past still counts.
    if (rect.right + window.scrollX < 0) return false;
    if (rect.bottom + window.scrollY < 0) return false;
    return true;
  }

  function clean(text) {
    return (text || "").replace(/\s+/g, " ").replace(/\*/g, " ").trim();
  }

  function isPlaceholder(text) {
    return PLACEHOLDER_TEXT.has(clean(text).toLowerCase());
  }

  /** Walk light DOM plus any open shadow roots. */
  function queryDeep(root, selector, out) {
    out = out || [];
    let nodes = [];
    try {
      nodes = Array.from(root.querySelectorAll(selector));
    } catch (e) {
      nodes = [];
    }
    out.push(...nodes);
    let hosts = [];
    try {
      hosts = Array.from(root.querySelectorAll("*")).filter((el) => el.shadowRoot);
    } catch (e) {
      hosts = [];
    }
    for (const host of hosts) queryDeep(host.shadowRoot, selector, out);
    return out;
  }

  function labelFromAria(el) {
    const aria = el.getAttribute("aria-label");
    if (aria) return aria;
    const ref = el.getAttribute("aria-labelledby");
    if (ref) {
      const text = ref
        .split(/\s+/)
        .map((id) => {
          const node = document.getElementById(id);
          return node ? node.innerText : "";
        })
        .filter(Boolean)
        .join(" ");
      if (text) return text;
    }
    return "";
  }

  /** The wrapper that owns this control's question text. */
  function wrapperFor(el) {
    return el.closest(
      [
        WORKDAY_FORM_FIELD,
        "fieldset",
        ".field",
        ".form-field",
        ".application-question",
        '[class*="question"]',
        '[class*="FormField"]',
        '[data-test*="question"]',
        '[class*="form-group"]',
        "label",
      ].join(", ")
    );
  }

  // Workday splits a date into separate Month and Year boxes whose own labels
  // are just "Month" and "Year". Unqualified they are ambiguous, and the
  // resolver has to assume — so it assumes "start", and an end-date pair comes
  // back filled with the start date. A job running from 01/2025 to Present
  // then reads as 01/2025 to 01/2025.
  const BARE_DATE_LABEL = /^(month|year|day|mm|yy|yyyy)$/i;
  const DATE_GROUP_RE = /\b(from|to|start|end|through)\b/i;

  /** Give a bare "Month"/"Year" box the From/To context of its date group. */
  function qualifyDateLabel(el, label) {
    if (!BARE_DATE_LABEL.test(clean(label))) return label;
    let node = el.parentElement;
    for (let i = 0; i < 6 && node; i += 1) {
      const heading = node.querySelector(
        "legend, [data-automation-id$='label'], [data-automation-id$='Label'], label"
      );
      for (const text of [heading ? clean(heading.innerText) : "", clean(labelFromAria(node))]) {
        if (text && !BARE_DATE_LABEL.test(text) && DATE_GROUP_RE.test(text)) {
          return `${text} ${clean(label)}`;
        }
      }
      node = node.parentElement;
    }
    return label;
  }

  function labelFor(el) {
    return qualifyDateLabel(el, baseLabelFor(el));
  }

  function baseLabelFor(el) {
    const aria = labelFromAria(el);
    if (clean(aria)) return clean(aria);

    if (el.labels && el.labels.length) {
      const text = clean(el.labels[0].innerText);
      if (text) return text;
    }
    if (el.id) {
      let node = null;
      try {
        node = document.querySelector(`label[for="${CSS.escape(el.id)}"]`);
      } catch (e) {
        node = null;
      }
      if (node) {
        const text = clean(node.innerText);
        if (text) return text;
      }
    }

    const wrap = wrapperFor(el);
    if (wrap) {
      const node = wrap.querySelector(
        'label, legend, [data-automation-id$="label"], [data-automation-id$="Label"], [class*="label"]'
      );
      if (node && node !== el && !node.contains(el)) {
        const text = clean(node.innerText);
        if (text) return text;
      }
      // Workday renders the question as the wrapper's first line of text.
      const first = clean((wrap.innerText || "").split("\n")[0]);
      if (first) return first.slice(0, 180);
    }

    return clean(el.placeholder || el.getAttribute("data-automation-id") || el.name || "");
  }

  /** The question a radio/checkbox group answers, as opposed to one option's text. */
  function groupLabelFor(el) {
    const fieldset = el.closest("fieldset");
    if (fieldset) {
      const legend = fieldset.querySelector("legend");
      if (legend) {
        const text = clean(legend.innerText);
        if (text) return text;
      }
    }
    const group = el.closest('[role="radiogroup"], [role="group"]');
    if (group) {
      const aria = clean(labelFromAria(group));
      if (aria) return aria;
    }
    const wrap = el.closest(
      `${WORKDAY_FORM_FIELD}, .application-question, [class*="question"], .field, .form-field`
    );
    if (wrap) {
      const node = wrap.querySelector('label, legend, [data-automation-id$="label"]');
      if (node && !node.contains(el)) {
        const text = clean(node.innerText);
        if (text) return text;
      }
      const first = clean((wrap.innerText || "").split("\n")[0]);
      if (first) return first.slice(0, 180);
    }
    return "";
  }

  // Which radio group a control belongs to, for radios with no `name`. The
  // backend groups radios by name, and a nameless one used to become a group
  // of one — answered "yes" whatever the question wanted, because a list of
  // one option has only one pick. The shared question container is the group.
  const GROUP_IDS = new WeakMap();
  let groupSeq = 0;

  function groupKeyFor(el) {
    const container =
      el.closest('fieldset, [role="radiogroup"], [role="group"]') ||
      el.closest(`${WORKDAY_FORM_FIELD}, .application-question, [class*="question"], .field, .form-field`);
    if (!container) return "";
    if (!GROUP_IDS.has(container)) {
      groupSeq += 1;
      GROUP_IDS.set(container, `g${groupSeq}`);
    }
    return GROUP_IDS.get(container);
  }

  /** This radio's or checkbox's own option text. */
  function optionLabelFor(el) {
    if (el.labels && el.labels.length) {
      const text = clean(el.labels[0].innerText);
      if (text) return text;
    }
    if (el.id) {
      try {
        const node = document.querySelector(`label[for="${CSS.escape(el.id)}"]`);
        if (node) {
          const text = clean(node.innerText);
          if (text) return text;
        }
      } catch (e) {
        /* ignore malformed ids */
      }
    }
    const aria = clean(labelFromAria(el));
    if (aria) return aria;
    const parentLabel = el.closest("label");
    if (parentLabel) {
      const text = clean(parentLabel.innerText);
      if (text) return text;
    }
    return clean(el.value || "");
  }

  /**
   * Which repeating entry does this control belong to?
   *
   * Workday's My Experience step repeats blocks — workExperience-1,
   * workExperience-2, education-1, websitePanelSet-2 — and the same label
   * ("Company", "From", "Year") means a different answer in each. Without this
   * the resolver would give every entry the same value.
   */
  const SECTION_PATTERNS = [
    [/^workExperience-(\d+)$/i, "experience"],
    [/^work-experience-(\d+)$/i, "experience"],
    [/^education-(\d+)$/i, "education"],
    [/^websitePanelSet-(\d+)$/i, "website"],
    [/^website-(\d+)$/i, "website"],
    [/^websiteSection-(\d+)$/i, "website"],
  ];

  function sectionFor(el) {
    let node = el;
    while (node && node !== document.documentElement) {
      const auto = node.getAttribute && node.getAttribute("data-automation-id");
      if (auto) {
        for (const [pattern, kind] of SECTION_PATTERNS) {
          const m = auto.match(pattern);
          if (m) return { kind, index: parseInt(m[1], 10) || 1 };
        }
      }
      node = node.parentElement;
    }
    return { kind: "", index: 0 };
  }

  // Field names that only ever occur inside a repeating block. Used to recover
  // the entry when the container ids are not the ones we know about — real
  // Workday tenants do not all use `workExperience-1`, and an untagged field
  // falls through to the flat resolver, which has no answer for a bare
  // "Company" or "Role Description" and so leaves them empty.
  const REPEATING_LABELS = [
    [/^job\s*title$/i, "experience"],
    [/^company$/i, "experience"],
    [/^role\s*description$/i, "experience"],
    [/^(work\s*)?location$/i, "experience"],
    [/^from$/i, "experience"],
    [/^to$/i, "experience"],
    [/currently\s+work\s+here/i, "experience"],
    [/^school(\s+or\s+university)?$/i, "education"],
    [/^degree$/i, "education"],
    [/^field\s*of\s*study$/i, "education"],
    [/^overall\s+result/i, "education"],
    [/^url$/i, "website"],
    [/^website$/i, "website"],
  ];

  function repeatingKind(label) {
    const text = clean(label);
    for (const [pattern, kind] of REPEATING_LABELS) {
      if (pattern.test(text)) return kind;
    }
    return "";
  }

  /**
   * Give untagged fields an entry number from their order on the page.
   *
   * Two "Company" boxes down the page are employer 1 and employer 2 — that
   * holds however the tenant names its containers, so it recovers the mapping
   * when the id patterns miss.
   */
  function inferSectionsByOrder(fields) {
    const counts = new Map();          // "experience|company" -> how many seen
    for (const field of fields) {
      if (field.section_kind) continue;
      const label = field.group_label || field.label || "";
      const kind = repeatingKind(label);
      if (!kind) continue;
      const key = `${kind}|${clean(label).toLowerCase()}`;
      const seen = (counts.get(key) || 0) + 1;
      counts.set(key, seen);
      field.section_kind = kind;
      field.section_index = seen;
    }
    return fields;
  }

  function selectorFor(el) {
    const auto = el.getAttribute("data-automation-id");
    if (auto) return `[data-automation-id="${auto}"]`;
    if (el.id) return `#${el.id}`;
    const name = el.getAttribute("name");
    if (name) return `${el.tagName.toLowerCase()}[name="${name}"]`;
    return el.tagName.toLowerCase();
  }

  /** The value a custom widget is currently showing, blank when it is a placeholder. */
  function widgetValue(el) {
    const text = clean(el.innerText || el.textContent || el.value || "");
    return isPlaceholder(text) ? "" : text.slice(0, 120);
  }

  function describeNative(el, idx) {
    const tag = el.tagName.toLowerCase();
    const type = (el.getAttribute("type") || (tag === "select" ? "select-one" : tag) || "").toLowerCase();
    if (SKIP_TYPES.has(type)) return null;
    if (type !== "file" && !isVisible(el)) return null;
    // Phone-widget country lists render as inputs; the real field is next to them.
    if (el.closest(".iti__country-list") || el.classList.contains("iti__search-input")) return null;
    if (el.disabled || el.readOnly) return null;

    const isChoice = type === "radio" || type === "checkbox";
    const options =
      tag === "select" ? Array.from(el.options).map((o) => clean(o.text)).filter(Boolean) : [];
    const section = sectionFor(el);

    return {
      idx: String(idx),
      selector: selectorFor(el),
      tag,
      type,
      name: el.getAttribute("name") || "",
      id: el.id || "",
      automation: el.getAttribute("data-automation-id") || "",
      autocomplete: el.getAttribute("autocomplete") || "",
      placeholder: el.getAttribute("placeholder") || "",
      label: isChoice ? groupLabelFor(el) || optionLabelFor(el) : labelFor(el),
      group_label: isChoice ? groupLabelFor(el) : "",
      option_label: isChoice ? optionLabelFor(el) : "",
      group_key: type === "radio" ? groupKeyFor(el) : "",
      value: isChoice ? "" : clean(el.value || ""),
      checked: isChoice ? !!el.checked : false,
      required: !!el.required || el.getAttribute("aria-required") === "true",
      options,
      role: el.getAttribute("role") || "",
      section_kind: section.kind,
      section_index: section.index,
    };
  }

  function describeWidget(el, idx) {
    if (!isVisible(el)) return null;
    if (el.getAttribute("aria-disabled") === "true") return null;
    const section = sectionFor(el);
    return {
      idx: String(idx),
      selector: selectorFor(el),
      tag: el.tagName.toLowerCase(),
      type: "",
      name: el.getAttribute("name") || "",
      id: el.id || "",
      automation: el.getAttribute("data-automation-id") || "",
      autocomplete: "",
      placeholder: el.getAttribute("placeholder") || "",
      label: labelFor(el),
      group_label: "",
      option_label: "",
      group_key: "",
      value: widgetValue(el),
      checked: false,
      required: el.getAttribute("aria-required") === "true",
      options: [],
      role: el.getAttribute("role") || "combobox",
      section_kind: section.kind,
      section_index: section.index,
    };
  }

  /**
   * Scrape the frame.
   * Returns { fields, elements } — `elements` is an idx -> Element map so the
   * filler never has to re-find a node by selector (Workday ids repeat).
   */
  TA.scrape = function scrape(root) {
    // `root` scopes the result to one part of the page, so the user can fill
    // a single section of a long form instead of all of it. The scan itself
    // is still of the whole page, and entries are numbered there: numbering
    // inside the picked block restarted at 1, so employer 2's block was
    // tagged experience 1 and overwritten with employer 1's answers.
    const scoped = !!(root && root.querySelectorAll) && root !== document;
    const scope = document;
    const seen = new Set();
    const fields = [];
    const elements = new Map();
    let idx = 0;

    const push = (el, describe) => {
      if (seen.has(el)) return;
      seen.add(el);
      const field = describe(el, idx);
      if (!field) return;
      // A control nested inside an already-captured custom widget is that
      // widget's internals, not a separate question.
      fields.push(field);
      elements.set(String(idx), el);
      idx += 1;
    };

    // Custom widgets first, so their inner inputs can be recognised as internals.
    const widgets = queryDeep(scope, CUSTOM_WIDGET_SELECTOR);
    const widgetSet = new Set(widgets);
    for (const el of widgets) {
      if (el.matches("input, textarea, select")) continue; // handled natively below
      push(el, describeWidget);
    }

    for (const el of queryDeep(scope, NATIVE_SELECTOR)) {
      const ownerWidget = widgets.find((w) => w !== el && w.contains(el));
      if (ownerWidget && !widgetSet.has(el) && el.type !== "file") {
        // react-select keeps a hidden text input inside its control; skip it.
        const type = (el.getAttribute("type") || "").toLowerCase();
        if (type !== "checkbox" && type !== "radio") continue;
      }
      push(el, describeNative);
    }

    // Recover repeating-entry membership for anything the container ids missed.
    // Page-wide, before any scoping, so the second "Company" on the page is
    // employer 2 whichever block the user pointed at.
    inferSectionsByOrder(fields);

    if (!scoped) return { fields, elements };
    const kept = [];
    const keptElements = new Map();
    for (const field of fields) {
      const el = elements.get(field.idx);
      if (!insideRoot(root, el)) continue;
      kept.push(field);
      keptElements.set(field.idx, el);
    }
    return { fields: kept, elements: keptElements };
  };

  /** Is `el` inside `root`, looking through open shadow roots too? */
  function insideRoot(root, el) {
    let node = el;
    while (node) {
      if (node === root) return true;
      node = node.parentNode || node.host || null;
    }
    return false;
  }

  /**
   * What kind of page is this, before we try to fill anything?
   *
   * Workday's `/apply` URL is not a form — it is a chooser ("Autofill with
   * Resume" / "Apply Manually" / "Use My Last Application"), and behind that
   * sits a six-step wizard whose first step is Create Account / Sign In. Both
   * scrape to zero usable fields, so without this the panel just reports
   * "0 filled" and gives the user nothing to act on.
   */
  TA.detectStage = function detectStage() {
    const vis = (sel) => {
      const el = document.querySelector(sel);
      return el && isVisible(el) ? el : null;
    };

    // Workday's application wizard shows "step N of M".
    let step = "";
    const active = document.querySelector('[data-automation-id="progressBarActiveStep"]');
    const raw = active ? clean(active.innerText) : "";
    const m = (raw || (document.body ? document.body.innerText : "")).match(
      /(?:current )?step (\d+) of (\d+)\s*(.*)/i
    );
    if (m) {
      const name = clean(m[3]).slice(0, 28);
      step = `Step ${m[1]} of ${m[2]}${name ? ` · ${name}` : ""}`;
    } else if (raw) {
      step = raw.slice(0, 40);
    }

    // The chooser that stands between the /apply URL and the actual form.
    // "Use My Last Application" is listed first on purpose: it carries your
    // previous Workday answers forward, so there is far less left to fill.
    const CHOICES = [
      ["Use My Last Application", '[data-automation-id="useMyLastApplication"]'],
      ["Autofill with Resume", '[data-automation-id="autofillWithResume"]'],
      ["Apply Manually", '[data-automation-id="applyManually"]'],
    ];
    const actions = CHOICES.filter(([, sel]) => vis(sel)).map(([label, selector]) => ({ label, selector }));
    if (actions.length) {
      return {
        stage: "gate",
        step,
        message: "Workday has not opened the form yet — pick how to start.",
        actions,
        actionLabel: actions[0].label,
        actionSelector: actions[0].selector,
      };
    }

    // The job posting itself, before the apply flow starts.
    const applySelector =
      '[data-automation-id="adventureButton"], [data-automation-id="jobPostingApplyButton"]';
    if (vis('[data-automation-id="adventureButton"]') || vis('[data-automation-id="jobPostingApplyButton"]')) {
      return {
        stage: "gate",
        step,
        message: "This is the job posting, not the form.",
        actions: [{ label: "Start application", selector: applySelector }],
        actionLabel: "Start application",
        actionSelector: applySelector,
      };
    }

    // Credentials. We never fill these — the account is the user's to create.
    if (vis('input[type="password"]') || vis('[data-automation-id="password"]')) {
      return {
        stage: "auth",
        step,
        message: "Sign in or create your Workday account first. Quill never fills passwords.",
      };
    }

    return { stage: "form", step };
  };

  /** Cheap check: does this frame look like a job application at all? */
  TA.looksLikeApplication = function looksLikeApplication() {
    const host = location.hostname.toLowerCase();
    const atsHost = /greenhouse|lever\.co|myworkdayjobs|myworkday|ashbyhq|smartrecruiters|icims|jobvite|smartapply|glassdoor|indeed|bamboohr|workable|successfactors|taleo|rippling|dover/.test(
      host
    );
    const controls = document.querySelectorAll(
      'input:not([type="hidden"]):not([type="submit"]):not([type="button"]), textarea, select'
    ).length;
    const customs = document.querySelectorAll(CUSTOM_WIDGET_SELECTOR).length;
    if (atsHost && controls + customs >= 1) return true;
    const hasEmail = !!document.querySelector('input[type="email"], input[name*="email" i], input[id*="email" i]');
    const hasResume = !!document.querySelector('input[type="file"]');
    if (hasEmail && (hasResume || controls >= 4)) return true;
    const text = (document.body ? document.body.innerText : "").slice(0, 4000).toLowerCase();
    const applyWords = /apply for|application form|submit your application|job application|personal information/.test(text);
    return applyWords && controls + customs >= 3;
  };

  // ── The job description, for tailoring ─────────────────────────────────────
  //
  // Most ATSs show the description on the page the form lives on (Greenhouse,
  // Lever, Ashby) or on the posting page one click earlier (Workday), each in
  // a container of its own. The ATS-specific selectors come first; the
  // generic ones are last because `main`/`article` can hold the form too.
  const JD_SELECTORS = [
    '[data-automation-id="jobPostingDescription"]',          // Workday
    ".job__description", ".job-post-content", "#content .job-post", // Greenhouse
    '[data-qa="job-description"]', ".posting-page .section-wrapper", // Lever
    '[class*="_descriptionText"]', ".ashby-job-posting-right-pane",  // Ashby
    '[itemprop="description"]',                                  // SmartRecruiters, schema.org
    ".iCIMS_JobContent",
    '[class*="job-description" i]', '[id*="job-description" i]',
    '[class*="jobDescription"]', '[id*="jobDescription"]',
    '[data-testid*="description" i]',
    "article", '[role="main"]', "main",
  ];
  // From here on the selectors are generic and may hold a form, not a job.
  const JD_GENERIC_FROM = JD_SELECTORS.indexOf("article");
  const JD_MIN = 300;
  const JD_MAX = 20000;

  /**
   * The description on this page: `{ text, source, specific }`, or null.
   * `specific` is false for a generic container (`main`, `article`), which
   * on a form step is the form: it must not overwrite a description the
   * posting page supplied, and ranks below one.
   * A selection of at least JD_MIN characters wins — it is the user saying
   * "this is the description" on a page whose markup nothing here recognises.
   */
  TA.jobDescription = function jobDescription() {
    const tidy = (s) => (s || "").replace(/\s+/g, " ").trim().slice(0, JD_MAX);
    const picked = tidy(window.getSelection ? String(window.getSelection()) : "");
    if (picked.length >= JD_MIN) return { text: picked, source: "selection", specific: true };
    for (const [i, sel] of JD_SELECTORS.entries()) {
      let nodes;
      try {
        nodes = document.querySelectorAll(sel);
      } catch (e) {
        continue; // a selector this browser does not support
      }
      for (const node of nodes) {
        const text = tidy(node.innerText || node.textContent);
        if (text.length >= JD_MIN) return { text, source: "page", specific: i < JD_GENERIC_FROM };
      }
    }
    return null;
  };
})();
