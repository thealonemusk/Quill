"""
Compile a LaTeX resume to PDF with Tectonic, and audit the result.

Tectonic is a single vendored binary (`tools/tectonic.exe`) — no TeX
distribution is installed on the machine. Its first ever run downloads a package
bundle (~3 minutes); every run after that is ~3 seconds, which is what makes the
trim-and-recompile loop in `tailor.py` practical.

Auditing matters as much as compiling. A PDF can build perfectly and still be
unreadable to an ATS, so `audit()` reads the text back out of the finished file
and reports what a parser would actually see.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from backend.resume import texdoc

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_TECTONIC = PROJECT_ROOT / "tools" / "tectonic.exe"

# Tectonic's first run fetches the bundle; later runs hit the cache.
FIRST_RUN_TIMEOUT = 600
WARM_TIMEOUT = 120


class CompileError(RuntimeError):
    """Raised when LaTeX refuses to build the document."""

    def __init__(self, message: str, log: str = "") -> None:
        super().__init__(message)
        self.log = log


def tectonic_path(configured: str = "") -> Optional[Path]:
    """Locate the Tectonic binary: configured path, vendored copy, then PATH."""
    if configured:
        p = Path(configured)
        if p.is_file():
            return p
    if DEFAULT_TECTONIC.is_file():
        return DEFAULT_TECTONIC
    found = shutil.which("tectonic")
    return Path(found) if found else None


def is_available(configured: str = "") -> bool:
    return tectonic_path(configured) is not None


@dataclass
class Audit:
    """What an ATS would actually get out of the compiled PDF."""

    pages: int
    text: str
    chars: int
    fonts: List[str] = field(default_factory=list)
    images: int = 0
    tables: int = 0
    ligatures: List[str] = field(default_factory=list)
    hyphen_splits: List[str] = field(default_factory=list)
    non_ascii: List[str] = field(default_factory=list)

    @property
    def clean(self) -> bool:
        return not self.ligatures and not self.hyphen_splits and self.chars > 500

    def problems(self) -> List[str]:
        out = []
        if self.chars < 500:
            out.append(f"only {self.chars} characters extractable — the PDF may be an image")
        if self.ligatures:
            out.append(f"ligature glyphs an ATS cannot match: {', '.join(self.ligatures)}")
        if self.hyphen_splits:
            out.append(f"words split across lines: {', '.join(self.hyphen_splits[:5])}")
        if self.images:
            out.append(f"{self.images} image(s) — text inside them is invisible to a parser")
        if self.tables:
            out.append(f"{self.tables} table(s) — a common cause of scrambled parsing")
        return out


def audit_pdf(pdf: Path) -> Audit:
    """Read the PDF back and report what a text-extracting parser sees."""
    import fitz

    doc = fitz.open(pdf)
    text = "".join(page.get_text() for page in doc)

    fonts, images, tables = set(), 0, 0
    for page in doc:
        for block in page.get_text("dict")["blocks"]:
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    fonts.add(span["font"])
        images += len(page.get_images())
        try:
            tables += len(list(page.find_tables()))
        except Exception:
            pass

    ligatures = sorted(
        f"{glyph!r}->{plain}"
        for glyph, plain in texdoc.LIGATURE_GLYPHS.items()
        if glyph in text
    )
    # "micro-\nservices" reads as two tokens to a keyword matcher.
    hyphen_splits = re.findall(r"(\w+)-\n(\w+)", text)
    splits = [f"{a}-{b}" for a, b in hyphen_splits]

    non_ascii = sorted({c for c in text if ord(c) > 127 and c not in texdoc.LIGATURE_GLYPHS})

    return Audit(
        pages=len(doc),
        text=text,
        chars=len(text.strip()),
        fonts=sorted(fonts),
        images=images,
        tables=tables,
        ligatures=ligatures,
        hyphen_splits=splits,
        non_ascii=non_ascii,
    )


def compile_tex(
    source: str,
    out_pdf: Path,
    tectonic: str = "",
    timeout: Optional[int] = None,
) -> Audit:
    """
    Build `source` into `out_pdf` and return an audit of the result.

    Compilation happens in a throwaway directory so a failed run cannot leave
    half-written artefacts next to the user's files.
    """
    binary = tectonic_path(tectonic)
    if binary is None:
        raise CompileError(
            "Tectonic is not installed. Expected it at tools/tectonic.exe — "
            "run scripts/install_tectonic.py."
        )

    out_pdf = Path(out_pdf)
    out_pdf.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="Quill-tex-") as tmp:
        work = Path(tmp)
        tex = work / "resume.tex"
        tex.write_text(source, encoding="utf-8")

        proc = subprocess.run(
            [str(binary), "-X", "compile", str(tex), "--outdir", str(work)],
            capture_output=True,
            text=True,
            timeout=timeout or WARM_TIMEOUT,
        )
        built = work / "resume.pdf"
        if not built.is_file():
            raise CompileError(_first_tex_error(proc.stderr or proc.stdout), proc.stderr or "")
        shutil.copyfile(built, out_pdf)

    return audit_pdf(out_pdf)


def _first_tex_error(log: str) -> str:
    """Pull the useful line out of a LaTeX log; they are mostly noise."""
    for line in (log or "").splitlines():
        if line.startswith("! "):
            return line[2:].strip()
    for line in (log or "").splitlines():
        if "error:" in line.lower():
            return line.strip()
    return "LaTeX failed to produce a PDF"
