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
