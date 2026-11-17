import json
import os
import re
import string
import threading
import uuid
from pathlib import Path

import streamlit as st

# 방문자별 목록 파일 저장 위치: data/<list_id>.json
DATA_DIR = Path(__file__).parent / "data"
LIST_ID_RE = re.compile(r"[0-9a-f]{32}")  # 파일명으로 쓰이므로 형식을 엄격히 검증한다
MAX_NAME_LEN = 100
MAX_QUANTITY = 99


# 같은 서버 안에서 여러 세션이 동시에 파일을 읽고 쓰지 않도록 하는 전역 락
@st.cache_resource
def get_lock():
    return threading.Lock()


# 주소의 ?id= 값으로 방문자(목록)를 구분한다. 없거나 형식이 틀리면 새로 발급한다
def get_list_id():
    list_id = st.query_params.get("id", "")
    if not LIST_ID_RE.fullmatch(list_id):
        list_id = uuid.uuid4().hex
        st.query_params["id"] = list_id
    return list_id


# 방문자 id에 해당하는 데이터 파일 경로
def list_path(list_id):
    return DATA_DIR / f"{list_id}.json"


# 파일에서 읽은 한 항목을 검증·보정한다 (잘못된 항목이면 None)
def sanitize_item(raw, seen_ids):
    if not isinstance(raw, dict):
        return None
    name = raw.get("name")
    if not isinstance(name, str) or not name.strip():
        return None
    item_id = raw.get("id")
    if not isinstance(item_id, str) or not item_id or item_id in seen_ids:
        item_id = str(uuid.uuid4())
    seen_ids.add(item_id)
    quantity = raw.get("quantity")
    if isinstance(quantity, bool) or not isinstance(quantity, int):
        quantity = 1
    return {
        "id": item_id,
        "name": name.strip()[:MAX_NAME_LEN],
        "quantity": max(1, min(quantity, MAX_QUANTITY)),
        "checked": raw.get("checked") is True,
    }


# 아이템 목록을 파일에서 불러온다 (없거나 깨져 있으면 빈 리스트, 잘못된 항목은 건너뜀)
def load_items(path):
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(data, list):
        return []
    seen_ids = set()
    items = (sanitize_item(raw, seen_ids) for raw in data)
    return [item for item in items if item is not None]


# 임시 파일에 먼저 쓴 뒤 교체해서, 쓰는 도중 중단돼도 기존 파일이 깨지지 않게 저장한다
def save_items(path, items):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


# 파일을 다시 읽어 → 변경 → 저장한다 (다른 탭/세션의 변경을 덮어쓰지 않기 위함)
def mutate(change):
    path = list_path(st.session_state.list_id)
    with get_lock():
        items = load_items(path)
        change(items)
        save_items(path, items)


# 새 아이템을 목록에 추가한다
def add_item(name, quantity=1):
    new = {"id": str(uuid.uuid4()), "name": name, "quantity": quantity, "checked": False}
    mutate(lambda items: items.append(new))


# id로 아이템을 찾아 이름/수량을 수정한다
def update_item(item_id, name, quantity):
    def change(items):
        for item in items:
            if item["id"] == item_id:
                item["name"] = name
                item["quantity"] = quantity
    mutate(change)


# id로 아이템을 삭제한다
def delete_item(item_id):
    mutate(lambda items: items.__setitem__(slice(None), [i for i in items if i["id"] != item_id]))


# id로 아이템의 체크 상태를 토글한다
def toggle_item(item_id):
    def change(items):
        for item in items:
            if item["id"] == item_id:
                item["checked"] = not item["checked"]
    mutate(change)


# 체크된 아이템을 모두 삭제한다
def clear_checked():
    mutate(lambda items: items.__setitem__(slice(None), [i for i in items if not i["checked"]]))
    st.session_state.editing_id = None


# 삭제 버튼 콜백: 수정 중인 항목이면 수정 모드도 해제한다
def on_delete(item_id):
    delete_item(item_id)
    if st.session_state.editing_id == item_id:
        st.session_state.editing_id = None


# 마크다운이 이름을 서식/링크/이모지로 해석하지 못하도록 ASCII 구두점을 모두 이스케이프한다
def escape_md(text):
    return "".join("\\" + ch if ch in string.punctuation else ch for ch in text)


# ───────────────────────── UI ─────────────────────────

st.set_page_config(page_title="쇼핑 리스트", page_icon="🛒", layout="centered")

# 테마(라이트/다크)에 따라 색이 바뀌는 Streamlit 기본 변수만 사용한 최소 CSS
st.markdown(
    """
    <style>
    /* 모바일에서도 목록 행(체크박스 | ✏️ | 🗑️)이 한 줄을 유지하도록 */
    div[data-testid="stHorizontalBlock"] { flex-wrap: nowrap !important; align-items: center; }
    div[data-testid="stColumn"] { min-width: 0 !important; }
    /* 아이콘 버튼은 작게 */
    div[data-testid="stColumn"] button { padding: 0.15rem 0.5rem; min-height: 2.2rem; }
    /* 상단 여백 축소, 불필요한 기본 요소 숨김 */
    .block-container { padding-top: 2.5rem; }
    footer, #MainMenu { visibility: hidden; }
    </style>
    """,
    unsafe_allow_html=True,
)

# 방문자 id 확정 + 수정 중인 항목 상태 초기화
st.session_state.list_id = get_list_id()
if "editing_id" not in st.session_state:
    st.session_state.editing_id = None

# 파일이 항상 기준: 매 실행마다 다시 읽는다 (다른 탭의 변경도 반영됨)
items = load_items(list_path(st.session_state.list_id))


# 아이템 한 줄을 그린다 (일반 모드: 체크박스 | 수정 | 삭제, 수정 모드: 편집 폼)
def render_item(item):
    iid = item["id"]

    if st.session_state.editing_id == iid:
        # F2: 수정 모드
        with st.form(f"edit_form_{iid}"):
            c_name, c_qty = st.columns([3, 1])
            edit_name = c_name.text_input(
                "이름", value=item["name"], max_chars=MAX_NAME_LEN, key=f"edit_name_{iid}"
            )
            edit_qty = c_qty.number_input(
                "수량", min_value=1, max_value=MAX_QUANTITY, value=item["quantity"],
                step=1, key=f"edit_qty_{iid}",
            )
            c_save, c_cancel = st.columns(2)
            save_clicked = c_save.form_submit_button("저장", type="primary", use_container_width=True)
            cancel_clicked = c_cancel.form_submit_button("취소", use_container_width=True)
        if save_clicked:
            if edit_name.strip():
                update_item(iid, edit_name.strip(), int(edit_qty))
                st.session_state.editing_id = None
                st.rerun()
            else:
                st.warning("아이템 이름은 비워 둘 수 없어요.")
        elif cancel_clicked:
            st.session_state.editing_id = None
            st.rerun()
        return

    c_check, c_edit, c_del = st.columns([8, 1.3, 1.3], vertical_alignment="center")
    # F4: 체크 (완료 항목은 취소선 + 회색)
    label = f"{escape_md(item['name'])}  ×{item['quantity']}"
    if item["checked"]:
        label = f":gray[~~{label}~~]"
    c_check.checkbox(
        label, value=item["checked"], key=f"chk_{iid}",
        on_change=toggle_item, args=(iid,),
    )
    c_edit.button("✏️", key=f"edit_{iid}", help="수정",
                  on_click=lambda i=iid: st.session_state.update(editing_id=i))
    # F3: 삭제
    c_del.button("🗑️", key=f"del_{iid}", help="삭제", on_click=on_delete, args=(iid,))


st.title("🛒 쇼핑 리스트")
st.caption("이 페이지 주소가 나만의 목록이에요. 즐겨찾기(북마크)해 두면 다음에도 같은 목록을 볼 수 있어요.")

# F7: 진행 현황
total = len(items)
done = sum(1 for i in items if i["checked"])
st.progress(done / total if total else 0.0)
st.caption(f"**{done} / {total}** 완료" if total else "목록이 비어 있어요")

# F1 + F5: 아이템 추가 (한 줄 배치, Enter 제출, 제출 후 입력창 초기화)
with st.form("add_form", clear_on_submit=True, border=False):
    c_name, c_qty, c_btn = st.columns([5, 2, 2], vertical_alignment="bottom")
    new_name = c_name.text_input("아이템", placeholder="무엇을 살까요? 예: 우유",
                                 max_chars=MAX_NAME_LEN, label_visibility="collapsed")
    new_qty = c_qty.number_input("수량", min_value=1, max_value=MAX_QUANTITY, value=1,
                                 step=1, label_visibility="collapsed")
    submitted = c_btn.form_submit_button("추가", type="primary", use_container_width=True)
if submitted:
    if new_name.strip():
        add_item(new_name.strip(), int(new_qty))
        st.rerun()
    else:
        st.warning("아이템 이름을 입력해 주세요.")

# 아이템 목록: 미완료 → 완료 순으로 구분
if not items:
    st.info("아직 추가된 항목이 없어요. 위에서 첫 아이템을 추가해 보세요!")
else:
    pending = [i for i in items if not i["checked"]]
    completed = [i for i in items if i["checked"]]

    for item in pending:
        render_item(item)
    if not pending:
        st.success("모두 구매했어요! 🎉")

    if completed:
        st.divider()
        st.caption(f"완료 {len(completed)}개")
        for item in completed:
            render_item(item)

# F6: 완료 항목 일괄 삭제
st.button("완료 항목 모두 삭제", on_click=clear_checked, disabled=done == 0,
          use_container_width=True)
