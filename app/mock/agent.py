"""가상 AI 에이전트: Claude·LangGraph 없이 정해진 시나리오(scenario.py)로 실제 에이전트와 같은 이벤트를 흘려보낸다.

HandoverAgent 와 같은 메서드(start · send_message · skip · finish · notify_files · retry · snapshot …)를 제공하므로
BackEnd 의 라우터·작업 실행기·DB 기록은 실제와 똑같이 동작한다. 단계마다 delay 초씩 쉬어 진행률 표시를 흉내 낸다.
대화 상태는 메모리에만 둔다(가상 응답 모드는 서버를 켤 때마다 새로 시작한다).
"""

from __future__ import annotations

import copy
import threading
import time
from collections.abc import Callable
from datetime import date
from pathlib import PurePosixPath
from typing import Any

from amigo_agent import AgentConfig, AgentStateError, QAScope, answer_from_materials
from amigo_agent.dept_info import clean_dept_info, has_info, pick_questions
from amigo_agent.edits import apply_gantt_edit
from amigo_agent.render import render_document
from amigo_agent.state import evaluate_coverage, item_label
from amigo_agent.template import COVERAGE_LABELS, SLOT_BY_KEY, STAGE_LABEL, STAGE_NUMBER, slots_for

from . import scenario

EventHandler = Callable[[dict[str, Any]], None] | None

QUICK_REPLIES_QNA = ["모르겠어요 (건너뛰기)", "지금 문서 생성"]
QUICK_REPLIES_CONFIRM = ["네, 맞아요", "수정할게요"]
YES = ("네", "예", "맞아요", "맞습니다", "응", "ok", "okay", "좋아요")
SKIP = ("모르겠어요", "건너뛰기", "건너뛸게요", "해당 없음", "없어요")
FINISH = ("지금 문서 생성", "문서 생성", "문서 만들어", "인수인계서 생성")
QUICK_REPLIES_YESNO = ["예", "아니요"]
PERIOD_CHOICES = ("매일", "매주", "매월", "매년")
AUX_NOTE = "해당 사항이 없으실 경우 '이 질문 건너뛰기'를 클릭하여 넘어가세요."
NO = ("아니요", "아니오", "아뇨", "아니", "없어요", "없습니다", "없음")


def _is_no(text: str) -> bool:
    return text.strip().rstrip(".!").lower() in NO


def _question_meta(gap: dict) -> dict[str, Any]:
    """질문 메시지의 meta: 예/아니요(choice) · 주기·기간 선택(picker) · 보조질문(aux) 표시는 실제 에이전트(nodes.ask)와 같다."""
    meta: dict[str, Any] = {"quick_replies": QUICK_REPLIES_QNA, "allow_files": True}
    if gap.get("gate"):
        meta.update(quick_replies=QUICK_REPLIES_YESNO, allow_files=False, choice="yesno")
    if gap.get("picker") == "period":
        meta.update(picker="period", choices=list(PERIOD_CHOICES), allow_files=False)
    if gap.get("aux"):
        meta.update(aux=True, aux_note=AUX_NOTE)
    return meta


class MockAgent:
    def __init__(self, delay: float = 0.4) -> None:
        self.delay = delay
        self._states: dict[str, dict[str, Any]] = {}
        self._locks: dict[str, threading.Lock] = {}
        self._guard = threading.Lock()

    # ------------------------------------------------------------------ 정보
    def describe(self) -> dict[str, Any]:
        return {"engine": "mock", "model": None}

    def usage(self) -> None:
        return None

    def answer_question(self, question: str, history: list[dict[str, str]], scopes: list[QAScope]) -> dict[str, Any]:
        """가상 응답 모드: Claude 없이 질문과 가까운 근거를 자료에서 그대로 보여 준다."""
        return answer_from_materials(None, AgentConfig(), question, history, scopes)

    # ------------------------------------------------------------------ 실행
    def start(self, thread_id: str, profile: dict[str, Any], kb: Any, on_event: EventHandler = None) -> None:
        with self._lock(thread_id):
            emit = _Emitter(on_event)
            sources = [{"source_id": s["source_id"], "name": s["name"], "type": s["type"]} for s in getattr(kb, "sources", [])]
            st = {
                "profile": dict(profile or {}), "stage": "analyzing", "slots": {}, "gaps": [], "sources": sources,
                "question_count": 0, "current_gap_id": "", "pending": {}, "document": "", "document_version": 0, "waiting": False,
            }
            self._states[thread_id] = st
            active = slots_for(st["profile"])
            emit.stage("analyzing")
            emit.progress(f"자료 {len(sources)}건을 분석하고 있어요.", current=0, total=len(active))
            for spec in active:
                emit.progress(f"'{spec.title}' 분석 중", slot=spec.key, status="running")
                self._sleep()
                slot = self._build_slot(spec.key, st, kb)
                st["slots"][spec.key] = slot
                emit.slot(spec.key, slot)
                emit.progress(f"'{spec.title}' 분석 완료 · {COVERAGE_LABELS[slot['coverage']]}", slot=spec.key, status="done")
            keys = {s.key for s in active}
            st["gaps"], st["gap_meta"] = [], {}
            for i, g in enumerate(self._planned_gaps(st["profile"])):
                if g["slot"] not in keys:
                    continue
                gap_id = f"gap-{i + 1:02d}"
                st["gap_meta"][gap_id] = g
                st["gaps"].append({"id": gap_id, "slot": g["slot"], "item_id": self._item_id(st, g), "field": g["field"], "description": g["description"],
                                   "question": g["question"], "priority": g["priority"], "rank": i + 1, "status": "open",
                                   **{k: g[k] for k in ("gate", "follow", "aux", "picker") if g.get(k)}})
            emit.progress("자료 분석을 마쳤어요.", current=len(active), total=len(active))
            emit.stage("summary")
            self._sleep()
            emit.say("summary", self._summary(st), coverage={k: v["coverage"] for k, v in st["slots"].items()})
            self._next(st, emit)

    def send_message(self, thread_id: str, text: str, kb: Any, on_event: EventHandler = None) -> None:
        text = (text or "").strip()
        if not text:
            raise AgentStateError("빈 메시지는 보낼 수 없습니다.")
        with self._lock(thread_id):
            st, emit = self._ready(thread_id), _Emitter(on_event)
            st["waiting"] = False
            self._sleep()
            if any(p in text for p in FINISH):
                return self._finish(st, emit)
            if st["stage"] == "review":
                return self._revise(st, emit, text)
            if st["pending"]:
                if _is_yes(text):
                    return self._resolve(st, emit)
                return self._answer(st, emit, text, correcting=True)
            gap = self._current_gap(st)
            if gap is not None and gap.get("gate") and (_is_yes(text) or _is_no(text)):
                return self._answer_gate(st, emit, gap, _is_yes(text))
            if any(p in text for p in SKIP):
                return self._skip(st, emit)
            if gap is not None and gap.get("picker") == "period" and (text in PERIOD_CHOICES or "~" in text):
                return self._answer_period(st, emit, gap, text)
            return self._answer(st, emit, text)

    def skip(self, thread_id: str, kb: Any, on_event: EventHandler = None) -> None:
        with self._lock(thread_id):
            st, emit = self._ready(thread_id), _Emitter(on_event)
            st["waiting"] = False
            self._sleep()
            self._skip(st, emit)

    def reopen_question(self, thread_id: str, gap_id: str, kb: Any, on_event: EventHandler = None) -> None:
        """이미 답했거나 건너뛴 질문으로 돌아가 다시 묻는다(질의응답에서만). 새 답은 앞의 답을 덮어쓴다."""
        with self._lock(thread_id):
            st, emit = self._ready(thread_id), _Emitter(on_event)
            if st["stage"] not in ("qna", "review"):
                raise AgentStateError("질의응답이나 문서 검토 중에만 질문으로 이동할 수 있습니다.")
            target = next((g for g in st["gaps"] if g["id"] == gap_id), None)
            if target is None:
                raise AgentStateError("돌아갈 질문을 찾지 못했습니다.")
            if target["status"] not in ("answered", "resolved", "skipped", "open") + (("asked",) if st["stage"] == "review" else ()):
                raise AgentStateError("지금 묻고 있는 질문입니다.")
            st["resume_review"] = gap_id if st["stage"] == "review" else ""  # 검토 중에 골랐으면 답한 뒤 문서를 다시 만든다
            st["waiting"] = False
            self._sleep()
            for g in st["gaps"]:
                if g["status"] == "asked":
                    g["status"] = "open"  # 지금 묻던 질문은 이 질문 다음에 다시 묻는다
            if target.get("first"):  # 예/아니요로 시작한 질문은 처음 질문부터 다시 묻는다
                target.update(target.pop("first"))
            target["status"] = "open"
            st["force_gap"] = gap_id  # 고른 질문을 먼저 묻는다(목록 순서·번호는 그대로)
            st["pending"] = {}
            st["question_count"] = sum(1 for g in st["gaps"] if g["status"] in ("answered", "resolved", "skipped"))  # _next 가 +1 한다
            self._next(st, emit, ack="고르신 질문으로 이동했어요. 답해 주시면 그 내용으로 고칠게요.")

    def finish(self, thread_id: str, kb: Any, on_event: EventHandler = None) -> None:
        with self._lock(thread_id):
            st, emit = self._ready(thread_id), _Emitter(on_event)
            st["waiting"] = False
            self._finish(st, emit)

    def notify_files(self, thread_id: str, kb: Any, names: list[str], on_event: EventHandler = None) -> None:
        with self._lock(thread_id):
            st, emit = self._ready(thread_id), _Emitter(on_event)
            st["waiting"] = False
            emit.progress(f"새 자료 {len(names)}건을 분석에 반영하고 있어요.", status="running")
            self._sleep()
            st["sources"] += [{"name": n, "type": PurePosixPath(n.lower()).suffix.lstrip(".")} for n in names]
            joined = ", ".join(names)
            if st["stage"] == "review":
                emit.say("info", f"새 자료({joined})를 반영했어요. 문서를 다시 작성할게요.")
                return self._compose(st, emit)
            emit.say("info", f"새 자료({joined})를 반영했어요. 이어서 확인할게요.", quick_replies=QUICK_REPLIES_QNA)
            self._repeat_current(st, emit)

    def retry(self, thread_id: str, kb: Any, on_event: EventHandler = None) -> None:
        with self._lock(thread_id):
            st = self._states.get(thread_id)
            if not st:
                raise AgentStateError("다시 시도할 실행 기록이 없습니다.")
            if st["waiting"]:
                return
            emit = _Emitter(on_event)
            if st["stage"] in ("composing", "review"):
                return self._compose(st, emit)
            self._next(st, emit)

    def edit_gantt(self, thread_id: str, name: str, nth: int, months: list[int]) -> dict[str, Any]:
        with self._lock(thread_id):
            st = self._ready(thread_id)
            if not st["document"]:
                raise AgentStateError("아직 생성된 인수인계서가 없습니다.")
            if apply_gantt_edit(st["slots"], name, nth, months) is None:
                raise AgentStateError("고칠 일정 행을 찾지 못했습니다.")
            st["document"] = render_document(profile=st["profile"], slots=st["slots"], gaps=st["gaps"], sources=st["sources"], version=st["document_version"])
            return {"markdown": st["document"], "version": st["document_version"]}

    def rerender(self, thread_id: str, patch: dict[str, Any]) -> dict[str, Any]:
        """인계자 정보를 patch 로 갱신하고 같은 버전으로 문서를 다시 그린다(부서·팀별 추가자료 등록)."""
        self.update_profile(thread_id, patch)
        with self._lock(thread_id):
            st = self._ready(thread_id)
            if not st["document"]:
                raise AgentStateError("아직 생성된 인수인계서가 없습니다.")
            st["document"] = render_document(profile=st["profile"], slots=st["slots"], gaps=st["gaps"], sources=st["sources"], version=st["document_version"])
            return {"markdown": st["document"], "version": st["document_version"]}

    def update_profile(self, thread_id: str, patch: dict[str, Any]) -> bool:
        with self._lock(thread_id):
            st = self._states.get(thread_id)
            if not st:
                return False
            changed = False
            for key, value in patch.items():
                if value is None:
                    changed |= st["profile"].pop(key, None) is not None
                elif st["profile"].get(key) != value:
                    st["profile"][key], changed = value, True
            return changed

    def delete(self, thread_id: str) -> None:
        with self._guard:
            self._states.pop(thread_id, None)

    # ------------------------------------------------------------------ 조회
    def is_waiting(self, thread_id: str) -> bool:
        st = self._states.get(thread_id)
        return bool(st and st["waiting"])

    def snapshot(self, thread_id: str) -> dict[str, Any]:
        st = self._states.get(thread_id) or {}
        gaps = st.get("gaps", [])
        return {
            "exists": bool(st),
            "stage": st.get("stage", ""),
            "waiting": bool(st.get("waiting")),
            "next": ["wait_user"] if st.get("waiting") else [],
            "slots": copy.deepcopy(st.get("slots", {})),
            "gaps": copy.deepcopy(gaps),
            "open_gap_count": sum(1 for g in gaps if g["status"] == "open"),
            "current_gap_id": st.get("current_gap_id", ""),
            "pending": dict(st.get("pending", {})),
            "question_count": st.get("question_count", 0),
            "document": st.get("document", ""),
            "document_version": st.get("document_version", 0),
            "sources": list(st.get("sources", [])),
            "engine": "mock",
        }

    # ------------------------------------------------------------------ 시나리오 진행
    def _next(self, st: dict, emit: _Emitter, ack: str = "") -> None:
        open_gaps = [g for g in st["gaps"] if g["status"] == "open"]
        resume = st.get("resume_review")
        if resume and not any(g["id"] == resume and g["status"] == "open" for g in st["gaps"]):
            st["resume_review"] = ""  # 검토 중 고른 질문에 답했으면 바로 문서를 다시 만든다
            open_gaps = []
        if open_gaps:  # 질문 수는 제한하지 않는다(남은 질문을 모두 묻는다)
            gap = next((g for g in open_gaps if g["id"] == st.pop("force_gap", None)), open_gaps[0])
            gap["status"] = "asked"
            st["current_gap_id"] = gap["id"]
            st["question_count"] += 1
            if st["stage"] != "qna":
                st["stage"] = "qna"
                emit.stage("qna")
            content = (ack + "\n\n" if ack else "") + gap["question"]  # 장 이름은 meta.slot 으로 화면 태그에 표시되므로 본문에 붙이지 않는다
            emit.say("question", content, gap_id=gap["id"], slot=gap["slot"], number=st["question_count"],
                     remaining=len(open_gaps) - 1, **_question_meta(gap))
            st["waiting"] = True
            return
        text = "질문을 모두 마쳤어요. 건너뛴 내용은 화면 오른쪽 '확인 필요 사항'에 남겨 두고 인수인계서를 작성할게요."
        emit.say("info", (ack + "\n\n" if ack else "") + text)
        self._compose(st, emit)

    def _repeat_current(self, st: dict, emit: _Emitter) -> None:
        gap = self._current_gap(st)
        if gap is None or gap["status"] != "asked":
            return self._next(st, emit)
        emit.say("question", gap["question"], gap_id=gap["id"], slot=gap["slot"],
                 number=st["question_count"], remaining=sum(1 for g in st["gaps"] if g["status"] == "open"),
                 **_question_meta(gap))
        st["waiting"] = True

    def _answer(self, st: dict, emit: _Emitter, text: str, correcting: bool = False) -> None:
        gap = self._current_gap(st)
        if gap is None:
            emit.say("info", "말씀 감사합니다. 지금까지 내용으로 문서를 만들려면 '지금 문서 생성'을 눌러 주세요.", quick_replies=QUICK_REPLIES_QNA)
            st["waiting"] = True
            return
        spec = SLOT_BY_KEY[gap["slot"]]
        field = st["gap_meta"][gap["id"]]["field"]
        item = self._ensure_item(st, gap)
        item["fields"][field] = text
        item["origin"] = "user"
        citation = {"source_id": "chat", "label": f"인계자 답변 ({date.today().isoformat()} 대화)", "quote": text[:80]}
        item["citations"] = [c for c in item.get("citations", []) if c.get("source_id") != "chat"] + [citation]
        self._recount(st, gap["slot"])
        emit.slot(gap["slot"], st["slots"][gap["slot"]])
        gap["status"] = "answered"
        st["pending"] = {"gap_id": gap["id"], "slot": gap["slot"], "item_id": item["id"]}
        lead = "수정한 내용으로 다시 정리했어요." if correcting else "다음과 같이 정리했어요."
        emit.say("confirm", f"{lead}\n\n**[{spec.title}] {item_label(spec, item)}**\n- **{spec.label(field)}**: {text}\n\n맞으면 '네', 고칠 부분이 있으면 말씀해 주세요.",
                 quick_replies=QUICK_REPLIES_CONFIRM, refs=[[gap["slot"], item["id"]]])
        st["waiting"] = True

    def _answer_gate(self, st: dict, emit: _Emitter, gap: dict, yes: bool) -> None:
        """예/아니요 질문: ask 는 예면 이어지는 주관식 질문, 아니요면 '해당 없음' / confirm 은 예면 확인 끝, 아니요면 주관식 질문."""
        follow = (gap["gate"] == "ask") == yes
        gap.setdefault("first", {"question": gap["question"], "gate": gap["gate"], "picker": ""})
        if follow:
            gap.update(gate="", question=gap.get("follow") or gap["question"], status="open")
            st["force_gap"] = gap["id"]
            st["question_count"] -= 1
            return self._next(st, emit)
        ack = "확인했어요."
        if gap["gate"] == "ask" and gap["slot"] not in ("partners", "stakeholders"):
            item = self._ensure_item(st, gap)
            item["fields"][st["gap_meta"][gap["id"]]["field"]] = "해당 없음"
            self._recount(st, gap["slot"])
            emit.slot(gap["slot"], st["slots"][gap["slot"]])
            ack = f"'{SLOT_BY_KEY[gap['slot']].label(st['gap_meta'][gap['id']]['field'])}'은(는) '해당 없음'으로 기록했어요."
        gap["status"] = "resolved"
        self._next(st, emit, ack=ack)

    def _answer_period(self, st: dict, emit: _Emitter, gap: dict, text: str) -> None:
        """수행 시기 선택: 주기(매일·매주·매월·매년) 또는 달력에서 고른 기간('2026-03-02 ~ 2026-05-29')을 바로 기록한다."""
        item = self._ensure_item(st, gap)
        item["fields"]["timing"] = f"수행 주기: {text} 수행함." if text in PERIOD_CHOICES else f"비정기 수행(기간: {text.replace('-', '.')})함."
        item["origin"] = "user"
        self._recount(st, gap["slot"])
        emit.slot(gap["slot"], st["slots"][gap["slot"]])
        gap["status"] = "resolved"
        self._next(st, emit, ack=f"수행 시기를 '{text}'로 기록했어요.")

    def _resolve(self, st: dict, emit: _Emitter) -> None:
        gap = self._current_gap(st)
        if gap is not None:
            gap["status"] = "resolved"
        item = self._find_item(st, st["pending"].get("slot", ""), st["pending"].get("item_id", ""))
        if item is not None:
            item["confirmed"] = True
        st["pending"] = {}
        self._next(st, emit)  # 직전 확인 메시지에서 이미 정리했으므로 다음 질문 앞에 문구를 붙이지 않는다

    def _skip(self, st: dict, emit: _Emitter) -> None:
        gap = self._current_gap(st)
        if gap is not None and gap["status"] in ("asked", "answered", "open"):
            gap["status"] = "skipped"
        st["pending"] = {}
        self._next(st, emit, ack="이 질문은 건너뛸게요. 화면 오른쪽 '확인 필요 사항'에 남겨 둘게요.")

    def _finish(self, st: dict, emit: _Emitter) -> None:
        emit.say("info", "지금까지 정리한 내용으로 인수인계서를 작성할게요.")
        self._compose(st, emit)

    def _revise(self, st: dict, emit: _Emitter, text: str) -> None:
        emit.say("info", "말씀하신 내용을 반영해서 문서를 다시 작성할게요.")
        if "dept_notes" in {s.key for s in slots_for(st["profile"])}:
            slot = st["slots"].setdefault("dept_notes", {"key": "dept_notes", "items": [], "coverage": "missing", "summary": "", "note": ""})
            slot["items"].append({"id": f"dept_notes-r{st['document_version']}", "title": "검토 의견", "origin": "user", "confirmed": True,
                                  "fields": {"topic": "검토 의견", "detail": text},
                                  "citations": [{"source_id": "chat", "label": f"인계자 답변 ({date.today().isoformat()} 대화)", "quote": text[:80]}]})
            self._recount(st, "dept_notes")
            emit.slot("dept_notes", slot)
        self._compose(st, emit)

    def _compose(self, st: dict, emit: _Emitter) -> None:
        st["stage"] = "composing"
        emit.stage("composing")
        emit.progress("인수인계서를 작성하고 있어요.", status="running")
        self._sleep(2)
        version = st["document_version"] + 1
        markdown = render_document(profile=st["profile"], slots=st["slots"], gaps=st["gaps"], sources=st["sources"], version=version)
        st.update(document=markdown, document_version=version, stage="review", pending={})
        emit.document(markdown, version)
        emit.stage("review")
        emit.say("document", f"인수인계서 초안(v{version})을 작성했어요. 문서 탭에서 확인해 주세요.\n고칠 내용이나 빠진 내용을 채팅으로 알려 주시면 반영해서 다시 작성할게요.", version=version)
        st["waiting"] = True

    # ------------------------------------------------------------------ 데이터 만들기
    def _build_slot(self, key: str, st: dict, kb: Any = None) -> dict[str, Any]:
        task = st["profile"].get("task_name") or st["profile"].get("duties") or "담당 업무"
        raws = scenario.ITEMS.get(key, [])
        # 인계자 정보에서 단위업무를 골랐으면 업무 단위 요약의 업무명을 그 단위업무로 쓴다(기간·진행상황은 시나리오 값을 돌려 쓴다)
        unit_names = [u["name"] for u in st["profile"].get("units") or [] if u.get("name")]
        if key == "overview" and unit_names and raws:
            raws = [{**raws[i % len(raws)], "fields": {**raws[i % len(raws)]["fields"], "name": name}} for i, name in enumerate(unit_names)]
        items = []
        for i, raw in enumerate(raws):
            fields = {k: v.replace("{task_name}", task) for k, v in raw["fields"].items()}
            item = {"id": f"{key}-{i + 1:02d}", "title": fields.get(SLOT_BY_KEY[key].title_field, ""), "fields": fields,
                    "citations": [self._citation(st, raw["doc"], fields, i, kb)], "origin": "document", "confirmed": False}
            items.append(item)
        slot = {"key": key, "items": items, "coverage": "missing", "summary": "", "note": ""}
        st["slots"][key] = slot
        self._recount(st, key)
        return slot

    def _citation(self, st: dict, doc: str, fields: dict[str, str], index: int, kb: Any = None) -> dict[str, str]:
        """시나리오의 자료 종류에 맞는 업로드 파일을 골라 근거로 단다(가짜 지식베이스에 조각도 넣어 원문 조회가 되게)."""
        exts, locator = scenario.DOC_HINTS.get(doc, ((), ""))
        sources = st["sources"] or [{"source_id": "", "name": "등록 자료"}]
        source = next((s for s in sources if PurePosixPath(s["name"].lower()).suffix in exts), sources[index % len(sources)])
        if PurePosixPath(source["name"].lower()).suffix not in exts:
            locator = ""
        quote = next((v for v in fields.values() if v and len(v) > 8), "")
        chunk_id = ""
        if kb is not None and hasattr(kb, "add_chunk") and source.get("source_id"):
            chunk_id = kb.add_chunk(source["source_id"], f"(가상 응답 · {source['name']}) … {quote} …")
        return {"source_id": source.get("source_id", ""), "label": f"{source['name']} {locator}".strip(), "chunk_id": chunk_id, "quote": quote[:80]}

    def _recount(self, st: dict, key: str) -> None:
        spec, slot = SLOT_BY_KEY[key], st["slots"][key]
        slot["coverage"] = evaluate_coverage(spec, slot["items"])
        labels = [item_label(spec, it) for it in slot["items"]]
        slot["summary"] = ", ".join(labels[:3]) + (f" 외 {len(labels) - 3}건" if len(labels) > 3 else "")

    def _summary(self, st: dict) -> str:
        active = slots_for(st["profile"])
        good = [s.title for s in active if st["slots"][s.key]["coverage"] == "sufficient"]
        weak = [s.title for s in active if st["slots"][s.key]["coverage"] != "sufficient"]
        head = f"올려 주신 자료 {len(st['sources'])}건을 분석했어요."
        if good:
            head += f" {', '.join(good)} 항목은 자료만으로 충분히 파악했습니다."
        if weak:
            head += f" {', '.join(weak)} 항목은 보완이 필요해요."
        lines = []
        for spec in active:
            slot = st["slots"][spec.key]
            icon = {"sufficient": "🟢", "partial": "🟡", "missing": "🔴"}[slot["coverage"]]
            detail = f" — {slot['summary']}" if slot["summary"] else ""
            lines.append(f"- {icon} **{spec.title}** · {COVERAGE_LABELS[slot['coverage']]} ({len(slot['items'])}건){detail}")
        remaining = sum(1 for g in st["gaps"] if g["status"] == "open")
        closing = (f"부족한 부분을 채우기 위해 **한 번에 하나씩** 질문드릴게요(모두 {remaining}개). "
                   "모르는 내용은 '건너뛰기', 관련 파일이 있으면 언제든 첨부해 주세요.") if remaining else "자료만으로 필요한 정보가 충족되었어요. 바로 인수인계서를 작성할게요."
        return f"{head}\n\n" + "\n".join(lines) + f"\n\n{closing}"

    @staticmethod
    def _planned_gaps(profile: dict[str, Any]) -> list[dict[str, Any]]:
        """시나리오 질문 목록. 소속 부·팀의 인수인계 목록이 있으면 '부서별 추가 자료' 일반 질문 대신 목록의 서류·물품 관리 방식과 비고 사항을 묻는다(최대 3개)."""
        info = clean_dept_info(profile.get("dept_notes"))
        if not has_info(info):
            return list(scenario.GAPS)
        kept = [g for g in scenario.GAPS if g["slot"] != "dept_notes"]
        for target in pick_questions(info, 3):
            kept.append({"slot": "dept_notes", "item": None, "field": "detail", "priority": 2, "new_title": target["name"],
                         "description": f"{target['label']} '{target['name']}'의 관리 내용이 자료에서 확인되지 않음", "question": target["question"]})
        return kept

    # ------------------------------------------------------------------ 도우미
    def _item_id(self, st: dict, gap: dict) -> str:
        item = next((it for it in st["slots"].get(gap["slot"], {}).get("items", []) if it["title"] == gap["item"]), None)
        return item["id"] if item else ""

    def _ensure_item(self, st: dict, gap: dict) -> dict[str, Any]:
        slot = st["slots"][gap["slot"]]
        item = self._find_item(st, gap["slot"], gap["item_id"])
        if item is None:
            spec = SLOT_BY_KEY[gap["slot"]]
            title = st["gap_meta"][gap["id"]].get("new_title") or "인계자 답변"
            item = {"id": f"{gap['slot']}-{len(slot['items']) + 1:02d}", "title": title, "fields": {spec.title_field: title},
                    "citations": [], "origin": "user", "confirmed": False}
            slot["items"].append(item)
            gap["item_id"] = item["id"]
        return item

    def _find_item(self, st: dict, slot_key: str, item_id: str) -> dict | None:
        return next((it for it in st["slots"].get(slot_key, {}).get("items", []) if it["id"] == item_id), None) if item_id else None

    def _current_gap(self, st: dict) -> dict | None:
        return next((g for g in st["gaps"] if g["id"] == st["current_gap_id"]), None)

    def _ready(self, thread_id: str) -> dict[str, Any]:
        st = self._states.get(thread_id)
        if not st:
            raise AgentStateError("아직 분석을 시작하지 않았습니다. 자료를 올리고 분석을 먼저 시작해 주세요.")
        if not st["waiting"]:
            raise AgentStateError("이전 작업이 끝나지 않았습니다. 잠시 후 다시 시도하거나 '다시 시도'를 눌러 주세요.")
        return st

    def _sleep(self, steps: float = 1) -> None:
        if self.delay > 0:
            time.sleep(self.delay * steps)

    def _lock(self, thread_id: str) -> threading.Lock:
        with self._guard:
            return self._locks.setdefault(thread_id, threading.Lock())


def _is_yes(text: str) -> bool:
    t = text.strip().lower()
    return any(t == y or t[len(y):len(y) + 1] in (",", " ", ".", "!", "~") and t.startswith(y) for y in YES)


class _Emitter:
    """실제 에이전트와 같은 모양의 이벤트(stage · progress · slot · message · document)를 만든다."""

    def __init__(self, handler: EventHandler) -> None:
        self.handler = handler

    def _emit(self, event: dict[str, Any]) -> None:
        if self.handler is not None:
            self.handler(event)

    def stage(self, stage: str) -> None:
        self._emit({"type": "stage", "stage": stage, "number": STAGE_NUMBER.get(stage, 0), "label": STAGE_LABEL.get(stage, stage)})

    def progress(self, message: str, **extra: Any) -> None:
        self._emit({"type": "progress", "message": message, **extra})

    def slot(self, key: str, slot: dict[str, Any]) -> None:
        self._emit({"type": "slot", "key": key, "slot": copy.deepcopy(slot)})

    def say(self, kind: str, content: str, **meta: Any) -> None:
        self._emit({"type": "message", "role": "assistant", "kind": kind, "content": content, "meta": meta})

    def document(self, markdown: str, version: int) -> None:
        self._emit({"type": "document", "markdown": markdown, "version": version})
