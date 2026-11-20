"""Safe document reading (txt/md/docx) and writing (md/docx, optionally styled from a .docx template)."""

from __future__ import annotations

import io
import re
import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.image.exceptions import UnrecognizedImageError
from docx.oxml.ns import qn
from docx.shared import Inches, Pt
from docx.table import Table
from docx.text.paragraph import Paragraph

from .mermaid import render_mermaid, split_mermaid_diagrams
from .prompting import clean_output

MAX_FILE_BYTES = 25 * 1024 * 1024
MAX_DOCX_UNCOMPRESSED = 150 * 1024 * 1024
MAX_ZIP_ENTRIES = 2000
MAX_COMPRESSION_RATIO = 150
SUPPORTED_INPUT = (".txt", ".md", ".markdown", ".docx")
MONO = "Consolas"

_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\ufffe\uffff]")


class DocumentError(Exception):
    pass


# ----------------------------------------------------------------------------- reading

def check_docx_safe(path: Path) -> None:
    """Reject zip bombs / malformed archives before handing the file to the XML parser.

    python-docx parses XML with lxml and resolve_entities=False, so XXE is not possible;
    this adds resource-exhaustion protection.
    """
    try:
        with zipfile.ZipFile(path) as z:
            infos = z.infolist()
            if len(infos) > MAX_ZIP_ENTRIES:
                raise DocumentError("DOCX has too many internal entries.")
            total = 0
            for i in infos:
                total += i.file_size
                if i.compress_size and i.file_size > 1_000_000 and i.file_size / i.compress_size > MAX_COMPRESSION_RATIO:
                    raise DocumentError("DOCX has a suspicious compression ratio (possible zip bomb).")
            if total > MAX_DOCX_UNCOMPRESSED:
                raise DocumentError("DOCX is too large when uncompressed.")
            if "word/document.xml" not in z.namelist():
                raise DocumentError("Not a valid Word document.")
    except zipfile.BadZipFile:
        raise DocumentError("Not a valid .docx file (corrupt or wrong format).") from None


def read_document(path: str | Path) -> str:
    p = Path(path)
    if not p.is_file():
        raise DocumentError(f"File not found: {p}")
    ext = p.suffix.lower()
    if ext not in SUPPORTED_INPUT:
        raise DocumentError(f"Unsupported file type '{ext}'. Supported: {', '.join(SUPPORTED_INPUT)}")
    if p.stat().st_size > MAX_FILE_BYTES:
        raise DocumentError(f"File is larger than {MAX_FILE_BYTES // (1024 * 1024)} MB.")
    if ext == ".docx":
        check_docx_safe(p)
        try:
            return _docx_to_markdown(p)
        except DocumentError:
            raise
        except Exception as e:  # noqa: BLE001 - malformed docs raise many different errors
            raise DocumentError(f"Could not parse Word document: {type(e).__name__}") from None
    return _CTRL.sub("", p.read_bytes().decode("utf-8-sig", errors="replace"))


def _cell(s: str) -> str:
    return s.replace("|", "\\|").replace("\n", " ").strip()


def _para_md(p: Paragraph) -> str:
    text = p.text.strip()
    if not text:
        return ""
    try:
        name = p.style.name if p.style is not None else ""
    except Exception:  # noqa: BLE001
        name = ""
    m = re.match(r"Heading (\d)", name or "")
    if m:
        return "\n" + "#" * min(int(m.group(1)), 6) + " " + text + "\n"
    if name == "Title":
        return "\n# " + text + "\n"
    if name.startswith("List Number"):
        return "1. " + text
    if name.startswith("List") or (p._p.pPr is not None and p._p.pPr.numPr is not None):
        return "- " + text
    return text + "\n"


def _table_md(t: Table) -> str:
    rows = [[_cell(c.text) for c in r.cells] for r in t.rows]
    if not rows:
        return ""
    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]
    lines = ["| " + " | ".join(rows[0]) + " |", "|" + " --- |" * width]
    lines += ["| " + " | ".join(r) + " |" for r in rows[1:]]
    return "\n" + "\n".join(lines) + "\n"


def _docx_to_markdown(path: Path) -> str:
    doc = Document(str(path))
    out: list[str] = []
    for el in doc.element.body.iterchildren():
        if el.tag == qn("w:p"):
            out.append(_para_md(Paragraph(el, doc)))
        elif el.tag == qn("w:tbl"):
            out.append(_table_md(Table(el, doc)))
    text = re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip()
    return _CTRL.sub("", text) + "\n"


# ----------------------------------------------------------------------------- writing

_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
_BULLET = re.compile(r"^(\s*)[-*+]\s+(.*)$")
_NUMBER = re.compile(r"^(\s*)\d+[.)]\s+(.*)$")
_TABLE_SEP = re.compile(r"^\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?$")
_INLINE = re.compile(r"(\*\*[^*]+\*\*|`[^`]+`|\*[^*\s][^*]*\*)")
_LINK = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")


def _clean(text: str) -> str:
    return _CTRL.sub("", text)


def _try_style(obj, *names: str) -> None:
    for n in names:
        try:
            obj.style = n
            return
        except (KeyError, ValueError):  # nosec B112 - style missing in template; try next fallback
            continue


def _add_inline(p, text: str) -> None:
    text = _LINK.sub(r"\1 (\2)", _clean(text))
    for part in _INLINE.split(text):
        if not part:
            continue
        if part.startswith("**") and part.endswith("**") and len(part) > 4:
            p.add_run(part[2:-2]).bold = True
        elif part.startswith("`") and part.endswith("`") and len(part) > 2:
            p.add_run(part[1:-1]).font.name = MONO
        elif part.startswith("*") and part.endswith("*") and len(part) > 2:
            p.add_run(part[1:-1]).italic = True
        else:
            p.add_run(part)


def _par(doc, text: str, *styles: str) -> None:
    p = doc.add_paragraph()
    if styles:
        _try_style(p, *styles)
    _add_inline(p, text)


def _heading(doc, text: str, level: int) -> None:
    text = _clean(text.replace("**", "").replace("`", ""))
    try:
        doc.add_heading(text, level)
    except Exception:  # noqa: BLE001 - template lacks heading styles
        doc.add_paragraph().add_run(text).bold = True


def _code(doc, lines: list[str]) -> None:
    p = doc.add_paragraph()
    _try_style(p, "Code", "No Spacing")
    for i, line in enumerate(lines):
        r = p.add_run(_clean(line))
        r.font.name = MONO
        r.font.size = Pt(9)
        if i < len(lines) - 1:
            r.add_break()


def _split_row(line: str) -> list[str]:
    s = line.strip()
    s = s[1:] if s.startswith("|") else s
    s = s[:-1] if s.endswith("|") and not s.endswith("\\|") else s
    return [c.strip().replace("\\|", "|") for c in re.split(r"(?<!\\)\|", s)]


def _table(doc, lines: list[str]) -> None:
    rows = [_split_row(l) for l in lines if not _TABLE_SEP.match(l.strip())]
    if not rows:
        return
    cols = max(len(r) for r in rows)
    t = doc.add_table(rows=len(rows), cols=cols)
    _try_style(t, "Table Grid")
    for ri, row in enumerate(rows):
        for ci in range(cols):
            p = t.cell(ri, ci).paragraphs[0]
            _add_inline(p, row[ci] if ci < len(row) else "")
            if ri == 0:
                for r in p.runs:
                    r.bold = True
    doc.add_paragraph()


def _base_doc(template: str | Path | None):
    """Start from the .docx template (keeps its styles, page setup, headers/footers) with the body cleared."""
    if template and Path(template).suffix.lower() == ".docx" and Path(template).is_file():
        tp = Path(template)
        if tp.stat().st_size > MAX_FILE_BYTES:
            raise DocumentError("Template file is too large.")
        check_docx_safe(tp)
        doc = Document(str(tp))
        body = doc.element.body
        for child in list(body):
            if child.tag != qn("w:sectPr"):
                body.remove(child)
        return doc
    return Document()


def _apply_document_chrome(doc: Document, header_text: str, footer_text: str, logo_path: str | Path | None) -> None:
    """Apply the selected header, footer and logo to every document section."""
    logo = Path(logo_path) if logo_path else None
    if logo and not logo.is_file():
        logo = None

    for section in doc.sections:
        header = section.header
        header.is_linked_to_previous = False
        paragraph = header.paragraphs[0]
        if header_text:
            for existing in list(header.paragraphs):
                existing.clear()
            paragraph = header.paragraphs[0]
        if logo:
            try:
                run = paragraph.add_run()
                run.add_picture(str(logo), width=Inches(0.55))
                if header_text:
                    paragraph.add_run("  ")
            except (OSError, ValueError, UnrecognizedImageError) as e:
                raise DocumentError(f"Could not embed header logo '{logo}': {e}") from e
        if header_text:
            paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
            paragraph.add_run(header_text)

        footer = section.footer
        footer.is_linked_to_previous = False
        if footer_text:
            for existing in list(footer.paragraphs):
                existing.clear()
            paragraph = footer.paragraphs[0]
            paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
            paragraph.add_run(footer_text)


def markdown_to_docx(md: str, template: str | Path | None = None,
                     header_text: str = "", footer_text: str = "",
                     logo_path: str | Path | None = None) -> bytes:
    md, diagrams = split_mermaid_diagrams(clean_output(md))
    doc = _base_doc(template)
    _apply_document_chrome(doc, header_text, footer_text, logo_path)
    lines = md.replace("\r\n", "\n").split("\n")
    para: list[str] = []
    i = 0

    def flush() -> None:
        if para:
            _par(doc, " ".join(para))
            para.clear()

    while i < len(lines):
        line = lines[i]
        s = line.strip()
        if s.startswith("```"):
            flush()
            language = s[3:].strip().lower()
            i += 1
            code = []
            while i < len(lines) and not lines[i].strip().startswith("```"):
                code.append(lines[i])
                i += 1
            i += 1
            if language != "mermaid":
                _code(doc, code)
            continue
        if not s:
            flush()
        elif m := _HEADING.match(s):
            flush()
            _heading(doc, m.group(2).strip().rstrip("#").strip(), len(m.group(1)))
        elif s.startswith("|"):
            flush()
            tbl = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                tbl.append(lines[i])
                i += 1
            _table(doc, tbl)
            continue
        elif re.fullmatch(r"(-{3,}|\*{3,}|_{3,})", s):
            flush()
        elif m := _BULLET.match(line):
            flush()
            _par(doc, m.group(2), *(("List Bullet 2", "List Bullet") if len(m.group(1)) >= 2 else ("List Bullet",)))
        elif m := _NUMBER.match(line):
            flush()
            _par(doc, m.group(2), *(("List Number 2", "List Number") if len(m.group(1)) >= 2 else ("List Number",)))
        elif s.startswith(">"):
            flush()
            _par(doc, s.lstrip(">").strip(), "Intense Quote", "Quote")
        else:
            para.append(s)
        i += 1
    flush()
    has_diagrams = False
    for code in diagrams:
        with TemporaryDirectory(prefix="techdocforge-mermaid-") as td:
            image = render_mermaid(code, Path(td) / "diagram.png")
            if image:
                if not has_diagrams:
                    _heading(doc, "Diagrams", 2)
                    has_diagrams = True
                doc.add_picture(str(image), width=Inches(6.5))
                doc.add_paragraph()
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()
