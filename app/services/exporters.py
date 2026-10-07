"""인수인계서 Markdown → Word(.docx) / PDF 변환.

Markdown 은 mistune 으로 구문 트리를 만든 뒤 제목·문단·목록·표만 옮긴다(에이전트가 만드는 문서 형식).
- Word: 한글이 깨지지 않도록 동아시아 글꼴(eastAsia)을 '맑은 고딕'으로 지정
- PDF : reportlab. 한글 TTF 글꼴을 찾아 문서에 포함(나눔고딕·맑은 고딕·애플고딕·WenQuanYi …).
        못 찾으면 reportlab 내장 CID 글꼴(HYGothic-Medium, 뷰어 글꼴 사용)로 대체. AMIGO_PDF_FONT 로 지정 가능.
"""

from __future__ import annotations

import io
import os
from dataclasses import dataclass, field
from datetime import date
from xml.sax.saxutils import escape

import mistune

_MD = mistune.create_markdown(renderer=None, plugins=["table", "strikethrough"])


# --------------------------------------------------------------------------- Markdown → 블록
@dataclass
class Run:
    text: str
    bold: bool = False
    italic: bool = False
    code: bool = False


@dataclass
class Block:
    kind: str  # heading | paragraph | list_item | table | rule
    runs: list[Run] = field(default_factory=list)
    level: int = 0  # heading level / list depth
    ordered: bool = False
    number: int = 0
    rows: list[list[list[Run]]] = field(default_factory=list)  # table: rows → cells → runs (첫 행이 머리글)


def _runs(children: list[dict], bold: bool = False, italic: bool = False) -> list[Run]:
    out: list[Run] = []
    for node in children or []:
        kind = node.get("type")
        if kind == "text":
            out.append(Run(node.get("raw", ""), bold, italic))
        elif kind == "strong":
            out.extend(_runs(node.get("children", []), True, italic))
        elif kind == "emphasis":
            out.extend(_runs(node.get("children", []), bold, True))
        elif kind == "codespan":
            out.append(Run(node.get("raw", ""), bold, italic, code=True))
        elif kind in ("softbreak", "linebreak"):
            out.append(Run("\n", bold, italic))
        elif kind == "link":
            out.extend(_runs(node.get("children", []), bold, italic))
        elif "children" in node:
            out.extend(_runs(node["children"], bold, italic))
        elif "raw" in node:
            out.append(Run(node["raw"], bold, italic))
    return out


def markdown_blocks(markdown: str) -> list[Block]:
    blocks: list[Block] = []

    def walk_list(node: dict, depth: int) -> None:
        ordered = bool((node.get("attrs") or {}).get("ordered"))
        start = int((node.get("attrs") or {}).get("start") or 1)
        for index, item in enumerate(node.get("children", [])):
            for child in item.get("children", []):
                if child.get("type") in ("block_text", "paragraph"):
                    blocks.append(Block("list_item", _runs(child.get("children", [])), level=depth, ordered=ordered, number=start + index))
                elif child.get("type") == "list":
                    walk_list(child, depth + 1)

    for node in _MD(markdown or ""):
        kind = node.get("type")
        if kind == "heading":
            blocks.append(Block("heading", _runs(node.get("children", [])), level=int(node["attrs"]["level"])))
        elif kind == "paragraph":
            blocks.append(Block("paragraph", _runs(node.get("children", []))))
        elif kind == "list":
            walk_list(node, 0)
        elif kind == "table":
            rows: list[list[list[Run]]] = []
            for part in node.get("children", []):
                if part.get("type") == "table_head":
                    rows.append([_runs(cell.get("children", [])) for cell in part.get("children", [])])
                elif part.get("type") == "table_body":
                    for row in part.get("children", []):
                        rows.append([_runs(cell.get("children", [])) for cell in row.get("children", [])])
            blocks.append(Block("table", rows=rows))
        elif kind == "thematic_break":
            blocks.append(Block("rule"))
        elif kind == "block_quote":
            for child in node.get("children", []):
                blocks.append(Block("paragraph", _runs(child.get("children", []), italic=True)))
    return blocks


def _plain(runs: list[Run]) -> str:
    return "".join(r.text for r in runs)


def _display_width(text: str) -> int:
    return sum(2 if "\u1100" <= ch <= "\uffdc" else 1 for ch in text)


# --------------------------------------------------------------------------- Word
KR_FONT = "맑은 고딕"


def to_docx(markdown: str, *, title: str = "업무 인수인계서") -> bytes:
    from docx import Document
    from docx.enum.table import WD_TABLE_ALIGNMENT
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.shared import Cm, Pt, RGBColor

    doc = Document()
    doc.core_properties.title = title
    doc.core_properties.author = "AMIGO 인수인계 시스템"
    for section in doc.sections:
        section.left_margin = section.right_margin = Cm(2.0)
        section.top_margin = section.bottom_margin = Cm(2.0)

    def set_font(style_or_run, size: float | None = None) -> None:
        font = style_or_run.font
        font.name = KR_FONT
        if size:
            font.size = Pt(size)
        rpr = style_or_run.element.get_or_add_rPr()
        rfonts = rpr.find(qn("w:rFonts"))
        if rfonts is None:
            rfonts = OxmlElement("w:rFonts")
            rpr.insert(0, rfonts)
        for attr in ("w:ascii", "w:hAnsi", "w:eastAsia", "w:cs"):
            rfonts.set(qn(attr), KR_FONT)
        for attr in ("w:asciiTheme", "w:hAnsiTheme", "w:eastAsiaTheme", "w:cstheme"):
            if rfonts.get(qn(attr)) is not None:
                del rfonts.attrib[qn(attr)]

    set_font(doc.styles["Normal"], 10)
    for name, size in (("Title", 22), ("Heading 1", 18), ("Heading 2", 14), ("Heading 3", 12)):
        set_font(doc.styles[name], size)
        doc.styles[name].font.color.rgb = RGBColor(0x1F, 0x2A, 0x44)

    def add_runs(paragraph, runs: list[Run], size: float | None = None) -> None:
        for run in runs:
            for i, piece in enumerate(run.text.split("\n")):
                if i:
                    paragraph.add_run().add_break()
                r = paragraph.add_run(piece)
                r.bold = run.bold or None
                r.italic = run.italic or None
                set_font(r, size)

    def shade(cell, color: str) -> None:
        tc_pr = cell._tc.get_or_add_tcPr()
        shd = OxmlElement("w:shd")
        shd.set(qn("w:val"), "clear")
        shd.set(qn("w:color"), "auto")
        shd.set(qn("w:fill"), color)
        tc_pr.append(shd)

    for block in markdown_blocks(markdown):
        if block.kind == "heading":
            style = "Title" if block.level == 1 else f"Heading {min(block.level - 1, 3)}"
            add_runs(doc.add_paragraph(style=style), block.runs)
        elif block.kind == "paragraph":
            add_runs(doc.add_paragraph(), block.runs)
        elif block.kind == "list_item" and block.ordered:
            # 근거 번호([1], [2] …)와 어긋나지 않도록 Word 자동 번호 대신 Markdown 의 번호를 그대로 쓴다
            paragraph = doc.add_paragraph()
            paragraph.paragraph_format.left_indent = Cm(0.6 + 0.6 * block.level)
            paragraph.paragraph_format.first_line_indent = Cm(-0.6)
            add_runs(paragraph, [Run(f"{block.number}. ")] + block.runs)
        elif block.kind == "list_item":
            style = "List Bullet" if block.level == 0 else f"List Bullet {min(block.level + 1, 3)}"
            add_runs(doc.add_paragraph(style=style), block.runs)
        elif block.kind == "table" and block.rows:
            cols = max(len(r) for r in block.rows)
            table = doc.add_table(rows=len(block.rows), cols=cols)
            table.style = "Table Grid"
            table.alignment = WD_TABLE_ALIGNMENT.CENTER
            for r_index, row in enumerate(block.rows):
                for c_index in range(cols):
                    cell = table.cell(r_index, c_index)
                    runs = row[c_index] if c_index < len(row) else []
                    if r_index == 0:
                        runs = [Run(x.text, True, x.italic) for x in runs]
                        shade(cell, "E8EEF7")
                    add_runs(cell.paragraphs[0], runs, size=9)
            doc.add_paragraph()
        elif block.kind == "rule":
            doc.add_paragraph("―" * 30)

    buffer = io.BytesIO()
    doc.save(buffer)
    return buffer.getvalue()


# --------------------------------------------------------------------------- PDF
_FONT_CANDIDATES = [
    # (일반, 굵게, TTC 하위 글꼴 번호)
    ("/usr/share/fonts/truetype/nanum/NanumGothic.ttf", "/usr/share/fonts/truetype/nanum/NanumGothicBold.ttf", 0),
    ("C:/Windows/Fonts/malgun.ttf", "C:/Windows/Fonts/malgunbd.ttf", 0),
    ("/Library/Fonts/NanumGothic.ttf", "/Library/Fonts/NanumGothicBold.ttf", 0),
    ("/System/Library/Fonts/Supplemental/AppleGothic.ttf", None, 0),
    ("/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc", None, 0),
    ("/usr/share/fonts/truetype/unfonts-core/UnDotum.ttf", "/usr/share/fonts/truetype/unfonts-core/UnDotumBold.ttf", 0),
]
_REGISTERED: tuple[str, str] | None = None


def _pdf_fonts() -> tuple[str, str]:
    """(본문 글꼴, 굵은 글꼴) 이름을 등록하고 돌려준다."""
    global _REGISTERED
    if _REGISTERED:
        return _REGISTERED
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont
    from reportlab.pdfbase.ttfonts import TTFont

    candidates = list(_FONT_CANDIDATES)
    custom = os.getenv("AMIGO_PDF_FONT")
    if custom:
        candidates.insert(0, (custom, os.getenv("AMIGO_PDF_FONT_BOLD") or None, 0))
    for regular, bold, index in candidates:
        if not regular or not os.path.isfile(regular):
            continue
        try:
            pdfmetrics.registerFont(TTFont("AmigoKR", regular, subfontIndex=index))
            bold_name = "AmigoKR"
            if bold and os.path.isfile(bold):
                pdfmetrics.registerFont(TTFont("AmigoKR-Bold", bold, subfontIndex=index))
                bold_name = "AmigoKR-Bold"
            pdfmetrics.registerFontFamily("AmigoKR", normal="AmigoKR", bold=bold_name, italic="AmigoKR", boldItalic=bold_name)
            _REGISTERED = ("AmigoKR", bold_name)
            return _REGISTERED
        except Exception:  # CFF 기반 OTF 등 reportlab 이 못 읽는 글꼴은 건너뛴다
            continue
    pdfmetrics.registerFont(UnicodeCIDFont("HYGothic-Medium"))
    _REGISTERED = ("HYGothic-Medium", "HYGothic-Medium")
    return _REGISTERED


def _markup(runs: list[Run]) -> str:
    parts = []
    for run in runs:
        text = escape(run.text).replace("\n", "<br/>")
        if run.bold:
            text = f"<b>{text}</b>"
        parts.append(text)
    return "".join(parts)


def to_pdf(markdown: str, *, title: str = "업무 인수인계서") -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_LEFT
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import HRFlowable, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    regular, bold = _pdf_fonts()
    navy = colors.HexColor("#1F2A44")
    body = ParagraphStyle("body", fontName=regular, fontSize=9.5, leading=14.5, wordWrap="CJK", alignment=TA_LEFT, spaceAfter=4)
    cell = ParagraphStyle("cell", parent=body, fontSize=8.5, leading=12, spaceAfter=0)
    head_cell = ParagraphStyle("head_cell", parent=cell, fontName=bold)
    headings = {
        1: ParagraphStyle("h1", parent=body, fontName=bold, fontSize=18, leading=24, textColor=navy, spaceAfter=10),
        2: ParagraphStyle("h2", parent=body, fontName=bold, fontSize=13, leading=18, textColor=navy, spaceBefore=10, spaceAfter=6),
        3: ParagraphStyle("h3", parent=body, fontName=bold, fontSize=11, leading=16, textColor=navy, spaceBefore=6, spaceAfter=4),
    }

    page_width = A4[0] - 36 * mm
    story = []
    for block in markdown_blocks(markdown):
        if block.kind == "heading":
            story.append(Paragraph(_markup(block.runs), headings[min(block.level, 3)]))
            if block.level == 2:
                story.append(HRFlowable(width="100%", thickness=0.6, color=colors.HexColor("#C9D3E3"), spaceAfter=4))
        elif block.kind == "paragraph":
            story.append(Paragraph(_markup(block.runs), body))
        elif block.kind == "list_item":
            bullet = f"{block.number}." if block.ordered else "•"
            style = ParagraphStyle("li", parent=body, leftIndent=12 + 12 * block.level, bulletIndent=2 + 12 * block.level)
            story.append(Paragraph(_markup(block.runs), style, bulletText=bullet))
        elif block.kind == "table" and block.rows:
            cols = max(len(r) for r in block.rows)
            # 열 너비: 한글은 영문보다 두 배 넓으므로 표시 폭 기준으로 나누고, 짧은 열도 최소 4글자 폭은 보장한다
            lengths = [max(_display_width(_plain(r[c])) if c < len(r) else 0 for r in block.rows) for c in range(cols)]
            weights = [min(max(n, 9), 70) for n in lengths]
            total = sum(weights) or 1
            widths = [page_width * w / total for w in weights]
            data = []
            for r_index, row in enumerate(block.rows):
                style = head_cell if r_index == 0 else cell
                data.append([Paragraph(_markup(row[c]) if c < len(row) else "", style) for c in range(cols)])
            table = Table(data, colWidths=widths, repeatRows=1)
            table.setStyle(
                TableStyle(
                    [
                        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#9AA8BD")),
                        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E8EEF7")),
                        ("VALIGN", (0, 0), (-1, -1), "TOP"),
                        ("LEFTPADDING", (0, 0), (-1, -1), 4),
                        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                    ]
                )
            )
            story += [table, Spacer(1, 6)]
        elif block.kind == "rule":
            story.append(HRFlowable(width="100%", thickness=0.5, color=colors.grey))

    def footer(canvas, doc_template) -> None:
        canvas.saveState()
        canvas.setFont(regular, 8)
        canvas.setFillColor(colors.grey)
        canvas.drawString(18 * mm, 10 * mm, f"{title} · AMIGO 자동 작성 · {date.today().isoformat()}")
        canvas.drawRightString(A4[0] - 18 * mm, 10 * mm, f"{doc_template.page}")
        canvas.restoreState()

    buffer = io.BytesIO()
    SimpleDocTemplate(
        buffer, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm, topMargin=16 * mm, bottomMargin=18 * mm, title=title, author="AMIGO"
    ).build(story or [Paragraph("(내용 없음)", body)], onFirstPage=footer, onLaterPages=footer)
    return buffer.getvalue()
