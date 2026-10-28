"""가상 Confluence 사이트: app/mock/confluence.py 의 예시 페이지를 Confluence 처럼 생긴 HTML 화면으로 보여 준다.

가상 데이터의 페이지 링크(url)가 실제로 열리도록 Confluence 와 같은 주소 모양을 쓴다.
  /wiki                                  대시보드(최근 수정 페이지 · 스페이스)
  /wiki/spaces/{key}                     스페이스 홈(페이지 트리)
  /wiki/pages/viewpage.action?pageId=…   페이지 보기
  /wiki/dosearchsite.action?queryString= 검색
"""

from __future__ import annotations

import html
import re

import mistune
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import HTMLResponse

from ..mock import confluence

router = APIRouter(prefix="/wiki", include_in_schema=False)

_MD = mistune.create_markdown(escape=True, plugins=["table", "strikethrough"])

PREFIX = "/wiki"


def _e(text: str) -> str:
    return html.escape(str(text), quote=True)


def _page_href(page_id: str) -> str:
    return f"{PREFIX}/pages/viewpage.action?pageId={page_id}"


def _space_href(key: str) -> str:
    return f"{PREFIX}/spaces/{key}"


def _mark(text: str, terms: list[str]) -> str:
    """이미 이스케이프하지 않은 글자에서 검색어를 <mark> 로 감싼다."""
    escaped = _e(text)
    if not terms:
        return escaped
    pattern = re.compile("|".join(re.escape(_e(t)) for t in sorted(terms, key=len, reverse=True)), re.IGNORECASE)
    return pattern.sub(lambda m: f"<mark>{m.group(0)}</mark>", escaped)


def _avatar(name: str) -> str:
    return f'<span class="avatar" aria-hidden="true">{_e(name[:1])}</span>'


def _space_icon(key: str, size: str = "") -> str:
    hue = sum(map(ord, key)) * 47 % 360
    return f'<span class="space-icon {size}" style="--hue:{hue}">{_e(key[:1])}</span>'


def _tree(space: str, current: str = "") -> str:
    """스페이스의 페이지를 상위 페이지(ancestors) 경로로 묶어 트리로 그린다. 페이지가 없는 상위 제목은 폴더로 보인다."""
    root: dict = {}
    for page in confluence.PAGES:
        if page.space != space:
            continue
        node = root
        for title in page.ancestors:
            node = node.setdefault(title, {})
        node.setdefault("\0pages", []).append(page)

    def render(node: dict) -> str:
        items = []
        for title, child in node.items():
            if title == "\0pages":
                continue
            items.append(f'<li><details open><summary>{_e(title)}</summary>{render(child)}</details></li>')
        for page in sorted(node.get("\0pages", []), key=lambda p: p.title):
            cls = ' class="current"' if page.id == current else ""
            items.append(f'<li><a{cls} href="{_page_href(page.id)}">{_e(page.title)}</a></li>')
        return f"<ul>{''.join(items)}</ul>" if items else ""

    return render(root)


def _layout(title: str, main: str, *, space: str = "", current: str = "", query: str = "") -> str:
    if space:
        sidebar = f"""
        <aside class="sidebar">
          <a class="space-head" href="{_space_href(space)}">{_space_icon(space)}<span><strong>{_e(confluence.SPACES.get(space, space))}</strong><small>스페이스</small></span></a>
          <nav class="side-links">
            <a href="{_space_href(space)}">스페이스 홈</a>
            <a href="{PREFIX}/dosearchsite.action?queryString=&amp;where={space}">이 스페이스 검색</a>
          </nav>
          <div class="side-title">페이지</div>
          <nav class="tree">{_tree(space, current)}</nav>
        </aside>"""
    else:
        links = "".join(
            f'<a href="{_space_href(k)}">{_space_icon(k, "sm")}{_e(v)}</a>' for k, v in confluence.SPACES.items()
        )
        sidebar = f"""
        <aside class="sidebar">
          <nav class="side-links">
            <a href="{PREFIX}">홈</a>
            <a href="{PREFIX}/dosearchsite.action?queryString=">최근 항목</a>
          </nav>
          <div class="side-title">스페이스</div>
          <nav class="side-links spaces">{links}</nav>
        </aside>"""
    return f"""<!doctype html>
<html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_e(title)} - 컨플루언스</title>
<style>{_CSS}</style></head>
<body>
<header class="topbar">
  <a class="logo" href="{PREFIX}"><svg viewBox="0 0 32 32" width="22" height="22" aria-hidden="true"><path fill="#1868DB" d="M4.2 22.5c-.3.5-.6 1.1-.9 1.5-.2.4-.1 1 .3 1.2l5.6 3.4c.4.3 1 .1 1.3-.3.2-.4.5-.9.9-1.5 2.4-4 4.9-3.5 9.3-1.4l5.6 2.6c.5.2 1 0 1.2-.5l2.7-6c.2-.5 0-1-.4-1.2-1.2-.6-3.5-1.6-5.6-2.6-7.6-3.7-14-3.4-20 4.8z"/><path fill="#1868DB" d="M27.8 9.5c.3-.5.6-1.1.9-1.5.2-.4.1-1-.3-1.2L22.8 3.4c-.4-.3-1-.1-1.3.3-.2.4-.5.9-.9 1.5-2.4 4-4.9 3.5-9.3 1.4L5.7 4c-.5-.2-1 0-1.2.5l-2.7 6c-.2.5 0 1 .4 1.2 1.2.6 3.5 1.6 5.6 2.6 7.6 3.7 14 3.4 20-4.8z"/></svg><span>Confluence</span></a>
  <nav class="topnav"><a href="{PREFIX}">홈</a><a href="{PREFIX}/dosearchsite.action?queryString=">최근 항목</a><a href="{_space_href('WEB')}">스페이스</a></nav>
  <span class="create">+ 만들기</span>
  <form class="search" action="{PREFIX}/dosearchsite.action"><input name="queryString" value="{_e(query)}" placeholder="검색" aria-label="검색"></form>
  {_avatar("김민수")}
</header>
<div class="mockbar">가상 컨플루언스입니다 · AMIGO 가상 응답 모드의 예시 데이터이며 실제 사내 위키와 연결되어 있지 않습니다.</div>
<div class="shell">{sidebar}<main class="main">{main}</main></div>
</body></html>"""


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
def dashboard():
    recent = sorted(confluence.PAGES, key=lambda p: p.updated_at, reverse=True)
    rows = "".join(
        f"""<li class="card-row"><a href="{_page_href(p.id)}"><span class="doc-icon"></span><span><strong>{_e(p.title)}</strong>
        <small>{_e(confluence.SPACES.get(p.space, p.space))} · {_e(p.author)} · {_e(p.updated_at)} 수정</small></span></a></li>"""
        for p in recent
    )
    spaces = "".join(
        f"""<a class="space-card" href="{_space_href(k)}">{_space_icon(k, "lg")}<strong>{_e(v)}</strong>
        <small>페이지 {sum(1 for p in confluence.PAGES if p.space == k)}개</small></a>"""
        for k, v in confluence.SPACES.items()
    )
    main = f"""<h1 class="page-title">홈</h1>
    <h2 class="section">스페이스</h2><div class="space-grid">{spaces}</div>
    <h2 class="section">최근 업데이트</h2><ul class="card-list">{rows}</ul>"""
    return _layout("홈", main)


@router.get("/spaces/{key}", response_class=HTMLResponse)
def space_home(key: str):
    key = key.upper()
    if key not in confluence.SPACES:
        raise HTTPException(404, "없는 스페이스입니다.")
    pages = sorted((p for p in confluence.PAGES if p.space == key), key=lambda p: p.updated_at, reverse=True)
    rows = "".join(
        f"""<li class="card-row"><a href="{_page_href(p.id)}"><span class="doc-icon"></span><span><strong>{_e(p.title)}</strong>
        <small>{_e(' › '.join(p.ancestors) or '최상위')} · {_e(p.author)} · {_e(p.updated_at)} 수정</small></span></a></li>"""
        for p in pages
    )
    name = confluence.SPACES[key]
    main = f"""<div class="space-hero">{_space_icon(key, "xl")}<div><h1 class="page-title">{_e(name)}</h1>
    <p class="muted">스페이스 키 {_e(key)} · 페이지 {len(pages)}개</p></div></div>
    <h2 class="section">이 스페이스의 페이지</h2><ul class="card-list">{rows}</ul>"""
    return _layout(name, main, space=key)


@router.get("/pages/viewpage.action", response_class=HTMLResponse)
def view_page(pageId: str = Query(...)):
    page = confluence.get_page(pageId)
    if page is None:
        raise HTTPException(404, "없는 페이지입니다.")
    space = confluence.SPACES.get(page.space, page.space)
    crumbs = [f'<a href="{_space_href(page.space)}">{_e(space)}</a>', *(f"<span>{_e(t)}</span>" for t in page.ancestors)]
    labels = "".join(
        f'<a class="label" href="{PREFIX}/dosearchsite.action?queryString={_e(label)}">{_e(label)}</a>' for label in page.labels
    )
    main = f"""<nav class="crumbs">{' <i>/</i> '.join(crumbs)}</nav>
    <h1 class="page-title">{_e(page.title)}</h1>
    <div class="byline">{_avatar(page.author)}<span>작성자 <strong>{_e(page.author)}</strong> · 최종 수정 {_e(page.updated_at)} · 버전 {page.version}</span></div>
    <article class="wiki-content">{_MD(page.body)}</article>
    {f'<div class="labels"><span class="muted">라벨</span>{labels}</div>' if labels else ''}
    <div class="comments"><h3>댓글 0개</h3><div class="comment-box">{_avatar("김민수")}<span>댓글 추가…</span></div></div>"""
    return _layout(page.title, main, space=page.space, current=page.id)


@router.get("/dosearchsite.action", response_class=HTMLResponse)
def search(queryString: str = "", where: str = ""):
    found = confluence.search_pages(queryString, space=where.upper(), team="*")
    terms = found["terms"]
    rows = "".join(
        f"""<li class="result"><a class="result-title" href="{_page_href(r['id'])}">{_mark(r['title'], terms)}</a>
        <div class="result-path">{_e(' › '.join([r['space_name'], *r['ancestors']]))}</div>
        <p>{_mark(r['excerpt'], terms)}</p>
        <small class="muted">{_e(r['author'])} · {_e(r['updated_at'])} 수정</small></li>"""
        for r in found["results"]
    )
    options = "".join(
        f'<option value="{k}"{" selected" if k == where.upper() else ""}>{_e(v)}</option>' for k, v in confluence.SPACES.items()
    )
    heading = f"'{_e(found['query'])}' 검색 결과 {found['total']}건" if found["query"] else f"모든 페이지 {found['total']}개"
    main = f"""<h1 class="page-title">검색</h1>
    <form class="search-big" action="{PREFIX}/dosearchsite.action">
      <input name="queryString" value="{_e(queryString)}" placeholder="예: 인증서, 유지보수 검수" autofocus>
      <select name="where"><option value="">모든 스페이스</option>{options}</select>
      <button>검색</button>
    </form>
    <p class="muted">{heading}</p>
    <ul class="results">{rows or '<li class="muted">결과가 없습니다. 다른 검색어를 입력해 보세요.</li>'}</ul>"""
    return _layout("검색", main, space=where.upper() if where.upper() in confluence.SPACES else "", query=queryString)


_CSS = """
:root{--text:#172B4D;--sub:#626F86;--line:#DCDFE4;--bg:#fff;--side:#F7F8F9;--hover:#F1F2F4;--blue:#0C66E4;--blue-bg:#E9F2FF;--mark:#F8E6A0}
*{box-sizing:border-box}
body{margin:0;font:14px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI","Noto Sans KR","Malgun Gothic",sans-serif;color:var(--text);background:var(--bg)}
a{color:var(--blue);text-decoration:none}a:hover{text-decoration:underline}
.topbar{height:56px;display:flex;align-items:center;gap:16px;padding:0 16px;border-bottom:1px solid var(--line);background:#fff;position:sticky;top:0;z-index:5}
.logo{display:flex;align-items:center;gap:8px;color:var(--text);font-weight:600;font-size:16px}.logo:hover{text-decoration:none}
.topnav{display:flex;gap:4px}.topnav a{color:var(--sub);font-weight:500;padding:6px 10px;border-radius:3px}.topnav a:hover{background:var(--hover);text-decoration:none}
.create{background:var(--blue);color:#fff;font-weight:500;padding:6px 12px;border-radius:3px;cursor:default}
.search{margin-left:auto}.search input{width:220px;height:32px;border:1px solid var(--line);border-radius:3px;padding:0 10px;font:inherit;background:var(--side)}
.avatar{display:inline-grid;place-items:center;width:28px;height:28px;border-radius:50%;background:#1D7AFC;color:#fff;font-size:12px;font-weight:600;flex:none}
.mockbar{background:#FFF7D6;color:#7F5F01;font-size:12px;padding:6px 16px;border-bottom:1px solid #F5CD47}
.shell{display:flex;min-height:calc(100vh - 86px)}
.sidebar{width:260px;flex:none;background:var(--side);border-right:1px solid var(--line);padding:16px 8px}
.space-head{display:flex;gap:10px;align-items:center;padding:8px;color:var(--text);border-radius:3px}.space-head:hover{background:var(--hover);text-decoration:none}
.space-head small{display:block;color:var(--sub);font-size:12px}
.space-icon{display:inline-grid;place-items:center;width:32px;height:32px;border-radius:4px;color:#fff;font-weight:700;background:hsl(var(--hue) 55% 45%);flex:none}
.space-icon.sm{width:20px;height:20px;font-size:11px;margin-right:8px}.space-icon.lg{width:40px;height:40px;font-size:18px}.space-icon.xl{width:56px;height:56px;font-size:24px}
.side-links a{display:flex;align-items:center;padding:6px 8px;color:var(--text);border-radius:3px}.side-links a:hover{background:var(--hover);text-decoration:none}
.side-title{margin:16px 8px 4px;font-size:11px;font-weight:700;color:var(--sub);text-transform:uppercase;letter-spacing:.04em}
.tree ul{list-style:none;margin:0;padding-left:12px}.tree>ul{padding-left:0}
.tree summary{cursor:pointer;padding:4px 8px;color:var(--sub);border-radius:3px}.tree summary:hover{background:var(--hover)}
.tree a{display:block;padding:4px 8px;color:var(--text);border-radius:3px;font-size:13px}.tree a:hover{background:var(--hover);text-decoration:none}
.tree a.current{background:var(--blue-bg);color:var(--blue);font-weight:600}
.main{flex:1;min-width:0;max-width:860px;padding:24px 40px 64px}
.crumbs{color:var(--sub);font-size:13px}.crumbs i{font-style:normal;margin:0 4px;color:#B3B9C4}.crumbs span{color:var(--sub)}
.page-title{font-size:28px;font-weight:600;margin:8px 0 12px;letter-spacing:-.01em}
.byline{display:flex;gap:8px;align-items:center;color:var(--sub);font-size:13px;margin-bottom:24px}
.wiki-content h2{font-size:20px;margin:28px 0 8px;font-weight:600}.wiki-content h3{font-size:16px;margin:20px 0 6px}
.wiki-content table{border-collapse:collapse;margin:12px 0;width:100%}
.wiki-content th,.wiki-content td{border:1px solid var(--line);padding:7px 10px;text-align:left;vertical-align:top}
.wiki-content th{background:var(--side);font-weight:600}
.wiki-content code{background:var(--hover);padding:1px 4px;border-radius:3px;font-size:13px}
.wiki-content blockquote{margin:12px 0;padding:8px 14px;border-left:3px solid var(--blue);background:var(--blue-bg)}
.labels{display:flex;flex-wrap:wrap;gap:6px;align-items:center;margin-top:32px;padding-top:16px;border-top:1px solid var(--line)}
.label{background:var(--hover);color:var(--text);padding:1px 8px;border-radius:3px;font-size:12px}
.comments{margin-top:32px}.comments h3{font-size:16px}.comment-box{display:flex;gap:10px;align-items:center;border:1px solid var(--line);border-radius:3px;padding:10px;color:var(--sub)}
.muted{color:var(--sub)}
.section{font-size:16px;margin:24px 0 10px}
.space-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:12px}
.space-card{display:flex;flex-direction:column;gap:8px;padding:16px;border:1px solid var(--line);border-radius:4px;color:var(--text)}
.space-card:hover{box-shadow:0 2px 6px rgba(9,30,66,.15);text-decoration:none}.space-card small{color:var(--sub)}
.space-hero{display:flex;gap:16px;align-items:center}
.card-list{list-style:none;margin:0;padding:0;border:1px solid var(--line);border-radius:4px}
.card-row+.card-row{border-top:1px solid var(--line)}
.card-row a{display:flex;gap:12px;padding:10px 14px;color:var(--text)}.card-row a:hover{background:var(--hover);text-decoration:none}
.card-row small{display:block;color:var(--sub);font-size:12px}
.doc-icon{width:16px;height:20px;margin-top:3px;border-radius:2px;background:#1D7AFC;flex:none;position:relative}
.doc-icon::after{content:"";position:absolute;left:3px;right:3px;top:6px;height:7px;border-top:2px solid #fff;border-bottom:2px solid #fff}
.search-big{display:flex;gap:8px;margin-bottom:12px}
.search-big input{flex:1;height:36px;border:2px solid var(--blue);border-radius:3px;padding:0 10px;font:inherit}
.search-big select{height:36px;border:1px solid var(--line);border-radius:3px;font:inherit}
.search-big button{height:36px;padding:0 16px;border:0;border-radius:3px;background:var(--blue);color:#fff;font:inherit;font-weight:500;cursor:pointer}
.results{list-style:none;padding:0}.result{padding:14px 0;border-bottom:1px solid var(--line)}
.result-title{font-size:16px;font-weight:600}.result-path{color:#216E4E;font-size:12px}.result p{margin:4px 0}
mark{background:var(--mark);color:inherit;padding:0 1px}
@media (max-width:760px){.sidebar,.topnav,.create{display:none}.main{padding:16px}.search input{width:140px}}
"""
