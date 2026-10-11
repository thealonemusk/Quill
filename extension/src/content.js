/**
 * Per-frame orchestrator.
 *
 * Every frame scrapes and fills itself — that is how Glassdoor (smartapply
 * iframe), embedded Greenhouse boards and Workday's nested frames get covered
 * without any frame-specific code. Only the top frame draws the widget; the
 * child frames report their counts up to it through the service worker.
 */
(function () {
  const TA = (window.__TA = window.__TA || {});
  const isTop = window.top === window.self;

  const state = {
    fields: [],
    elements: new Map(),
    lastResolve: null,
    busy: false,
    pending: null,     // aggregation across frames, top frame only
    widgetShown: false,
    dismissed: false,  // the user closed the panel; only an explicit Show reopens it
  };

  // `message()` renders HTML, and labels scraped off the page, frame reports
  // and backend errors are not ours. Everything of theirs goes through this.
  const esc = (text) => TA.widget.escape(text);

  // How long the top frame waits for a frame that announced it was starting
  // before closing the tally without it. Generous: a Workday frame with ten
  // skills and two employers takes well over the old fixed 15 seconds. A test
  // may lower it through `TA.frameReportCapMs`.
  const FRAME_REPORT_CAP_MS = 120000;
  const frameCap = () => TA.frameReportCapMs || FRAME_REPORT_CAP_MS;

  let runSeq = 0;
  const newRunId = () => `${Date.now().toString(36)}-${(runSeq += 1)}`;

  const EMPTY_REPORT = { scraped: 0, filled: 0, low: 0, failed: 0, unresolved: [] };

  /**
   * Reloading the extension orphans the content scripts already running in open
   * tabs: `chrome.runtime` goes undefined and every call throws. That is not a
   * backend problem, and saying so sends the user off fixing the wrong thing —
   * so it is reported as its own condition.
   */
  function contextAlive() {
    try {
      return typeof chrome !== "undefined" && !!chrome.runtime && !!chrome.runtime.id;
    } catch (e) {
      return false;
    }
  }

  const CONTEXT_LOST = {
    ok: false,
    contextLost: true,
    error: "extension reloaded — refresh this page",
  };

  const send = (message) =>
    new Promise((resolve) => {
      if (!contextAlive()) {
        resolve(CONTEXT_LOST);
        return;
      }
      try {
        chrome.runtime.sendMessage(message, (reply) => {
          const err = chrome.runtime.lastError;
          if (err) {
            const lost = /context invalidated|receiving end does not exist|message port closed/i.test(
              err.message || ""
            );
            resolve(lost ? CONTEXT_LOST : { ok: false, error: err.message });
          } else {
            resolve(reply || { ok: false, error: "no reply" });
          }
        });
      } catch (e) {
        const text = String(e && e.message ? e.message : e);
        const lost = !contextAlive() || /sendMessage|context invalidated/i.test(text);
        resolve(lost ? CONTEXT_LOST : { ok: false, error: text });
      }
    });

  // ── Filling ────────────────────────────────────────────────────────────────

  /** Ask the backend what this form wants, for the fields we can currently see. */
  async function resolveFields(fields, opts) {
    return send({
      type: "resolve",
      payload: {
        url: location.href,
        page_title: document.title,
        overwrite: !!(opts && opts.overwrite),
        fields,
      },
    });
  }

  async function runFrame(opts) {
    // `opts.root` scopes the run to one section the user pointed at. Section
    // runs never expand the Add-an-entry blocks: the user chose a region, and
    // opening new ones underneath it would be filling what they did not pick.
    const root = (opts && opts.root) || null;
    let { fields, elements } = TA.scrape(root);
    state.fields = fields;
    state.elements = elements;

    const stage = root ? { stage: "form", step: "" } : TA.detectStage();
    state.stage = stage;
    // A gate or a credentials step has nothing fillable behind it yet; say so
    // rather than reporting "0 filled".
    if (stage.stage !== "form") {
      return { scraped: 0, filled: 0, low: 0, failed: 0, unresolved: [], stage };
    }
    if (!fields.length) return { scraped: 0, filled: 0, low: 0, failed: 0, unresolved: [], stage };

    TA.injectStyles();

    let resolved = await resolveFields(fields, opts);
    if (!resolved.ok) {
      return {
        scraped: fields.length, filled: 0, low: 0, failed: 0, unresolved: [],
        error: resolved.error, contextLost: !!resolved.contextLost,
      };
    }

    // Workday's My Experience step hides Work Experience, Education and
    // Websites behind Add buttons — there is nothing to fill until they are
    // opened. Open as many entries as the profile can fill, then look again.
    const added = root ? 0 : await TA.expandSections(resolved.data.sections_needed);
    if (added) {
      ({ fields, elements } = TA.scrape(root));
      state.fields = fields;
      state.elements = elements;
      const again = await resolveFields(fields, opts);
      if (again.ok) resolved = again;
    }

    const data = resolved.data;
    state.lastResolve = data;

    let resumeFile = null;
    let resumeTailored = false;
    if (data.fills.some((f) => f.action === "file")) {
      // The URL picks this job's tailored PDF when one exists.
      const resume = await send({ type: "resume", url: location.href });
      if (resume.ok) {
        resumeFile = TA.base64ToFile(resume.data.b64, resume.data.filename, resume.data.mime);
        resumeTailored = !!resume.data.tailored;
      }
    }

    const { applied, failed } = await TA.applyFills(data.fills, elements, resumeFile);
    const first = TA.markTodo(data.unresolved, fields, elements);
    if (first && isTop) first.scrollIntoView({ block: "center", behavior: "smooth" });

    return {
      scraped: fields.length,
      filled: applied.length,
      low: applied.filter((f) => f.confidence === "low").length,
      failed: failed.length,
      failedLabels: failed.map((f) => f.label || f.key || "unnamed field").slice(0, 6),
      unresolved: data.unresolved,
      job_title: data.job_title,
      company: data.company,
      ats: data.ats,
      known_job: data.known_job,
      has_resume: data.has_resume,
      needed_resume: data.fills.some((f) => f.action === "file"),
      got_resume: !!resumeFile,
      resume_tailored: resumeTailored,
    };
  }

  // ── Top-frame aggregation ──────────────────────────────────────────────────

  function blankTotals() {
    return {
      scraped: 0, filled: 0, low: 0, failed: 0, frames: 0,
      unresolved: [], job_title: "", company: "", ats: "",
      known_job: false, missingResume: false, resume: "", error: "", stage: null, contextLost: false,
      failedLabels: [],
      busyFrames: 0,   // frames that were still filling from an earlier run
      unreported: 0,   // frames that said "starting" and never reported
    };
  }

  function merge(totals, r) {
    if (r.busyFrame) {
      totals.busyFrames += 1;
      return totals;
    }
    totals.scraped += r.scraped || 0;
    totals.filled += r.filled || 0;
    totals.low += r.low || 0;
    totals.failed += r.failed || 0;
    totals.frames += 1;
    if (r.unresolved && r.unresolved.length) totals.unresolved.push(...r.unresolved);
    if (r.failedLabels && r.failedLabels.length) totals.failedLabels.push(...r.failedLabels);
    if (!totals.job_title && r.job_title) totals.job_title = r.job_title;
    if (!totals.company && r.company) totals.company = r.company;
    if (!totals.ats && r.ats && r.ats !== "unknown") totals.ats = r.ats;
    if (r.known_job) totals.known_job = true;
    if (r.needed_resume && !r.got_resume) totals.missingResume = true;
    if (r.got_resume) totals.resume = r.resume_tailored ? "tailored" : totals.resume || "default";
    if (r.error && !totals.error) totals.error = r.error;
    if (r.contextLost) totals.contextLost = true;
    // A gate in any frame is what the user has to act on first.
    if (r.stage && r.stage.stage !== "form" && !totals.stage) totals.stage = r.stage;
    if (r.stage && r.stage.step && !(totals.stage && totals.stage.step)) {
      totals.stage = totals.stage || r.stage;
    }
    return totals;
  }

  function finish(totals) {
    state.busy = false;
    TA.widget.setBusy(false);

    if (totals.contextLost) {
      TA.widget.message(
        "The extension was reloaded, so this page is running a stale copy. " +
        "<b>Refresh the page (F5)</b> and try again.",
        "err"
      );
      TA.widget.setBackend(false, "extension reloaded — refresh the page");
      return;
    }

    if (totals.error) {
      TA.widget.message(
        `Backend unreachable — start it with <b>python run.py</b>.<br><span style="opacity:.7">${esc(totals.error)}</span>`,
        "err"
      );
      TA.widget.setBackend(false, totals.error);
      return;
    }

    TA.widget.setBackend(true, "backend connected");
    TA.widget.setJob(totals.job_title, totals.company, totals.ats);

    // Portal gate: show the button that moves the user forward, not the stats.
    if (totals.stage && totals.stage.stage !== "form") {
      TA.widget.setStage(totals.stage, { onAction: clickThrough });
      TA.widget.showTodo([]);
      TA.widget.message(totals.stage.message, "info");
      return;
    }
    TA.widget.setStage({ stage: "form", step: totals.stage ? totals.stage.step : "" }, { onAction: clickThrough });
    TA.widget.showResults({
      filled: totals.filled,
      low: totals.low,
      todo: totals.unresolved.length,
    });
    TA.widget.showTodo(totals.unresolved);
    TA.widget.setAppliedLabel(totals.known_job ? "Mark applied" : "Track + applied");

    const notes = [];
    if (totals.scraped === 0) notes.push("No form fields found on this page.");
    if (totals.missingResume) notes.push("No resume on file — attach it by hand.");
    if (totals.resume === "tailored") notes.push("Attached the resume tailored for this job.");
    if (totals.resume === "default") notes.push("Attached your default resume — none tailored for this job.");
    if (totals.failed) {
      // Naming the field is the difference between "something broke" and
      // "click this one yourself" — they are outlined red on the page too.
      // The labels are scraped off the page, so they are escaped: a label is
      // page-controlled text, and this lands in innerHTML.
      const names = totals.failedLabels
        .map((l) => String(l))
        .map((l) => esc(l.length > 34 ? `${l.slice(0, 34)}…` : l))
        .join(", ");
      notes.push(
        `Set by hand (outlined red): <b>${names || `${totals.failed} field(s)`}</b>.`
      );
    }
    // A frame that never answered is named, not silently left out of the
    // tally — its fields may be half filled.
    if (totals.unreported) {
      notes.push(
        `<b>${totals.unreported} embedded form(s) did not report back</b> — ` +
        "these counts are incomplete. Check that part of the page before you submit."
      );
    }
    if (totals.busyFrames) {
      notes.push(
        `<b>${totals.busyFrames} embedded form(s) were still filling</b> from the previous run ` +
        "and were not filled again."
      );
    }
    TA.widget.message(notes.join(" "), totals.unreported || totals.busyFrames ? "err" : "info");
  }

  async function runAll(opts) {
    if (state.busy) return;
    state.busy = true;
    TA.widget.setBusy(true, "Filling…");
    TA.widget.message("");

    // Every child frame acks before it starts work, so we know how many
    // reports to wait for. Without the ack the top frame finishes its own
    // (usually tiny) form first and finalises the tally before the embedded
    // form — the one that holds all the fields — has even answered.
    const runId = newRunId();
    const pending = {
      runId, totals: blankTotals(), expected: 0, reported: 0, ownDone: false, timer: null, cap: null,
    };
    state.pending = pending;

    const done = () => {
      if (state.pending !== pending) return;
      state.pending = null;
      clearTimeout(pending.timer);
      clearTimeout(pending.cap);
      // A frame that announced itself and never answered is counted as
      // missing, so the panel says the tally is incomplete.
      pending.totals.unreported += Math.max(0, pending.expected - pending.reported);
      finish(pending.totals);
    };

    pending.maybeDone = () => {
      clearTimeout(pending.timer);
      if (pending.ownDone && pending.reported >= pending.expected) {
        // Brief grace in case a slower frame is still acking.
        pending.timer = setTimeout(done, 500);
      }
    };
    pending.noteStarting = () => {
      pending.expected += 1;
      clearTimeout(pending.timer);
    };

    // The cap runs from the start, not from when our own frame finished, and
    // it only ever fires while some frame that said "starting" is still out.
    pending.cap = setTimeout(done, frameCap());

    // Ask the other frames first so they ack while we fill our own.
    send({ type: "broadcast", message: { type: "TA_RUN_FRAME", opts: { ...(opts || {}), runId } } });

    const mine = await runFrame(opts);
    if (state.pending === pending) {
      merge(pending.totals, mine);
      pending.ownDone = true;
      pending.maybeDone();
    }
  }

  // ── Fill one section ───────────────────────────────────────────────────────
  //
  // The picker has to be armed in every frame, because on Glassdoor and
  // embedded Greenhouse boards the form the user wants to point at is not in
  // the top frame at all. Whichever frame gets the click wins; the rest are
  // told to stand down.

  let picking = false;

  async function armPicker(runId) {
    if (picking) return;
    picking = true;
    TA.injectStyles();
    let section = null;
    try {
      section = await TA.pickSection();
    } finally {
      picking = false;
    }
    if (!section) {
      if (isTop) {
        // Esc in the top frame has to stand the other frames down too —
        // otherwise every embedded frame keeps its crosshair and its capture
        // listeners, and the next click in the form is swallowed by a picker
        // the user thought they had cancelled.
        send({ type: "broadcast", message: { type: "TA_PICK_CANCEL" } });
        // Drop the aggregation we armed, or its grace timer fires later and
        // overwrites the panel with an empty tally.
        state.pending = null;
        finishSection(null);
      }
      return;
    }
    // Claim it, so the other frames drop out of picking mode.
    send({ type: "broadcast", message: { type: "TA_PICK_CANCEL" } });
    // overwrite:true stays for a section run — pointing at a block is asking
    // for it to be (re)done. It is safe only because the scraper numbers the
    // block's entry against the whole page, so what it writes is that same
    // entry's answer, never employer 1's data over employer 2.
    if (isTop) {
      state.pending = null;
      await fillSection(section);
    } else {
      send({ type: "starting" });
      if (state.busy) {
        send({ type: "report", result: { ...EMPTY_REPORT, busyFrame: true, runId } });
        return;
      }
      state.busy = true;
      let result;
      try {
        result = await runFrame({ overwrite: true, root: section });
      } catch (e) {
        result = { ...EMPTY_REPORT };
      } finally {
        state.busy = false;
      }
      send({ type: "report", result: { ...result, runId } });
    }
  }

  async function fillSection(section) {
    state.busy = true;
    TA.widget.setBusy(true, "Filling section…");
    TA.widget.message("");
    const totals = merge(blankTotals(), await runFrame({ overwrite: true, root: section }));
    finish(totals);
  }

  function finishSection(totals) {
    state.busy = false;
    TA.widget.setBusy(false);
    if (!totals) TA.widget.message("Section fill cancelled.", "info");
  }

  /** Top frame: arm every frame's picker and wait for whichever one is used. */
  async function pickAndFill() {
    if (state.busy || picking) return;
    TA.widget.message(
      "Click the part of the form you want filled — <b>Esc</b> to cancel.",
      "info"
    );

    const runId = newRunId();
    const pending = {
      runId, totals: blankTotals(), expected: 0, reported: 0, ownDone: true, timer: null, cap: null,
    };
    state.pending = pending;
    const done = () => {
      if (state.pending !== pending) return;
      state.pending = null;
      clearTimeout(pending.timer);
      clearTimeout(pending.cap);
      pending.totals.unreported += Math.max(0, pending.expected - pending.reported);
      finish(pending.totals);
    };
    pending.maybeDone = () => {
      clearTimeout(pending.timer);
      if (pending.reported >= pending.expected && pending.expected > 0) {
        pending.timer = setTimeout(done, 300);
      }
    };
    pending.noteStarting = () => {
      state.busy = true;
      TA.widget.setBusy(true, "Filling section…");
      pending.expected += 1;
      clearTimeout(pending.timer);
      // Only once a frame has actually started: until then the user may
      // still be choosing where to click.
      if (!pending.cap) pending.cap = setTimeout(done, frameCap());
    };

    send({ type: "broadcast", message: { type: "TA_PICK", runId } });
    await armPicker(runId);
  }

  // ── Messaging ──────────────────────────────────────────────────────────────

  chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
    if (!message || !message.type) return;

    if (message.type === "TA_RUN" && isTop) {
      runAll(message.opts);
      sendResponse({ ok: true });
      return;
    }

    // A child frame was told to fill itself. Ack first so the top frame knows
    // to wait for us, then always report — even a zero-field frame, so the
    // tally can close without waiting out the hard timeout.
    //
    // A frame still filling from the last run does not start a second fill on
    // top of it — two runs writing the same widgets at once is how a Refill
    // pressed mid-run corrupted an iframe. It says so instead, so the tally
    // names it rather than leaving it out.
    if (message.type === "TA_RUN_FRAME" && !isTop) {
      const runId = message.opts && message.opts.runId;
      send({ type: "starting" });
      if (state.busy) {
        send({ type: "report", result: { ...EMPTY_REPORT, busyFrame: true, runId } });
        sendResponse({ ok: true, busy: true });
        return;
      }
      state.busy = true;
      runFrame(message.opts)
        .then(
          (result) => result,
          () => ({ ...EMPTY_REPORT })
        )
        .then((result) => {
          state.busy = false;
          send({ type: "report", result: { ...result, runId } });
        });
      sendResponse({ ok: true });
      return;
    }

    // Arm the picker in a child frame; the form is often not in the top one.
    if (message.type === "TA_PICK" && !isTop) {
      armPicker(message.runId);
      sendResponse({ ok: true });
      return;
    }

    // Another frame got the click: stand down.
    if (message.type === "TA_PICK_CANCEL") {
      TA.cancelPick();
      sendResponse({ ok: true });
      return;
    }

    // A child frame is about to work: count it.
    if (message.type === "TA_FRAME_STARTING" && isTop) {
      if (state.pending) state.pending.noteStarting();
      sendResponse({ ok: true });
      return;
    }

    // A child frame reported its counts back to us.
    // A report carrying another run's id is a late answer to a run whose
    // tally already closed, and would inflate this one's counts.
    if (message.type === "TA_REPORT" && isTop) {
      const runId = message.result && message.result.runId;
      if (state.pending && (!runId || runId === state.pending.runId)) {
        merge(state.pending.totals, message.result);
        state.pending.reported += 1;
        state.pending.maybeDone();
      }
      sendResponse({ ok: true });
      return;
    }

    if (message.type === "TA_STATUS" && isTop) {
      sendResponse({
        ok: true,
        visible: TA.widget.isMounted(),
        job: state.lastResolve
          ? { title: state.lastResolve.job_title, company: state.lastResolve.company, ats: state.lastResolve.ats }
          : null,
      });
      return;
    }

    // The user asked for the panel: this is the one thing that undoes a close.
    if (message.type === "TA_SHOW" && isTop) {
      state.dismissed = false;
      mountWidget(true);
      sendResponse({ ok: true });
      return;
    }

    // A child frame found an application form. On Glassdoor and embedded
    // Greenhouse boards the whole form lives in that frame, so the top frame
    // would otherwise never show the panel.
    if (message.type === "TA_FRAME_HAS_FORM" && isTop) {
      mountWidget(true);
      sendResponse({ ok: true });
      return;
    }
  });

  // ── Widget wiring (top frame) ──────────────────────────────────────────────

  async function markApplied() {
    const resolved = state.lastResolve;
    const reply = await send({
      type: "track",
      payload: {
        url: location.href,
        title: (resolved && resolved.job_title) || document.title,
        company: (resolved && resolved.company) || "",
        status: "applied",
        notes: "marked from the extension",
      },
    });
    if (reply.ok) {
      TA.widget.message(
        reply.data.created ? "Tracked as a new applied job." : "Marked applied in the dashboard.",
        "info"
      );
      TA.widget.setAppliedLabel("Applied ✓");
    } else {
      TA.widget.message(`Could not reach the dashboard: ${esc(reply.error)}`, "err");
    }
  }

  // ── Tailor the resume to this job ──────────────────────────────────────────
  //
  // The description, best source first: text the user selected; the ATS's own
  // description container; one remembered from the posting page earlier in
  // this tab (Workday's form steps show none); a generic container last. The
  // backend prefers the description the scanner saved, when the job is known.
  const TAILOR_POLL_MS = 3000;
  const TAILOR_MAX_MS = 8 * 60 * 1000;
  let rememberedLength = 0;

  /** Keep the description a posting page shows, for the steps that do not. */
  function rememberDescription() {
    if (!isTop || !TA.jobDescription) return;
    const jd = TA.jobDescription();
    // Only an ATS's own description container: a generic `main` on a form
    // step is the form, and must not overwrite the real description.
    if (!jd || jd.source !== "page" || !jd.specific) return;
    if (jd.text.length <= rememberedLength) return;
    rememberedLength = jd.text.length;
    send({ type: "rememberJd", jd: { text: jd.text } });
  }

  async function findDescription() {
    let jd = TA.jobDescription ? TA.jobDescription() : null;
    if (jd && jd.specific) return jd;
    const recalled = await send({ type: "recallJd" });
    if (recalled.ok && recalled.data && recalled.data.text) {
      return { text: recalled.data.text, source: "remembered", specific: true };
    }
    return jd; // a generic container, or nothing — the backend may still have one saved
  }

  async function tailorResume() {
    if (state.tailoring) return;
    state.tailoring = true;
    TA.widget.setTailoring(true);
    try {
      const jd = await findDescription();
      const resolved = state.lastResolve;
      const start = await send({
        type: "tailor",
        payload: {
          url: location.href,
          title: (resolved && resolved.job_title) || document.title,
          company: (resolved && resolved.company) || "",
          jd_text: jd ? jd.text : "",
          jd_source: jd ? jd.source : "",
        },
      });
      if (!start.ok) {
        TA.widget.message(`Could not tailor: ${esc(start.error)}`, "err");
        return;
      }
      TA.widget.message(
        `Tailoring your resume from ${esc(start.data.source)}… this takes one to four minutes.`,
        "info"
      );
      const began = Date.now();
      while (Date.now() - began < TAILOR_MAX_MS) {
        await new Promise((r) => setTimeout(r, TA.tailorPollMs || TAILOR_POLL_MS));
        const s = await send({ type: "tailorStatus", jobId: start.data.job_id });
        if (!s.ok) {
          TA.widget.message(`Lost track of the tailoring run: ${esc(s.error)}`, "err");
          return;
        }
        if (s.data.state === "running") {
          TA.widget.setTailoring(true, `Tailoring… ${Math.round((Date.now() - began) / 1000)}s`);
          continue;
        }
        if (!s.data.ok) {
          TA.widget.message(`Tailoring failed: ${esc(s.data.error)} Your default resume will be used.`, "err");
          return;
        }
        const reworded = s.data.reworded
          ? `${s.data.reworded} line(s) reworded${s.data.refused ? `, ${s.data.refused} refused by the fact check` : ""}`
          : "reordered for this job, not reworded (the model was unavailable)";
        TA.widget.message(
          `Tailored resume ready — ${esc(reworded)}. ` +
            `<a href="${esc(s.data.pdf_url)}" target="_blank" rel="noopener">View PDF</a><br>` +
            "Autofill will attach it.",
          "info"
        );
        return;
      }
      TA.widget.message("Still tailoring after 8 minutes — check the dashboard's review queue.", "err");
    } finally {
      state.tailoring = false;
      TA.widget.setTailoring(false);
    }
  }

  /**
   * Click a portal's own gate button (Workday's "Apply Manually" / "Apply"),
   * then wait for the form it opens and refresh the panel. Never touches a
   * submit control — this only moves forward into the form.
   */
  async function clickThrough(selector) {
    const el = document.querySelector(selector);
    if (!el) {
      TA.widget.message("That button is gone — reload the page.", "err");
      return;
    }
    TA.widget.setBusy(true, "Opening…");
    el.click();

    // Workday swaps the whole page in; poll rather than guess a delay.
    for (let i = 0; i < 24; i += 1) {
      await new Promise((r) => setTimeout(r, 500));
      const stage = TA.detectStage();
      if (stage.stage !== "gate") {
        state.stageKey = `${stage.stage}|${stage.actionLabel || ""}|${stage.step || ""}`;
        TA.widget.setBusy(false);
        TA.widget.setStage(stage, { onAction: clickThrough });
        TA.widget.message(
          stage.stage === "auth" ? stage.message : "Form is open — press Autofill.",
          "info"
        );
        return;
      }
    }
    TA.widget.setBusy(false);
    TA.widget.message("The form did not open. Click the portal's own button.", "err");
  }

  function focusTodo(label) {
    const norm = (s) => (s || "").replace(/\s+/g, " ").trim().toLowerCase();
    const field = state.fields.find(
      (f) => norm(f.group_label || f.label || f.placeholder || f.name) === norm(label)
    );
    if (!field) return;
    const el = state.elements.get(String(field.idx));
    if (!el) return;
    el.scrollIntoView({ block: "center", behavior: "smooth" });
    try { el.focus({ preventScroll: true }); } catch (e) { /* not focusable */ }
  }

  /** Re-read the page's stage and update the panel, but only when it changed. */
  function refreshStage() {
    if (!isTop || state.busy || !TA.widget.isMounted()) return;
    const stage = TA.detectStage();
    const key = `${stage.stage}|${stage.actionLabel || ""}|${stage.step || ""}`;
    if (key === state.stageKey) return;
    state.stageKey = key;
    state.stage = stage;
    TA.widget.setStage(stage, { onAction: clickThrough });
    if (stage.stage !== "form") TA.widget.message(stage.message, "info");
  }

  function mountWidget(force) {
    if (!isTop) return;
    if (TA.widget.isMounted()) return;
    // Closed by the user: stays closed through every later DOM mutation and
    // frame announcement until they ask for it again (TA_SHOW clears this).
    if (state.dismissed) return;
    if (!force && !TA.looksLikeApplication()) return;
    const fresh = TA.widget.mount({
      onFill: () => runAll({ overwrite: false }),
      onFillSection: pickAndFill,
      onRefill: () => runAll({ overwrite: true }),
      onApplied: markApplied,
      onTailor: tailorResume,
      onTodoClick: focusTodo,
      onAction: clickThrough,
      onClose: () => {
        state.dismissed = true;
      },
    });
    state.widgetShown = true;
    // Only a newly built panel needs the stage and the handshake.
    if (!fresh) return;

    // Show the gate before the user even presses Autofill.
    state.stageKey = null;
    refreshStage();
    send({ type: "ping" }).then((reply) => {
      if (reply.ok) {
        TA.widget.setBackend(true, `Profile: ${reply.data.name || "unnamed"}`);
        if (!reply.data.ready) {
          TA.widget.message(`Profile incomplete: ${esc((reply.data.missing || []).join(", "))}`, "err");
        } else if (!reply.data.has_resume) {
          TA.widget.message("No resume on file — upload one in Settings.", "err");
        }
      } else if (reply.contextLost) {
        TA.widget.setBackend(false, "extension reloaded — refresh the page");
        TA.widget.message(
          "The extension was reloaded. <b>Refresh this page (F5)</b> to reconnect.",
          "err"
        );
      } else {
        TA.widget.setBackend(false, reply.error);
        TA.widget.message("Backend offline — start it with <b>python run.py</b>.", "err");
      }
    });
  }

  // Portals render their forms late and swap them per step, so keep looking.
  function watch() {
    let timer = null;
    let announced = false;

    const check = () => {
      clearTimeout(timer);
      timer = setTimeout(() => {
        if (isTop) {
          rememberDescription();
          mountWidget(false);
          // Workday renders its chooser well after the widget mounts, and
          // swaps the whole page on every wizard step — so the stage has to be
          // re-read, not just read once.
          refreshStage();
        } else if (!announced && TA.looksLikeApplication()) {
          announced = true;
          send({ type: "broadcast", message: { type: "TA_FRAME_HAS_FORM" } });
        }
      }, 700);
    };

    const observer = new MutationObserver(check);
    observer.observe(document.documentElement, { childList: true, subtree: true });
    check();

    // SPA route changes: Workday's wizard steps and Glassdoor's modal replace
    // the whole form without a page load.
    let href = location.href;
    setInterval(() => {
      if (location.href !== href) {
        href = location.href;
        announced = false;
        rememberedLength = 0;     // a new page may be a new posting
        TA.clearMarks();
        check();
      }
    }, 1200);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", watch, { once: true });
  } else {
    watch();
  }
})();
