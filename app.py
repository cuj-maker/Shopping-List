"""쇼핑리스트 앱 (Streamlit).

PRD.md 기반. 3단계 UI 디자인 적용 후 TEST_REPORT.md의 B1(동시 사용 시 데이터 유실)을 수정했다.
"""

import json
import os
import re
import threading
import uuid
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

import streamlit as st

# ---------------------------------------------------------------------------
# 상수
# ---------------------------------------------------------------------------
DATA_FILE = "shopping_list.json"
MAX_NAME_LENGTH = 50

DATA_PATH = Path(__file__).parent / DATA_FILE

# 마크다운에서 특별한 의미를 갖는 문자 (이름을 그대로 보여주기 위해 이스케이프)
_MD_SPECIAL = re.compile(r"([\\`*_{}\[\]()#+\-.!~|<>])")


# ---------------------------------------------------------------------------
# 데이터 모델
# ---------------------------------------------------------------------------
def new_item(name: str) -> dict:
    """PRD 4.1 구조에 맞는 새 아이템 딕셔너리를 만든다.

    Args:
        name: 아이템 이름 (이미 정리·검증된 값).

    Returns:
        {"id", "name", "checked", "created_at"} 키를 가진 딕셔너리.
    """
    return {
        "id": str(uuid.uuid4()),
        "name": name,
        "checked": False,
        "created_at": datetime.now().isoformat(timespec="seconds"),
    }


def validate_name(raw: str) -> tuple[str | None, str | None]:
    """입력한 이름을 정리하고 검증한다.

    Args:
        raw: 사용자가 입력한 원본 문자열.

    Returns:
        (정리된 이름, None) 또는 검증 실패 시 (None, 오류 메시지).
    """
    name = (raw or "").strip()
    if not name:
        return None, "아이템 이름을 입력해 주세요."
    if len(name) > MAX_NAME_LENGTH:
        return None, f"아이템 이름은 {MAX_NAME_LENGTH}자 이하로 입력해 주세요. (현재 {len(name)}자)"
    return name, None


def _is_valid_item(obj: object) -> bool:
    """불러온 데이터가 아이템 구조에 맞는지 확인한다."""
    return (
        isinstance(obj, dict)
        and isinstance(obj.get("id"), str)
        and isinstance(obj.get("name"), str)
        and isinstance(obj.get("checked"), bool)
    )


# ---------------------------------------------------------------------------
# 알림 메시지 (콜백에서 설정 → 다음 화면에서 한 번 표시)
# ---------------------------------------------------------------------------
def flash(kind: str, message: str) -> None:
    """다음 렌더링 때 한 번 보여줄 메시지를 등록한다.

    Args:
        kind: "warning" 또는 "success".
        message: 표시할 문구.
    """
    st.session_state["flash"] = (kind, message)


def show_flash() -> None:
    """등록된 메시지가 있으면 표시하고 지운다."""
    msg = st.session_state.pop("flash", None)
    if not msg:
        return
    kind, text = msg
    if kind == "warning":
        st.warning(text)
    else:
        st.success(text)


# ---------------------------------------------------------------------------
# 저장소
# ---------------------------------------------------------------------------
# 파일이 유일한 원본(source of truth)이다. 탭(세션)마다 사본을 들고 있다가 통째로
# 덮어쓰면 다른 탭의 변경이 사라지므로(TEST_REPORT B1), 모든 변경은
# "잠금 → 파일에서 최신 목록 읽기 → 변경 → 저장" 순서로 처리한다.
@st.cache_resource
def _file_lock() -> threading.Lock:
    """모든 세션이 함께 쓰는 파일 잠금 (스크립트가 다시 실행돼도 같은 객체)."""
    return threading.Lock()


def load_items(report: bool = True) -> list[dict]:
    """DATA_FILE에서 아이템 리스트를 불러온다.

    파일이 없으면 빈 리스트를 반환한다.
    JSON이 깨졌으면 빈 리스트를 반환하고 경고를 표시한다.

    Args:
        report: 문제가 있을 때 경고 메시지를 표시할지 여부.

    Returns:
        아이템 딕셔너리 리스트.
    """
    warn = (lambda msg: flash("warning", msg)) if report else (lambda msg: None)
    if not DATA_PATH.exists():
        return []
    try:
        data = json.loads(DATA_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError, OSError):
        warn(f"{DATA_FILE} 파일을 읽을 수 없어 빈 리스트로 시작합니다.")
        return []

    if not isinstance(data, list):
        warn(f"{DATA_FILE} 형식이 올바르지 않아 빈 리스트로 시작합니다.")
        return []

    items = [obj for obj in data if _is_valid_item(obj)]
    if len(items) != len(data):
        warn(f"{DATA_FILE}에서 형식이 잘못된 아이템 {len(data) - len(items)}개를 건너뛰었습니다.")
    return items


def save_items(items: list[dict]) -> None:
    """아이템 리스트를 DATA_FILE에 UTF-8 JSON으로 저장한다.

    임시 파일에 먼저 쓴 뒤 교체해서, 저장 중 중단돼도 파일이 깨지지 않게 한다.

    Args:
        items: 저장할 아이템 딕셔너리 리스트.
    """
    tmp_path = DATA_PATH.with_suffix(".json.tmp")
    try:
        tmp_path.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp_path, DATA_PATH)
    except OSError as e:
        flash("warning", f"저장에 실패했습니다: {e}")


def _commit(change: Callable[[list[dict]], object]) -> object:
    """파일의 최신 목록에 change를 적용하고, 바뀐 경우에만 저장한다.

    Args:
        change: 목록을 직접 수정하고, 바뀌었으면 참으로 평가되는 값을 돌려주는 함수.

    Returns:
        change의 반환값.
    """
    with _file_lock():
        items = load_items(report=False)
        result = change(items)
        if result:
            save_items(items)
    st.session_state["items"] = items
    return result


def _find_in(items: list[dict], item_id: str) -> dict | None:
    return next((it for it in items if it["id"] == item_id), None)


def _items() -> list[dict]:
    return st.session_state["items"]


def _find(item_id: str) -> dict | None:
    return _find_in(_items(), item_id)


# ---------------------------------------------------------------------------
# 기능 (CRUD)
# ---------------------------------------------------------------------------
def add_item(name: str) -> bool:
    """새 아이템을 리스트 맨 아래에 추가하고 저장한다.

    앞뒤 공백을 제거하며, 빈 값이거나 MAX_NAME_LENGTH를 넘으면 추가하지 않는다.

    Args:
        name: 사용자가 입력한 아이템 이름.

    Returns:
        추가에 성공하면 True, 검증에 실패하면 False.
    """
    clean, error = validate_name(name)
    if error:
        flash("warning", error)
        return False

    def change(items: list[dict]) -> bool:
        items.append(new_item(clean))
        return True

    return bool(_commit(change))


def update_item(item_id: str, new_name: str) -> bool:
    """아이템 이름을 수정하고 저장한다.

    Args:
        item_id: 수정할 아이템의 id.
        new_name: 새 이름 (앞뒤 공백 제거 후 검증).

    Returns:
        수정에 성공하면 True, 검증 실패 또는 아이템이 없으면 False.
    """
    clean, error = validate_name(new_name)
    if error:
        flash("warning", error)
        return False

    def change(items: list[dict]) -> bool:
        item = _find_in(items, item_id)
        if item is None:
            return False
        item["name"] = clean
        return True

    st.session_state["editing_id"] = None
    if not _commit(change):
        flash("warning", "이미 삭제된 아이템이라 수정하지 못했어요.")
        return False
    return True


def delete_item(item_id: str) -> None:
    """아이템을 삭제하고 저장한다.

    Args:
        item_id: 삭제할 아이템의 id.
    """
    def change(items: list[dict]) -> bool:
        before = len(items)
        items[:] = [it for it in items if it["id"] != item_id]
        return len(items) != before

    _commit(change)
    if st.session_state["editing_id"] == item_id:
        st.session_state["editing_id"] = None


def toggle_item(item_id: str, checked: bool | None = None) -> None:
    """아이템의 체크(구매 완료) 상태를 바꾸고 저장한다.

    Args:
        item_id: 바꿀 아이템의 id.
        checked: 지정하면 그 값으로 맞추고, None이면 현재 상태를 반전한다.
            체크박스에서는 값을 지정한다. 다른 탭이 먼저 체크한 아이템을
            다시 체크했을 때 해제되어 버리는 일을 막기 위해서다.
    """
    def change(items: list[dict]) -> bool:
        item = _find_in(items, item_id)
        if item is None:
            return False
        new_value = (not item["checked"]) if checked is None else checked
        if item["checked"] == new_value:
            return False
        item["checked"] = new_value
        return True

    _commit(change)


def clear_checked() -> int:
    """체크된 아이템을 모두 삭제하고 저장한다.

    Returns:
        삭제한 아이템 개수.
    """
    def change(items: list[dict]) -> int:
        before = len(items)
        items[:] = [it for it in items if not it["checked"]]
        return before - len(items)

    removed = _commit(change)
    editing = st.session_state["editing_id"]
    if editing is not None and _find(editing) is None:
        st.session_state["editing_id"] = None
    return int(removed or 0)


# ---------------------------------------------------------------------------
# 위젯 콜백 (기능 호출 + 결과 알림)
# ---------------------------------------------------------------------------
def notify(message: str) -> None:
    """다음 렌더링 때 st.toast로 보여줄 짧은 알림을 등록한다."""
    st.session_state["toast"] = message


def show_toast() -> None:
    """등록된 알림이 있으면 토스트로 띄우고 지운다."""
    message = st.session_state.pop("toast", None)
    if message:
        st.toast(message)


def on_add_submit() -> None:
    """추가 폼 제출 시 호출된다."""
    name = st.session_state.get("new_item_name", "")
    if add_item(name):
        notify(f"추가했어요: {escape_md(name.strip())}")


def on_start_edit(item_id: str) -> None:
    """[수정] 클릭 시 해당 아이템을 편집 모드로 바꾼다 (한 번에 하나만)."""
    item = _find(item_id)
    if item is None:
        return
    st.session_state["editing_id"] = item_id
    st.session_state[f"edit_{item_id}"] = item["name"]


def on_save_edit(item_id: str) -> None:
    """[저장] 클릭 시 호출된다."""
    if update_item(item_id, st.session_state.get(f"edit_{item_id}", "")):
        notify("수정했어요")


def on_cancel_edit() -> None:
    """[취소] 클릭 시 편집 모드를 해제한다."""
    st.session_state["editing_id"] = None


def on_delete(item_id: str) -> None:
    """[삭제] 클릭 시 호출된다."""
    item = _find(item_id)
    if item is None:
        return
    delete_item(item_id)
    notify(f"삭제했어요: {escape_md(item['name'])}")


def on_toggle(item_id: str) -> None:
    """체크박스 변경 시 호출된다. 체크박스에 표시된 값으로 맞춘다."""
    toggle_item(item_id, bool(st.session_state.get(f"check_{item_id}")))


def on_clear_checked() -> None:
    """[완료 항목 삭제] 클릭 시 호출된다."""
    count = clear_checked()
    if count:
        notify(f"담은 물건 {count}개를 삭제했어요")


# ---------------------------------------------------------------------------
# 상태 초기화
# ---------------------------------------------------------------------------
def init_state() -> None:
    """매 실행마다 파일에서 최신 목록을 읽어 상태를 맞춘다.

    다른 탭에서 바뀐 내용도 다음 동작(클릭, 새로고침) 때 반영된다.
    파일 문제 경고는 세션의 첫 실행에서만 보여준다.
    """
    first_run = "items" not in st.session_state
    st.session_state["items"] = load_items(report=first_run)
    editing = st.session_state.get("editing_id")
    if editing is not None and _find(editing) is None:
        editing = None  # 다른 탭에서 삭제된 아이템을 편집 중이었던 경우
    st.session_state["editing_id"] = editing


# ---------------------------------------------------------------------------
# 스타일
# ---------------------------------------------------------------------------
# 색상·글꼴의 기본값은 .streamlit/config.toml(라이트/다크 테마)에서 정한다.
# 여기서는 테마로 할 수 없는 배치와 강조만 다룬다.
ACCENT = "#3B9A62"  # 편집 중 테두리 (라이트/다크 모두에서 보이는 중간 톤)
DANGER = "#D2462F"  # 삭제 강조

CSS = f"""
<style>
/* 콘텐츠 폭 */
[data-testid="stMainBlockContainer"] {{
    max-width: 40rem;
    padding-top: 3rem;
    padding-bottom: 4rem;
}}

/* 헤더 */
[data-testid="stHeading"] h1 {{
    font-size: 2.75rem;
    line-height: 1.1;
    padding-bottom: 0;
}}
/* Streamlit 기본 문단 규칙(.st-emotion-cache-* p)보다 우선하도록 컨테이너까지 지정 */
[data-testid="stMarkdownContainer"] p.sl-subtitle {{
    margin: 0.35rem 0 0.75rem;
    opacity: 0.7;
}}

/* 진행 현황 */
[data-testid="stMarkdownContainer"] p.sl-status {{
    font-family: "Jua", sans-serif;
    font-size: 2.25rem;
    line-height: 1.15;
    margin: 0 0 0.25rem;
}}
.sl-status small {{
    font-size: 1rem;
    opacity: 0.6;
    margin-left: 0.5rem;
}}

/* 섹션 제목 */
.st-key-open_head, .st-key-done_head {{
    margin-top: 1rem;
}}
[data-testid="stMarkdownContainer"] p.sl-section {{
    font-family: "Jua", sans-serif;
    font-size: 1.25rem;
    margin: 0;
}}
.sl-section span {{
    opacity: 0.5;
    margin-left: 0.35rem;
}}

/* 목록: 행 간격 좁게 */
.st-key-open_list, .st-key-done_list {{
    gap: 0.5rem;
}}

/* 아이템 행 */
[class*="st-key-row_"] p {{
    margin: 0;
    overflow-wrap: anywhere;
}}
/* 기본 음수 여백(-1rem) 때문에 이름이 체크박스보다 아래로 처지는 문제 보정 */
[class*="st-key-row_"] [data-testid="stMarkdownContainer"],
.st-key-done_head [data-testid="stMarkdownContainer"] {{
    margin-bottom: 0 !important;
}}
/* 담은 물건: 테두리 없이 흐리게 */
[class*="st-key-row_done_"] {{
    opacity: 0.55;
    padding: 0.1rem calc(1rem - 1px);
}}
/* 편집 중인 행 강조 */
[class*="st-key-row_edit_"] {{
    border-color: {ACCENT} !important;
    box-shadow: 0 0 0 1px {ACCENT};
}}

/* 아이콘 버튼 */
[class*="st-key-editbtn_"] button,
[class*="st-key-delete_"] button {{
    min-height: 2.25rem;
    padding: 0 0.4rem;
    font-size: 1.05rem;
}}
[class*="st-key-delete_"] button:hover,
.st-key-clear_checked button:hover:not(:disabled) {{
    color: {DANGER};
}}

/* 모바일: 열이 세로로 쌓이지 않게 */
@media (max-width: 640px) {{
    [data-testid="stMainBlockContainer"] {{
        padding-top: 2rem;
    }}
    [data-testid="stHeading"] h1 {{
        font-size: 2.25rem;
    }}
    .sl-status {{
        font-size: 1.85rem;
    }}
    [data-testid="stMain"] [data-testid="stHorizontalBlock"] {{
        flex-wrap: nowrap !important;
        gap: 0.5rem;
    }}
    [data-testid="stMain"] [data-testid="stColumn"] {{
        min-width: 0 !important;
    }}
}}
</style>
"""


def inject_css() -> None:
    """앱 전체에 커스텀 CSS를 적용한다."""
    st.markdown(CSS, unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# 화면
# ---------------------------------------------------------------------------
def escape_md(text: str) -> str:
    """마크다운 특수문자를 이스케이프해 입력한 그대로 보이게 한다."""
    return _MD_SPECIAL.sub(r"\\\1", text)


def render_header() -> None:
    """제목과 부제."""
    st.title("🛒 쇼핑리스트", anchor=False)
    st.markdown('<p class="sl-subtitle">오늘 살 것들을 정리해 보세요</p>', unsafe_allow_html=True)


def render_progress(items: list[dict]) -> None:
    """F5: 진행 현황 카드 (남은 개수, 완료 개수, 진행률 바)."""
    total = len(items)
    if not total:
        return
    done = sum(1 for it in items if it["checked"])
    remaining = total - done
    status = f"{remaining}개 남았어요" if remaining else "다 담았어요"

    with st.container(border=True, key="summary"):
        st.markdown(
            f'<p class="sl-status">{status}<small>{done}개 담음</small></p>',
            unsafe_allow_html=True,
        )
        st.progress(done / total, text=f"전체 {total}개 중 {done}개 완료")


def render_add_form() -> None:
    """F1: 아이템 추가 폼."""
    with st.form("add_form", clear_on_submit=True, border=False):
        col_input, col_btn = st.columns([4, 1], vertical_alignment="bottom")
        col_input.text_input(
            "아이템 이름",
            key="new_item_name",
            placeholder="예: 우유, 계란",
            label_visibility="collapsed",
        )
        col_btn.form_submit_button("추가", type="primary", on_click=on_add_submit, width="stretch")


def render_checkbox(col, item: dict) -> None:
    """F4: 체크박스.

    위젯 상태를 파일의 값으로 먼저 맞춘다. value=로만 넘기면 Streamlit이
    이전 위젯 상태를 우선해서, 다른 탭에서 바뀐 체크 상태가 반영되지 않는다.
    """
    key = f"check_{item['id']}"
    st.session_state[key] = item["checked"]
    col.checkbox(
        item["name"],
        key=key,
        on_change=on_toggle,
        args=(item["id"],),
        label_visibility="collapsed",
    )


def render_item(item: dict) -> None:
    """F2~F4: 아이템 한 줄."""
    item_id = item["id"]

    if st.session_state["editing_id"] == item_id:
        # 편집 모드: 입력창 아래에 저장/취소
        with st.container(border=True, key=f"row_edit_{item_id}"):
            col_check, col_name = st.columns([0.6, 9], vertical_alignment="center")
            render_checkbox(col_check, item)
            col_name.text_input("아이템 이름 수정", key=f"edit_{item_id}", label_visibility="collapsed")
            _, col_save, col_cancel = st.columns([3, 1.3, 1.3])
            col_save.button("저장", key=f"save_{item_id}", type="primary",
                            on_click=on_save_edit, args=(item_id,), width="stretch")
            col_cancel.button("취소", key=f"cancel_{item_id}", on_click=on_cancel_edit, width="stretch")
        return

    state = "done" if item["checked"] else "open"
    with st.container(border=not item["checked"], key=f"row_{state}_{item_id}"):
        col_check, col_name, col_edit, col_del = st.columns(
            [0.6, 7, 0.9, 0.9], vertical_alignment="center", gap="small"
        )
        render_checkbox(col_check, item)
        label = escape_md(item["name"])
        col_name.markdown(f"~~{label}~~" if item["checked"] else label)
        col_edit.button("✏️", key=f"editbtn_{item_id}", type="tertiary", help="수정",
                        on_click=on_start_edit, args=(item_id,), width="stretch")
        col_del.button("🗑️", key=f"delete_{item_id}", type="tertiary", help="삭제",
                       on_click=on_delete, args=(item_id,), width="stretch")


def section_title(text: str, count: int) -> str:
    """섹션 제목 HTML (text는 고정 문구, count는 정수만 받는다)."""
    return f'<p class="sl-section">{text}<span>{count}</span></p>'


def render_list(items: list[dict]) -> None:
    """아이템 목록 (살 것 → 담은 것), F6 일괄 삭제, F8 빈 상태 안내."""
    if not items:
        st.info("쇼핑리스트가 비어 있습니다. 위 입력창에 살 물건을 적어 추가하세요.")
        return

    # 화면에서만 미완료 → 완료 순으로 보여준다 (저장 순서는 그대로)
    open_items = [it for it in items if not it["checked"]]
    done_items = [it for it in items if it["checked"]]

    with st.container(key="open_head"):
        st.markdown(section_title("살 것", len(open_items)), unsafe_allow_html=True)
    with st.container(key="open_list"):
        if open_items:
            for item in open_items:
                render_item(item)
        else:
            st.caption("살 것을 모두 담았어요.")

    with st.container(key="done_head"):
        col_title, col_clear = st.columns([3, 1.6], vertical_alignment="center")
        col_title.markdown(section_title("담은 것", len(done_items)), unsafe_allow_html=True)
        col_clear.button(
            "완료 항목 삭제",
            key="clear_checked",
            type="tertiary",
            on_click=on_clear_checked,
            disabled=not done_items,
            width="stretch",
        )
    with st.container(key="done_list"):
        if done_items:
            for item in done_items:
                render_item(item)
        else:
            st.caption("체크한 물건이 여기로 옮겨져요.")


def main() -> None:
    """앱 화면을 그린다."""
    st.set_page_config(page_title="쇼핑리스트", page_icon="🛒", layout="centered")
    inject_css()
    init_state()

    render_header()
    items = _items()
    render_progress(items)
    render_add_form()
    show_flash()
    show_toast()
    render_list(items)


main()
