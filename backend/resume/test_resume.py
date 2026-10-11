"""
Tests for resume tailoring.

Runnable script rather than pytest, matching `extension/test/run_tests.py` —
this repo has no pytest setup.

    python backend/resume/test_resume.py            # no API key needed
    python backend/resume/test_resume.py --llm      # also exercises the rewrite call

Everything except `--llm` is deterministic and offline, which is deliberate: the
parts that decide what is true (the parser, the guard) and what matters (the
matcher) must be testable without spending money or depending on a model's mood.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from backend.resume import compile as rc  # noqa: E402
from backend.resume import guard as guard_mod  # noqa: E402
from backend.resume import match, texdoc  # noqa: E402

DATA = Path(__file__).resolve().parent / "testdata"
SAMPLE = DATA / "sample_resume.tex"
TEMPLATE = DATA / "template_style.tex"     # Jake's-Resume shape, the real-world case

failures: list[str] = []


def check(name: str, got, want) -> None:
    ok = (want in got) if isinstance(want, str) and isinstance(got, str) and want else got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {name:38} {str(got)[:46]!r}")
    if not ok:
        failures.append(f"{name}: expected {want!r}, got {got!r}")


def section(title: str) -> None:
    print(f"\n{title}")


# ── The parser ───────────────────────────────────────────────────────────────

def test_parser() -> texdoc.TexDoc:
    section("Parser — the document must survive a round trip untouched")
    doc = texdoc.parse_file(SAMPLE)
    check("slots found", len(doc.slots) > 8, True)
    check("round trip is byte-identical", doc.render() == doc.source, True)
    check("sections attributed", any(s.section == "Experience" for s in doc.slots), True)
    check("entries attributed", any(s.entry == "Paytm" for s in doc.slots), True)

    # A bullet containing maths must never be offered for rewriting.
    mathy = [s for s in doc.slots if "$" in s.raw]
    check("maths bullet marked non-editable", all(not s.editable for s in mathy), True)
    check("maths bullet still droppable", all(s.droppable for s in mathy), True)
    return doc


def test_escaping() -> None:
    section("Escaping — LaTeX specials must survive a trip through the model")
    for raw in ("traffic & 40% efficiency", "C++ and C#", "a_b {x} 100%", "cost ~$5"):
        check(f"round trip {raw!r}", texdoc.unescape(texdoc.escape(raw)), raw)
    check("ligature pair is broken", "\\kern0pt" in texdoc.escape("traffic"), True)
    check("harden is idempotent",
          texdoc.harden(texdoc.harden("x\\begin{document}y")).count("ATS hardening"), 1)


def test_drop_and_edit(doc: texdoc.TexDoc) -> None:
    section("Rendering — edits and drops")
    bullets = [s for s in doc.slots if s.kind == "bullet" and s.editable]
    target = bullets[0]
    out = doc.render(edits={target.id: "Rewritten bullet with 40% growth & scale."})
    check("edit is applied", out, "Rewritten bullet")
    check("edit is escaped", out, r"40\%")
    check("preamble untouched", out.split("\\begin{document}")[0],
          doc.source.split("\\begin{document}")[0])

    dropped = doc.render(drop={target.id})
    check("dropped text is gone", target.text[:40] not in dropped, True)
    check("other bullets survive", bullets[1].text[:40] in dropped, True)


# ── The real-world template shape ────────────────────────────────────────────

def test_template_style() -> None:
    """Four bugs that only a Jake's-Resume-shaped document exposes."""
    section("Template shape — macro preamble, nested headings, comments")
    doc = texdoc.parse_file(TEMPLATE)
    body_start = doc.source.index("\\begin{document}")

    # 1. Macro definitions contain a literal \item. Offering those as droppable
    #    bullets means the trimmer can delete \newcommand and break everything.
    preamble = [s for s in doc.slots if s.start < body_start]
    check("no slots inside the preamble", preamble, [])

    # 2. Project names are nested three commands deep.
    entries = {s.entry for s in doc.slots}
    check("ThrottleX heading found", "ThrottleX" in entries, True)
    check("OktaDB heading found", "OktaDB" in entries, True)
    check("heading drops the URL", any("example.com" in e for e in entries), False)

    # 3. An entry must not leak into the next section.
    ach = [s for s in doc.slots if s.section.startswith("Achievements")]
    check("achievements do not inherit education", all(not s.entry for s in ach), True)

    # 4. The summary must be rewritable, and must not swallow the comment
    #    divider that follows it.
    summary = [s for s in doc.slots if s.kind == "summary"]
    check("summary slot exists", len(summary), 1)
    check("summary is editable", summary[0].editable, True)
    check("summary excludes the comment", "%" in summary[0].text, False)
    check("summary excludes the \\small", summary[0].text.startswith("Backend engineer"), True)

    check("round trip is byte-identical", doc.render() == doc.source, True)

    # Re-injecting every bullet must leave the comment a comment. If the span
    # had swallowed it, escaping would have produced "\% ---------- Experience".
    edits = {s.id: s.text for s in doc.editable_slots}
    rendered = doc.render(edits=edits)
    check("comment survives as a comment", "\n% ---------- Experience" in rendered, True)
    check("comment was not escaped", "\\% ---------- Experience" in rendered, False)


def test_engine_compat() -> None:
    """pdfTeX-only primitives must not stop XeTeX."""
    section("Engine compatibility — a pdfLaTeX file under Tectonic")
    src = TEMPLATE.read_text(encoding="utf-8")
    check("fixture really uses pdfgentounicode", "\\pdfgentounicode" in src, True)

    prepared = texdoc.harden(src)
    check("compat shim injected", "engine compatibility" in prepared, True)
    check("shim lands after documentclass",
          prepared.index("engine compatibility") > prepared.index("\\documentclass"), True)
    check("harden is idempotent",
          texdoc.harden(prepared).count("engine compatibility"), 1)

    if not rc.is_available():
        print("  SKIP  tectonic not installed")
        return
    audit = rc.compile_tex(prepared, DATA / "template_out.pdf")
    check("pdfLaTeX-targeted file compiles", audit.pages, 1)
    check("text is extractable", audit.chars > 800, True)
    flat = " ".join(audit.text.split())
    check("comment divider is not printed", "---------- Experience" in flat, False)
    check("project name survives", "ThrottleX" in flat, True)


# ── The matcher ──────────────────────────────────────────────────────────────

def test_matching() -> None:
    section("Matching — word boundaries, coverage, ranking")
    check("'go' does not match inside 'google'", match.contains("google cloud", "go"), False)
    check("'go' matches standalone", match.contains("we use go and rust", "go"), True)
    check("'c++' matches", match.contains("strong c++ skills", "c++"), True)
    check("'spring boot' beats 'spring'", "spring boot" in match.find_tech("We use Spring Boot"), True)

    resume = "Built Spring Boot services on AWS with Docker and MySQL."
    cov = match.coverage(resume, ["spring boot", "aws", "kafka", "docker"])
    check("coverage counts matches", len(cov.matched), 3)
    check("coverage counts gaps", cov.missing, ["kafka"])
    check("coverage score", cov.score, 75.0)

    doc = texdoc.parse_file(SAMPLE)
    ranks = match.rank_slots(doc.slots, ["mqtt", "ci/cd", "jenkins"])
    top = max(ranks, key=ranks.get)
    check("most relevant bullet ranks top", ranks[top] > 0, True)
    check("summary is protected from trimming",
          all(ranks[s.id] >= 5.0 for s in doc.slots if s.kind == "summary"), True)


# ── The guard ────────────────────────────────────────────────────────────────

def test_guard() -> None:
    section("Guard — rewrites may reword facts, never add them")
    master = texdoc.plain_text(texdoc.parse_file(SAMPLE))
    gate = guard_mod.Guard(master)

    # Every one of these is a rewording of something the fixture already says.
    allowed = [
        "Migrated device connectivity to TLS over HTTPS/MQTTS for the Indonesia launch.",
        "Improved API response times by 40% through asynchronous processing and caching.",
        "Reduced latency by fixing a memory leak in the messaging client.",
    ]
    refused = [
        ("Built Kafka streaming pipelines.", "technology"),
        ("Led backend development at Google.", "company"),
        ("Enabled 450 production changes.", "inflated number"),
        ("Deployed with Terraform and Helm.", "technology"),
    ]
    for text in allowed:
        check(f"accepts: {text[:34]}", gate.check(text).ok, True)
    for text, why in refused:
        v = gate.check(text)
        check(f"refuses {why}: {text[:26]}", v.ok, False)
        if v.ok:
            failures.append(f"guard let through: {text}")

    accepted, rejected = gate.filter({"b01": allowed[1], "b02": refused[0][0]})
    check("filter keeps the good one", list(accepted), ["b01"])
    check("filter reports the bad one", len(rejected), 1)


GUARD_TEX = r"""\documentclass{article}
\newcommand{\entry}[2]{\begin{tabular*}{0.97\textwidth}{l}\textbf{#1} #2\end{tabular*}}
\begin{document}
Ashutosh Jha | +91 9939964663 | Noida, India
\section{Summary}
\small
Backend / distributed-systems engineer with production ownership of payments infrastructure.
\section{Experience}
\resumeSubheading{Paytm}{2025}
\begin{itemize}
  \resumeItem{Owned CI/CD and OTA release path for 18 device types, including Jenkins builds.}

  \resumeItem{Enabled 45 production changes through automated PR workflows.}
\end{itemize}
\resumeSubheading{Denr Financial Services}{2024}
\begin{itemize}
  \resumeItem{Improved API response times by 40\% through asynchronous processing and caching.}

  \resumeItem{Built backend services using AWS Lambda and S3.}
\end{itemize}
\section{Projects}
\resumeProjectHeading{ThrottleX}{Java 17, Spring Boot, MySQL, Docker}
\begin{itemize}
  \resumeItem{Built a rate-limiting service with Token Bucket and Sliding Window algorithms.}

  \resumeItem{Exposed a 9-endpoint admin API for policy management.}
\end{itemize}
\end{document}
"""


def test_guard_scoping() -> None:
    """
    The rewrites the first guard let through on the real master. Figures are
    checked against their own bullet as value + unit, names and technologies
    against their own entry, and number words and job-description terms at all.
    """
    section("Guard scoping — each claim against the narrowest source that can support it")
    doc = texdoc.parse(GUARD_TEX)
    jd = "We use Apache Flink, BigQuery and Kafka. Our platform team ships fast."
    gate = guard_mod.Guard(texdoc.plain_text(doc), jd_text=jd,
                           master_figures_text=texdoc.prose_text(doc))

    def slot(prefix):
        return next(s for s in doc.slots if s.text.startswith(prefix))

    def verdict(s, text):
        return gate.check(text, source=s.text, context=texdoc.entry_context(doc, s)).ok

    cicd, prs = slot("Owned CI/CD"), slot("Enabled 45")
    api, rate = slot("Improved API"), slot("Built a rate-limiting")
    refused = [
        (api, "Improved API response times by 64% through caching.", "digits from the phone number"),
        (api, "Improved API response times by 97% through caching.", "digits from 0.97\\textwidth"),
        (cicd, "Owned CI/CD and OTA release path for 8 device types.", "18 -> 8"),
        (cicd, "Owned CI/CD and OTA release path for 45 device types.", "45 from another bullet"),
        (prs, "Enabled 45M production changes through automated PR workflows.", "45 -> 45M"),
        (cicd, "Owned CI/CD and OTA releases at Denr Financial Services.", "another employer"),
        (cicd, "Owned stream processing with flink and bigquery.", "lowercase JD tech"),
        (prs, "Led a team of five engineers serving millions of users.", "number words"),
        (cicd, "Stripe integrations shipped across 18 device types.", "sentence-initial name"),
        (rate, "Built services in javascript behind a limiter.", "javascript for java"),
        (api, "Improved API response times by 40% using Spring Boot.", "tech from another entry"),
    ]
    allowed = [
        (api, "Cut API response times by 40% using asynchronous processing and caching."),
        (cicd, "Ran CI/CD and OTA releases for 18 device types with Jenkins builds."),
        (rate, "Built a rate-limiting service in Spring Boot, packaged with Docker."),
        (rate, "Built a Token Bucket and Sliding Window rate-limiting service in Java."),
        (cicd, "Owned CI/CD for 18 device types, applying distributed systems practice."),
    ]
    for s, text, why in refused:
        check(f"refuses {why}", verdict(s, text), False)
    for s, text in allowed:
        check(f"accepts: {text[:34]}", verdict(s, text), True)

    # The summary summarises the whole resume, so it may cite any bullet's
    # figures — but still none that no bullet states.
    check("summary may cite 45 and 18",
          gate.check("Backend engineer who shipped 45 production changes for 18 device types.").ok, True)
    check("summary may not invent 64%", gate.check("Backend engineer who cut latency 64%.").ok, False)
    check("figures compare as value + unit", guard_mod.figures("1,500 LOC") == guard_mod.figures("1.5k LOC"), True)


# ── Compilation ──────────────────────────────────────────────────────────────

def test_compile() -> None:
    section("Compilation — the PDF an ATS actually reads")
    if not rc.is_available():
        print("  SKIP  tectonic not installed (run scripts/install_tectonic.py)")
        return

    doc = texdoc.parse_file(SAMPLE)
    edits = {
        s.id: s.text for s in doc.editable_slots
    }
    # Words chosen because every one of them contains a ligature pair.
    bullets = [s for s in doc.slots if s.kind == "bullet" and s.editable]
    edits[bullets[0].id] = (
        "Fixed traffic efficiency for the offline fleet, notification workflows "
        "and difficult staff profiling, at 40% lower latency."
    )
    source = texdoc.harden(doc.render(edits=edits))
    audit = rc.compile_tex(source, DATA / "test_out.pdf")

    check("compiles to one page", audit.pages, 1)
    check("text is extractable", audit.chars > 1200, True)
    check("no ligature glyphs", audit.ligatures, [])
    check("no words split by hyphenation", audit.hyphen_splits, [])
    check("no images", audit.images, 0)
    check("no tables", audit.tables, 0)
    check("audit reports it clean", audit.problems(), [])

    flat = " ".join(audit.text.split())
    for word in ("traffic", "efficiency", "offline", "notification", "workflows", "difficult", "staff"):
        check(f"{word!r} extracts as plain ASCII", word in flat, True)

    # A document that cannot build must raise, not return a broken PDF.
    try:
        rc.compile_tex("\\documentclass{article}\\begin{document}\\undefinedmacro\\end{document}",
                       DATA / "broken.pdf")
        failures.append("compile_tex accepted a broken document")
        print("  FAIL  broken LaTeX raises CompileError")
    except rc.CompileError as exc:
        check("broken LaTeX raises CompileError", bool(str(exc)), True)


def test_one_page_fitter() -> None:
    section("One-page fitter — trims the least relevant bullet until it fits")
    if not rc.is_available():
        print("  SKIP  tectonic not installed")
        return
    from backend.resume import tailor

    doc = texdoc.parse_file(SAMPLE)
    # Bloat every bullet so the document cannot possibly fit on one page.
    bloat = {s.id: (s.text + " ") * 6 for s in doc.editable_slots}
    ranks = match.rank_slots(doc.slots, ["mqtt", "jenkins", "ci/cd"])

    audit, dropped, _ = tailor._fit_to_one_page(
        doc, bloat, ranks, DATA / "fitted.pdf", "", max_pages=1
    )
    check("converges to one page", audit.pages, 1)
    check("something was dropped", len(dropped) > 0, True)
    check("summary was not sacrificed",
          all(doc.slot(d).kind != "summary" for d in dropped), True)


# ── Optional: the model ──────────────────────────────────────────────────────

def test_llm() -> None:
    section("Rewrite call (--llm)")
    from backend.resume import llm

    if not llm.is_available():
        print("  SKIP  no usable API key")
        return
    try:
        data = llm.ask_json(
            "You answer with JSON only.",
            'Return {"ok": true, "word": "tailoring"} exactly.',
        )
        check("model returns JSON", data.get("ok"), True)
    except Exception as exc:  # noqa: BLE001
        print(f"  FAIL  model call: {str(exc)[:120]}")
        failures.append(f"llm: {exc}")


def test_tailored_lookup() -> None:
    section("Tailored lookup — the PDF an application actually uploads")
    import os
    import tempfile
    import time

    from backend.resume import tailor

    saved = tailor.TAILORED_DIR, tailor.master_path
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        master = root / "master.tex"
        master.write_text("master", encoding="utf-8")
        old = time.time() - 60
        os.utime(master, (old, old))
        tailor.TAILORED_DIR = root / "tailored"
        tailor.master_path = lambda configured="": master
        try:
            check("no folder -> none", tailor.tailored_pdf("abcd1234-x"), None)
            sub = tailor.output_dir("Acme Corp", "abcd1234-x")
            sub.mkdir(parents=True)
            (sub / "resume.pdf").write_bytes(b"%PDF tailored")

            check("finds this job's PDF", tailor.tailored_pdf("abcd1234-x"), sub / "resume.pdf")
            check("another job gets none", tailor.tailored_pdf("ffff0000-y"), None)

            named = tailor.tailored_upload("abcd1234-x", "Ashutosh_Jha.pdf")
            check("upload carries the default's name", named.name if named else "", "Ashutosh_Jha.pdf")
            check("named copy stays in its sub-folder", named.parent if named else None, sub / "upload")
            (sub / "base.pdf").write_bytes(b"%PDF untailored master")
            base = tailor.tailored_upload("abcd1234-x", "base.pdf")
            check("a default named base.pdf still gets the tailored bytes",
                  base.read_bytes() if base else b"", b"%PDF tailored")
            from backend.resume import sends
            (sub / "report.json").write_text('{"llm_used": true, "model_rewrites": 3}', encoding="utf-8")
            check("send log finds the report beside an upload/ copy",
                  sends._report_for(named).get("model_rewrites"), 3)
            check("named copy is the tailored bytes", named.read_bytes() if named else b"", b"%PDF tailored")
            docx = tailor.tailored_upload("abcd1234-x", "Ashutosh_Jha.docx")
            check("a .docx default still uploads a .pdf", docx.name if docx else "", "Ashutosh_Jha.pdf")

            now = time.time()
            os.utime(master, (now, now))
            check("older than the master -> ignored", tailor.tailored_pdf("abcd1234-x"), None)
        finally:
            tailor.TAILORED_DIR, tailor.master_path = saved


REORDER_TEX = r"""\documentclass{article}
\begin{document}
% tempoapply-headlines: Backend engineer | Platform / infrastructure engineer
\section{Summary}
\small
Backend engineer with production ownership of fleet infrastructure.

\section{Experience}
\textbf{Acme}
\begin{itemize}
  \resumeItem{Wrote internal tooling for the support team.}

  \resumeItem{Measured a 40\% drop in page load time.}

  \resumeItem{Ran Kubernetes clusters with Terraform for the platform.}
\end{itemize}

\section{Technical Skills}
\textbf{Languages:} C, Java, Python \\
\textbf{Infrastructure:} AWS (EC2, S3, Lambda), Docker, Kubernetes \\
\textbf{Databases:} MySQL, PostgreSQL,
\end{document}
"""


def test_reordering() -> None:
    section("Reordering — bullets within an entry, skills within a line")
    from backend.resume import jd as jd_mod
    from backend.resume import tailor

    doc = texdoc.parse(REORDER_TEX)
    bullets = [s for s in doc.slots if s.kind == "bullet"]
    check("one reorderable group", [len(g) for g in doc.bullet_groups()], [3])
    check("identity order is byte-identical",
          doc.render(order=[s.id for s in doc.slots]) == doc.source, True)

    flipped = doc.render(order=[s.id for s in reversed(bullets)])
    positions = [flipped.index(s.raw) for s in reversed(bullets)]
    check("reversed order is laid out reversed", positions == sorted(positions), True)
    check("every bullet appears exactly once",
          all(flipped.count(s.raw) == 1 for s in bullets), True)
    check("reorder moves only bullets", len(flipped), len(doc.source))
    dropped = doc.render(order=[s.id for s in reversed(bullets)], drop={bullets[2].id})
    check("drop still works under a reorder", bullets[2].raw in dropped, False)

    # Skills: brackets are one item, the trailing comma survives.
    labels = [l.label for l in doc.skills]
    check("skills lines found", labels, ["Languages", "Infrastructure", "Databases"])
    check("bracketed item stays whole", doc.skills[1].items[0], "AWS (EC2, S3, Lambda)")
    check("trailing comma kept", doc.skills[2].trailing, ",")
    moved = doc.render(skill_order={0: [2, 0, 1]})
    check("skills item moved", "\\textbf{Languages:} Python, C, Java \\\\" in moved, True)

    spec = jd_mod.JobSpec(job_title="Platform Engineer", required=["kubernetes", "python"])
    order = tailor._bullet_order(doc, spec)
    lead = [sid for sid in order if sid in {s.id for s in bullets}][0]
    check("job evidence leads the entry", lead, bullets[2].id)
    # The metric bullet must not jump the author's first bullet on a number
    # alone — reverting to reward_numbers=True puts b.."40%" second.
    rest = [sid for sid in order if sid in {bullets[0].id, bullets[1].id}]
    check("a number alone does not promote", rest, [bullets[0].id, bullets[1].id])

    skills = tailor._skill_order(doc, spec, "We run Kubernetes and Python services.")
    check("asked-for language moves first", skills.get(0, [])[:1], [2])
    check("asked-for infra moves first", skills.get(1, [])[:1], [2])

    summary = next(s for s in doc.slots if s.kind == "summary")
    edit = tailor._headline_edit(doc, spec, summary.text)
    check("headline picks the role's option",
          edit[1] if edit else None, "Platform / infrastructure engineer")
    backend = jd_mod.JobSpec(job_title="Backend Engineer", required=["java"])
    check("headline kept when it already fits", tailor._headline_edit(doc, backend, summary.text), None)


def test_send_log() -> None:
    section("Send log — callback rate by resume variant")
    from datetime import datetime, timedelta

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from backend.db.models import Base, Job, ResumeSend
    from backend.resume import sends

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    now = datetime(2026, 9, 23)
    old, fresh = now - timedelta(days=40), now - timedelta(days=3)
    cases = [
        ("tailored", "interviewing", old),
        ("tailored", "applied", old),        # no reply after 21 days
        ("tailored", "applied", fresh),      # pending: left out of the rate
        ("default", "rejected", old),
        ("default", "tailored", old),        # filled, never submitted
    ]
    for i, (variant, status, when) in enumerate(cases):
        db.add(Job(id=f"j{i}", title="t", company="c", platform="p", url=f"u{i}",
                   status=status, applied_at=when))
        db.add(ResumeSend(job_id=f"j{i}", variant=variant, sent_at=when))
    db.commit()
    stats = sends.callback_stats(db, now=now)
    tailored = stats["by"]["variant"]["tailored"]
    check("callback counted", tailored["callback"], 1)
    check("old silence is a no", tailored["no_reply"], 1)
    check("young application pending", tailored["pending"], 1)
    check("rate over settled only", tailored["rate"], 50.0)
    check("unsubmitted fill excluded", stats["overall"]["sent"], 4)
    check("small sample flagged", stats["overall"]["enough_data"], False)

    # The dashboard's Clear deletes rejected jobs. The rejection must survive,
    # or every cleared "no" silently inflates the rate.
    job = db.get(Job, "j1")
    job.status = "rejected"
    db.commit()
    check("status copied onto the send", db.get(ResumeSend, "j1").status, "rejected")
    db.delete(job)
    db.commit()
    after = sends.callback_stats(db, now=now)["by"]["variant"]["tailored"]
    check("deleted job's rejection still counted", after["rejected"], 1)
    check("rate uses the kept outcome", after["rate"], 50.0)
    db.close()


def test_arrangement_safety() -> None:
    section("Arrangement safety — shapes that must not be reordered")
    nested = texdoc.parse(
        "\\begin{document}\n\\section{Experience}\n\\textbf{Acme}\n\\begin{itemize}\n"
        "  \\item Led the platform work\n  \\begin{itemize}\n    \\item sub point\n"
        "  \\end{itemize}\n  \\item Second top-level point\n\\end{itemize}\n\\end{document}\n"
    )
    check("nested list entry not reorderable", nested.bullet_groups(), [])

    # A skills row wrapped in a macro bullet is also a bullet slot: two edits
    # over one span, so the bullet keeps it and the row is not reordered.
    wrapped = texdoc.parse(
        "\\begin{document}\n\\section{Skills}\n\\begin{itemize}\n"
        "  \\resumeItem{\n  \\textbf{Languages:} C, Java, Python\n  }\n\\end{itemize}\n\\end{document}\n"
    )
    check("skills row inside a bullet left to the bullet", wrapped.skills, [])
    # A bare \item before the row is not a slot (bullet text stops at \textbf),
    # so that row stays reorderable, and reordering it must still render.
    bare = texdoc.parse(
        "\\begin{document}\n\\section{Skills}\n\\begin{itemize}\n"
        "  \\item\n  \\textbf{Languages:} C, Java, Python\n\\end{itemize}\n\\end{document}\n"
    )
    check("bare-\\item skills row still found", len(bare.skills), 1)
    check("bare-\\item row reorders cleanly",
          "Python, C, Java" in bare.render(skill_order={0: [2, 0, 1]}), True)

    # The same skills line listed twice is two edits over one span.
    doc = texdoc.parse(REORDER_TEX)
    twice = texdoc.TexDoc(source=doc.source, slots=doc.slots, skills=[doc.skills[0]] * 2)
    try:
        twice.render(skill_order={0: [2, 1, 0], 1: [2, 1, 0]})
        refused = "no error"
    except ValueError:
        refused = "ValueError"
    check("overlapping spans refused", refused, "ValueError")


def test_tailor_end_to_end() -> None:
    section("Tailor end to end — refused rewrites, failed builds")
    if not rc.is_available():
        print("  SKIP  tectonic not installed")
        return
    import tempfile

    from backend.resume import tailor

    saved_dir, saved_propose = tailor.TAILORED_DIR, tailor._propose_rewrites
    with tempfile.TemporaryDirectory() as tmp:
        tailor.TAILORED_DIR = Path(tmp) / "tailored"
        try:
            # A model that answers, but only with a fabrication.
            doc = texdoc.parse_file(SAMPLE)
            target = doc.editable_slots[1].id
            tailor._propose_rewrites = lambda *a, **k: (
                {target: "Built Kafka pipelines at Google for 450 services."}, "fake"
            )
            res = tailor.tailor("We need Java, MQTT and Jenkins CI/CD.", job_title="Backend Engineer",
                                company="Acme", job_id="e2e00001-x", master=str(SAMPLE))
            check("tailor succeeds", res.ok, True)
            check("fabrication refused", len(res.report.get("rejected", [])), 1)
            check("refused rewrites are not 'llm used'", res.report.get("llm_used"), False)
            check("model_rewrites counts only accepted", res.report.get("model_rewrites"), 0)
            check("arranged", res.report.get("arranged"), True)

            # A rewrite that grows past 115% is where an added claim goes.
            long_slot = doc.editable_slots[1]
            padded = long_slot.text + " — collaborating asynchronously in distributed team environments."
            tailor._propose_rewrites = lambda *a, **k: ({long_slot.id: padded}, "fake")
            grown = tailor.tailor("We need Java.", company="Acme", job_id="e2e00005-v", master=str(SAMPLE))
            reasons = [r["reason"] for r in grown.report.get("rejected", [])]
            check("an over-length rewrite is refused", any("longer than allowed" in r for r in reasons), True)
            check("...and nothing was reworded", grown.report.get("model_rewrites"), 0)
            out = Path(res.pdf_path).parent
            check("no staging file left", (out / "resume.building.pdf").exists(), False)

            # A master that cannot compile must leave no resume.pdf behind.
            broken = Path(tmp) / "broken.tex"
            broken.write_text(SAMPLE.read_text(encoding="utf-8").replace(
                "\\end{document}", "\\undefinedcommandxyz\n\\end{document}"), encoding="utf-8")
            tailor._propose_rewrites = lambda *a, **k: ({}, "none")
            bad = tailor.tailor("Java", company="Broken", job_id="e2e00002-y", master=str(broken))
            check("broken master fails cleanly", bad.ok, False)
            bad_dir = tailor.output_dir("Broken", "e2e00002-y")
            check("no resume.pdf from a failed build", (bad_dir / "resume.pdf").exists(), False)
            check("no staging file from a failed build", (bad_dir / "resume.building.pdf").exists(), False)

            # The case staging exists for: round one builds a PDF, a later
            # trim round fails. max_pages=0 forces a second round; every
            # compile after the first raises.
            real_compile, calls = rc.compile_tex, []

            def flaky(*a, **k):
                calls.append(1)
                if len(calls) == 1:
                    return real_compile(*a, **k)
                raise rc.CompileError("simulated failure on a later round")

            tailor.rc.compile_tex = flaky
            try:
                late = tailor.tailor("Java", company="Late", job_id="e2e00003-z",
                                     master=str(SAMPLE), max_pages=0)
            finally:
                tailor.rc.compile_tex = real_compile
            late_dir = tailor.output_dir("Late", "e2e00003-z")

            # Out of bullets to trim and still over the limit: not a success.
            long = tailor.tailor("Java", company="Long", job_id="e2e00004-w",
                                 master=str(SAMPLE), max_pages=0)
            check("over-length tailored resume fails", long.ok, False)
            check("...and leaves no resume.pdf to upload",
                  (tailor.output_dir("Long", "e2e00004-w") / "resume.pdf").exists(), False)
            check("late failure reported", late.ok, False)
            check("round one's PDF not left as resume.pdf", (late_dir / "resume.pdf").exists(), False)
        finally:
            tailor.TAILORED_DIR, tailor._propose_rewrites = saved_dir, saved_propose


def test_llm_resilience() -> None:
    """Retry, fallback and cooldown, against a fake client — no network, no key."""
    section("LLM resilience — rate limits, overload, fallback, cooldown")
    import types

    from backend.resume import llm

    class Err(Exception):
        def __init__(self, status, msg=""):
            super().__init__(msg or f"Error code: {status}")
            self.status_code = status

    quota = Err(429, "Error code: 429 - You exceeded your current quota. Please retry in 52.9s.")
    check("429 waits what the server names", llm._wait_for(quota, 0), 53.9)
    daily = Err(429, "quota exceeded. Please retry in 7200s.")
    check("hours-long wait fails fast", llm._wait_for(daily, 0), None)
    # The real daily-quota error names a short wait; the quota id is the truth.
    spent = Err(429, "Quota exceeded ... limit: 20 ... Please retry in 27.4s. "
                     "'quotaId': 'GenerateRequestsPerDayPerProjectPerModel-FreeTier'")
    check("spent daily quota is not retried", llm._wait_for(spent, 0), None)
    check("bad key does not retry", llm._wait_for(Err(401), 0), None)
    check("overload backs off", llm._wait_for(Err(503), 0), 5)

    calls: list = []
    script: dict = {}

    def create(model, **_):
        calls.append(model)
        outcome = script[model].pop(0) if script.get(model) else Err(503)
        if isinstance(outcome, Exception):
            raise outcome
        return types.SimpleNamespace(choices=[types.SimpleNamespace(
            message=types.SimpleNamespace(content=outcome))])

    fake = types.SimpleNamespace(chat=types.SimpleNamespace(
        completions=types.SimpleNamespace(create=create)))
    saved = (llm._client, llm.model_name, llm.fallback_model, llm.time.sleep, dict(llm._down_until))
    llm._client = lambda: fake
    llm.model_name = lambda: "primary"
    llm.fallback_model = lambda: "backup"
    llm.time.sleep = lambda s: None
    llm._down_until.clear()
    try:
        script.update(primary=[Err(503)], backup=[])
        script["primary"].append('{"ok": 1}')
        check("recovers after one overload", llm.ask_json("s", "u"), {"ok": 1})

        calls.clear()
        script.update(primary=[Err(503)] * 3, backup=['{"via": "backup"}'])
        check("falls back when primary is down", llm.ask_json("s", "u"), {"via": "backup"})
        check("primary tried 3 times, then backup", calls, ["primary"] * 3 + ["backup"])

        calls.clear()
        script.update(primary=['{"x": 1}'], backup=['{"via": "backup"}'])
        check("down model skipped during cooldown", llm.ask_json("s", "u"), {"via": "backup"})
        check("no call spent on the cooling model", calls, ["backup"])

        calls.clear()
        script.update(backup=[Err(503)] * 3)
        try:
            llm.ask_json("s", "u")
            outcome = "returned"
        except llm.LLMUnavailable:
            outcome = "LLMUnavailable"
        check("all down raises for the offline path", outcome, "LLMUnavailable")
        calls.clear()
        try:
            llm.ask_json("s", "u")
        except llm.LLMUnavailable:
            pass
        check("then fails with zero calls", calls, [])

        llm._down_until.clear()
        calls.clear()
        script.update(primary=[Err(401)], backup=['{"ok": 2}'])
        check("bad key is not retried on that model", llm.ask_json("s", "u"), {"ok": 2})
        check("one call on the refused model", calls.count("primary"), 1)

        # A JSON array parses fine and then crashes every caller's data.get().
        llm._down_until.clear()
        calls.clear()
        script.update(primary=['["not", "an", "object"]'] * 3, backup=['{"ok": 3}'])
        check("a non-object reply is not returned", llm.ask_json("s", "u"), {"ok": 3})

        # A timeout carries no status_code; it used to be retried per job.
        class APITimeoutError(Exception):
            pass

        llm._down_until.clear()
        calls.clear()
        script.update(primary=[APITimeoutError("timed out")], backup=['{"ok": 4}'])
        check("a timeout fails over at once", llm.ask_json("s", "u"), {"ok": 4})

        check("...after one attempt, not three", calls.count("primary"), 1)
        check("...and the model cools down", llm._down_until.get("primary", 0) > 0, True)

        # A reasoning model spending the budget thinking: retry once, bigger.
        budgets: list = []

        def thinking(model, **kw):
            budget = kw.get("max_tokens") or kw.get("max_completion_tokens")
            budgets.append(budget)
            done = budget >= 8000
            return types.SimpleNamespace(choices=[types.SimpleNamespace(
                finish_reason="stop" if done else "length",
                message=types.SimpleNamespace(content='{"ok": 5}' if done else '{"ok":'))])

        llm._down_until.clear()
        fake.chat.completions.create = thinking
        check("a truncated reply is retried with a bigger budget",
              llm.ask_json("s", "u", max_tokens=4000), {"ok": 5})
        check("...budget doubled once", budgets, [4000, 8000])
        fake.chat.completions.create = create
    finally:
        (llm._client, llm.model_name, llm.fallback_model, llm.time.sleep) = saved[:4]
        llm._down_until.clear()
        llm._down_until.update(saved[4])


def main() -> None:
    doc = test_parser()
    test_template_style()
    test_engine_compat()
    test_escaping()
    test_drop_and_edit(doc)
    test_matching()
    test_guard()
    test_guard_scoping()
    test_compile()
    test_one_page_fitter()
    test_tailored_lookup()
    test_reordering()
    test_send_log()
    test_arrangement_safety()
    test_tailor_end_to_end()
    test_llm_resilience()
    if "--llm" in sys.argv:
        test_llm()

    print()
    if failures:
        print(f"{len(failures)} FAILURE(S):")
        for line in failures:
            print(f"  - {line}")
        sys.exit(1)
    print("All checks passed.")


if __name__ == "__main__":
    main()
