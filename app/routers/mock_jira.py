"""가상 Jira 사이트: app/mock/jira.py 의 예시 이슈·프로젝트를 Jira 처럼 생긴 HTML 화면으로 보여 준다.

인수인계 자료로 가져온 지라 항목의 링크(url)가 실제로 열리도록 Jira 와 같은 주소 모양을 쓴다.
  /jira                       프로젝트 목록
  /jira/projects/{key}        프로젝트 진행 현황(스프린트·에픽 진행률·보드)
  /jira/browse/{issue key}    이슈 보기
"""

from __future__ import annotations

import html

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse

from ..mock import jira

router = APIRouter(prefix="/jira", include_in_schema=False)

_CSS = """
*{box-sizing:border-box}body{margin:0;font:14px/1.55 -apple-system,'Segoe UI','Malgun Gothic',sans-serif;color:#172b4d;background:#f7f8f9}
a{color:#0c66e4;text-decoration:none}a:hover{text-decoration:underline}
.top{height:52px;display:flex;align-items:center;gap:10px;padding:0 20px;background:#fff;border-bottom:1px solid #dcdfe4}
.top b{font-size:16px}.mock{margin-left:auto;padding:3px 9px;border-radius:4px;background:#fff7d6;color:#7f5f01;font-size:12px}
main{max-width:1080px;margin:0 auto;padding:22px 20px 40px}h1{margin:4px 0 6px;font-size:24px}h2{margin:26px 0 10px;font-size:16px}
.muted{color:#626f86;font-size:13px}.card{padding:14px 16px;border:1px solid #dcdfe4;border-radius:8px;background:#fff;margin-bottom:10px}
.bar{height:8px;border-radius:4px;background:#dcdfe4;overflow:hidden}.bar i{display:block;height:100%;background:#22a06b}
.lozenge{display:inline-block;padding:0 6px;border-radius:3px;font-size:11px;font-weight:700;line-height:18px}
.todo{background:#dcdfe4;color:#44546f}.progress{background:#cce0ff;color:#0055cc}.done{background:#baf3db;color:#216e4e}
table{width:100%;border-collapse:collapse;background:#fff;border:1px solid #dcdfe4;border-radius:8px}th,td{padding:8px 10px;border-bottom:1px solid #f1f2f4;text-align:left;font-size:13px}
th{background:#f7f8f9;color:#626f86;font-weight:600}.board{display:grid;grid-template-columns:repeat(4,1fr);gap:10px}
.col{padding:8px;border-radius:8px;background:#f1f2f4;min-height:120px}.col h3{margin:2px 4px 8px;font-size:12px;color:#626f86}
.issue{display:block;padding:8px 10px;margin-bottom:6px;border-radius:6px;background:#fff;box-shadow:0 1px 1px rgba(9,30,66,.25);color:#172b4d}
.issue small{display:block;color:#626f86;margin-top:4px}.grid{display:grid;grid-template-columns:minmax(0,1fr) 280px;gap:18px}
dl{margin:0}dt{color:#626f86;font-size:12px;margin-top:10px}dd{margin:2px 0 0}.blocked{color:#ae2e24;font-weight:600}
pre{white-space:pre-wrap;font:inherit;margin:0}
"""


def _e(text) -> str:
    return html.escape(str(text), quote=True)


def _lozenge(status: str) -> str:
    return f'<span class="lozenge {jira.STATUS_CATEGORY.get(status, "todo")}">{_e(status)}</span>'


def _page(title: str, body: str) -> HTMLResponse:
    return HTMLResponse(
        f"""<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{_e(title)} - Jira</title><style>{_CSS}</style></head><body>
<div class="top"><a href="/jira"><b>Jira</b></a><span class="muted">소프트웨어</span><span class="mock">가상 Jira · AMIGO 예시 데이터</span></div>
<main>{body}</main></body></html>"""
    )


def _bar(percent: int) -> str:
    return f'<div class="bar"><i style="width:{int(percent)}%"></i></div>'


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
def projects():
    cards = []
    for p in jira.list_projects():
        sprint = p["sprint"]
        sprint_line = f'<div class="muted">{_e(sprint["name"])} · 남은 날 {sprint["days_left"]}일 · {sprint["percent"]}%</div>' if sprint else ""
        cards.append(
            f'<div class="card"><a href="/jira/projects/{_e(p["key"])}"><b>{_e(p["name"])}</b></a> <span class="muted">({_e(p["key"])}) · {_e(p["team"])}</span>'
            f'{sprint_line}<div class="muted">전체 {p["progress"]["percent"]}% 완료</div>{_bar(p["progress"]["percent"])}</div>'
        )
    return _page("프로젝트", "<h1>프로젝트</h1>" + "".join(cards))


@router.get("/projects/{key}", response_class=HTMLResponse)
def project(key: str):
    found = jira.get_project(key)
    if found is None:
        raise HTTPException(status_code=404, detail="프로젝트를 찾을 수 없습니다.")
    s = jira.project_summary(found)
    body = [f'<div class="muted">프로젝트 · {_e(found.team)}</div><h1>{_e(found.name)} ({_e(found.key)})</h1><p>{_e(found.description)}</p>']
    if s["sprint"]:
        sp = s["sprint"]
        body.append(f'<div class="card"><b>{_e(sp["name"])}</b> <span class="muted">{_e(sp["start"])} ~ {_e(sp["end"])} · 남은 날 {sp["days_left"]}일</span>'
                    f'<div class="muted">목표: {_e(sp["goal"])}</div><div class="muted">스토리 포인트 {sp["done_points"]}/{sp["points"]} ({sp["percent"]}%)</div>{_bar(sp["percent"])}</div>')
        cols = []
        for status in jira.STATUSES:
            items = [i for i in jira.ISSUES if i.project == found.key and i.sprint == sp["name"] and i.status == status]
            cards = "".join(f'<a class="issue" href="/jira/browse/{_e(i.key)}">{_e(i.summary)}<small>{_e(i.key)} · {_e(i.assignee or "담당자 없음")}</small></a>' for i in items)
            cols.append(f'<div class="col"><h3>{_e(status)} {len(items)}</h3>{cards}</div>')
        body.append(f'<h2>보드</h2><div class="board">{"".join(cols)}</div>')
    if s["epics"]:
        rows = "".join(f'<tr><td><a href="/jira/browse/{_e(e["key"])}">{_e(e["key"])}</a></td><td>{_e(e["summary"])}</td><td>{_lozenge(e["status"])}</td><td>{_e(e["assignee"])}</td><td>{e["percent"]}%{_bar(e["percent"])}</td></tr>' for e in s["epics"])
        body.append(f"<h2>에픽</h2><table><tr><th>키</th><th>요약</th><th>상태</th><th>담당자</th><th>진행률</th></tr>{rows}</table>")
    if s["blocked"]:
        body.append("<h2>막힌 이슈</h2>" + "".join(f'<div class="card"><a href="/jira/browse/{_e(b["key"])}">{_e(b["key"])}</a> {_e(b["summary"])} <span class="blocked">{_e(b["reason"])}</span></div>' for b in s["blocked"]))
    return _page(found.name, "".join(body))


@router.get("/browse/{key}", response_class=HTMLResponse)
def browse(key: str):
    issue = jira.get_issue(key)
    if issue is None:
        raise HTTPException(status_code=404, detail="이슈를 찾을 수 없습니다.")
    d = jira.issue_detail(issue)
    project_obj = jira.get_project(issue.project)
    main = [f'<div class="muted"><a href="/jira/projects/{_e(issue.project)}">{_e(project_obj.name if project_obj else issue.project)}</a> / {_e(issue.key)}</div><h1>{_e(issue.summary)}</h1>']
    if issue.description:
        main.append(f'<h2>설명</h2><div class="card"><pre>{_e(issue.description)}</pre></div>')
    if d["children"]:
        p = d["progress"]
        rows = "".join(f'<tr><td><a href="/jira/browse/{_e(c["key"])}">{_e(c["key"])}</a></td><td>{_e(c["summary"])}</td><td>{_lozenge(c["status"])}</td><td>{_e(c["assignee"])}</td></tr>' for c in d["children"])
        main.append(f'<h2>하위 이슈 · {p["percent"]}% 완료</h2>{_bar(p["percent"])}<br><table><tr><th>키</th><th>요약</th><th>상태</th><th>담당자</th></tr>{rows}</table>')
    if d["subtasks"]:
        main.append("<h2>하위 작업</h2>" + "".join(f'<div class="card"><a href="/jira/browse/{_e(c["key"])}">{_e(c["key"])}</a> {_e(c["summary"])} {_lozenge(c["status"])}</div>' for c in d["subtasks"]))
    if d["comments"]:
        main.append("<h2>댓글</h2>" + "".join(f'<div class="card"><b>{_e(c["author"])}</b> <span class="muted">{_e(c["date"])}</span><pre>{_e(c["body"])}</pre></div>' for c in d["comments"]))
    side = [f"<dt>상태</dt><dd>{_lozenge(issue.status)}</dd>", f"<dt>유형</dt><dd>{_e(issue.type)}</dd>", f"<dt>담당자</dt><dd>{_e(issue.assignee or '없음')}</dd>",
            f"<dt>보고자</dt><dd>{_e(issue.reporter or '-')}</dd>", f"<dt>우선순위</dt><dd>{_e(issue.priority)}</dd>"]
    if issue.sprint:
        side.append(f"<dt>스프린트</dt><dd>{_e(issue.sprint)}</dd>")
    if issue.epic:
        side.append(f'<dt>에픽</dt><dd><a href="/jira/browse/{_e(issue.epic)}">{_e(d["epic_summary"] or issue.epic)}</a></dd>')
    if issue.points:
        side.append(f"<dt>스토리 포인트</dt><dd>{issue.points}</dd>")
    if issue.due:
        side.append(f"<dt>기한</dt><dd>{_e(issue.due)}</dd>")
    if issue.blocked and issue.category != "done":
        side.append(f'<dt>막힘</dt><dd class="blocked">{_e(issue.blocked)}</dd>')
    side.append(f"<dt>최종 수정</dt><dd>{_e(issue.updated)}</dd>")
    return _page(f"[{issue.key}] {issue.summary}", f'<div class="grid"><div>{"".join(main)}</div><div class="card"><dl>{"".join(side)}</dl></div></div>')
