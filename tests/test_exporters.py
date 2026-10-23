import io
import zipfile

from pypdf import PdfReader

from app.services.exporters import markdown_blocks, to_docx, to_pdf

SAMPLE = """# 업무 인수인계서

| 구분 | 내용 |
|---|---|
| 인계자 | 김민수 **과장** |

## 1. 업무 개요

홈페이지 운영 전반을 인계합니다.

**인수자 핵심 당부사항**

1. 11월 웹 접근성 인증 갱신
2. 리뉴얼 예산 확인

## 9. 근거 자료

1. 업무정의서.pdf p.2 — "재무팀과 협의한다"
- 시스템_계정목록.xlsx (xlsx)
"""


def test_markdown_blocks_structure():
    blocks = markdown_blocks(SAMPLE)
    kinds = [b.kind for b in blocks]
    assert kinds[:2] == ["heading", "table"]
    table = blocks[1]
    assert ["".join(r.text for r in cell) for cell in table.rows[1]] == ["인계자", "김민수 과장"]
    assert any(r.bold for r in table.rows[1][1])
    numbered = [b for b in blocks if b.kind == "list_item" and b.ordered]
    assert [b.number for b in numbered] == [1, 2, 1]  # 문서 안의 번호를 그대로 유지


def test_docx_keeps_korean_and_numbering():
    data = to_docx(SAMPLE, title="테스트")
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        xml = zf.read("word/document.xml").decode("utf-8")
    assert "김민수" in xml and 'w:eastAsia="맑은 고딕"' in xml
    assert "1. " in xml and "업무정의서.pdf p.2" in xml


def test_pdf_has_embedded_korean_text():
    data = to_pdf(SAMPLE, title="테스트")
    assert data.startswith(b"%PDF")
    text = "".join(p.extract_text() for p in PdfReader(io.BytesIO(data)).pages)
    for word in ("업무", "인수인계서", "김민수", "홈페이지"):
        assert word in text


# ---------------------------------------------------------------- HTML 표(병합 셀·막대 색)
HTML_DOC = """# 업무 인수인계서

## 1. 업무 단위 요약 + 일정 명세표

<table>
<tr><th>단위업무명</th><th>1월</th><th>2월</th><th>3월</th><th>4월</th><th>비고</th></tr>
<tr><td>공동망 운영</td><td class="bar" bgcolor="#7055C8" colspan="2">&nbsp;</td><td></td><td></td><td>상시 &amp; 점검</td></tr>
</table>

### 1) 홈페이지 리뉴얼

<table>
<tr><th>항목</th><th colspan="3">내용</th></tr>
<tr><th>추진 경과 및 현황</th><td colspan="3">RFP 작성 중</td></tr>
<tr><th rowspan="3">주요 협업관계 및 협의사항</th><th>담당자</th><th>협의내용</th><th>추후 고려사항</th></tr>
<tr><td>재무팀 박지훈 차장</td><td>예산 3억 원</td><td>이사회 승인 후 계약</td></tr>
<tr><td>IT지원팀 최영호 과장</td><td>계정 발급</td><td>-</td></tr>
<tr><th>중점관리사항</th><td colspan="3">계약 전 승인</td></tr>
</table>

| 구분 | 내용 |
|---|---|
| 일반 표 | 그대로 |
"""


def test_html_tables_become_blocks_with_merged_cells_and_bar_colors():
    tables = [b for b in markdown_blocks(HTML_DOC) if b.kind == "table"]
    gantt, card, plain = tables
    # 월별 일정표: 막대 칸은 2개월 폭으로 합쳐지고 색이 칠해진다. 합쳐진 오른쪽 칸은 비어 있다
    assert gantt.spans == {(1, 1): (1, 2)} and gantt.fills == {(1, 1): "7055C8"}
    assert [len(r) for r in gantt.rows] == [6, 6]
    assert "".join(x.text for x in gantt.rows[1][5]) == "상시 & 점검"
    assert gantt.is_header(0, 3) and not gantt.is_header(1, 0)
    # 협업관계 칸: 왼쪽 라벨은 3줄에 걸쳐 합쳐지고, 아래 두 줄은 라벨 칸을 건너뛰어 오른쪽 세 칸에 놓인다
    assert card.spans[(2, 0)] == (3, 1) and card.spans[(0, 1)] == (1, 3)
    texts = [["".join(x.text for x in cell) for cell in row] for row in card.rows]
    assert texts[2] == ["주요 협업관계 및 협의사항", "담당자", "협의내용", "추후 고려사항"]
    assert texts[3] == ["", "재무팀 박지훈 차장", "예산 3억 원", "이사회 승인 후 계약"]
    assert texts[4] == ["", "IT지원팀 최영호 과장", "계정 발급", "-"]
    assert texts[5][0] == "중점관리사항" and all(len(r) == 4 for r in texts)
    # Markdown 표는 예전처럼 첫 행이 머리글이다
    assert plain.headers is None and plain.is_header(0, 1) and not plain.is_header(1, 0)


def _table_xml(docx_bytes: bytes) -> str:
    return zipfile.ZipFile(io.BytesIO(docx_bytes)).read("word/document.xml").decode("utf-8")


def test_docx_merges_cells_and_colors_bars():
    xml = _table_xml(to_docx(HTML_DOC))
    assert 'w:gridSpan w:val="2"' in xml and 'w:gridSpan w:val="3"' in xml  # 가로 병합(월 막대, 내용 칸)
    assert 'w:vMerge w:val="restart"' in xml  # 세로 병합(협업관계 라벨)
    # 막대는 칸 전체를 칠하지 않고 칸 가운데의 얇은 줄(문단 위쪽 테두리)로 그린다
    assert 'w:top w:val="single" w:sz="36" w:space="0" w:color="7055C8"' in xml and 'w:fill="7055C8"' not in xml
    for text in ("주요 협업관계 및 협의사항", "재무팀 박지훈 차장", "이사회 승인 후 계약", "상시 &amp; 점검"):  # XML 에서는 & 가 &amp; 로 저장된다
        assert text in xml


def test_pdf_renders_html_tables_with_korean_text():
    pdf = to_pdf(HTML_DOC)
    assert pdf.startswith(b"%PDF")
    text = "".join(page.extract_text() or "" for page in PdfReader(io.BytesIO(pdf)).pages)
    assert "재무팀 박지훈 차장" in text and "추후 고려사항" in text and "공동망 운영" in text
