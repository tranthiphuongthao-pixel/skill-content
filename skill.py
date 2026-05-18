#!/usr/bin/env python3
"""
SEO Content Reviewer
Usage: python3 skill.py <input_google_sheet_url>
"""
import os, re, sys, json, subprocess
from dotenv import load_dotenv
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/documents.readonly",
]

OUTPUT_SHEET_ID = os.getenv("OUTPUT_SHEET_ID", "1LoNZUU_ndBPdTPhTMMu3lGmOgK5xR3piacpCP_aY2VM")


# ── AUTH ──────────────────────────────────────────────────────────────────────

def get_credentials():
    return Credentials(
        token=None,
        refresh_token=os.environ["GOOGLE_REFRESH_TOKEN"],
        token_uri="https://oauth2.googleapis.com/token",
        client_id=os.environ["GOOGLE_CLIENT_ID"],
        client_secret=os.environ["GOOGLE_CLIENT_SECRET"],
        scopes=SCOPES,
    )

def get_sheets_service():
    return build("sheets", "v4", credentials=get_credentials())

def get_docs_service():
    return build("docs", "v1", credentials=get_credentials())


# ── UTILS ─────────────────────────────────────────────────────────────────────

def get_sheet_id(url):
    m = re.search(r"/spreadsheets/d/([a-zA-Z0-9-_]+)", url)
    return m.group(1) if m else None

def get_gid(url):
    m = re.search(r"gid=(\d+)", url)
    return int(m.group(1)) if m else None

def get_doc_id(url):
    m = re.search(r"/document/d/([a-zA-Z0-9-_]+)", url)
    return m.group(1) if m else None

def tokenize(name):
    stopwords = {"và", "the", "of", "a", "an", "in", "cho", "của", "với", "số", "so"}
    tokens = re.findall(r"[a-zA-Z0-9À-ɏḀ-ỿ]+", name.lower())
    return {t for t in tokens if len(t) > 1 and t not in stopwords}

def fuzzy_match(project_name, sheet_titles):
    """Tìm tab output khớp nhất với tên dự án (overlap từ > substring)."""
    p_tok = tokenize(project_name)
    best, best_score = None, 0
    for title in sheet_titles:
        score = len(p_tok & tokenize(title))
        if score > best_score:
            best, best_score = title, score
    if best_score > 0:
        return best
    p_lower = project_name.lower()
    for title in sheet_titles:
        if title.lower() in p_lower or p_lower in title.lower():
            return title
    return None


# ── BƯỚC 1: ĐỌC INPUT SHEET ──────────────────────────────────────────────────

def read_input_sheet(sheets_svc, sheet_url):
    """Trả về (project_name, articles[]) — chỉ bài có 'Y/c duyệt'."""
    sid = get_sheet_id(sheet_url)
    gid = get_gid(sheet_url)
    meta = sheets_svc.spreadsheets().get(spreadsheetId=sid).execute()

    # project_name = tên FILE (dùng để fuzzy-match tab output)
    project_name = meta["properties"]["title"]

    # tab_name = tab cụ thể theo gid, dùng để đọc dữ liệu
    tab_name = meta["sheets"][0]["properties"]["title"]
    if gid is not None:
        for s in meta["sheets"]:
            if s["properties"]["sheetId"] == gid:
                tab_name = s["properties"]["title"]
                break

    result = sheets_svc.spreadsheets().values().get(
        spreadsheetId=sid, range=f"'{tab_name}'!A:Z"
    ).execute()
    rows = result.get("values", [])
    if not rows:
        raise ValueError("Sheet đầu vào rỗng")

    headers = [h.strip().lower() for h in rows[0]]

    def find_col(*keywords):
        """Tìm cột đầu tiên match tên."""
        for i, h in enumerate(headers):
            if any(k in h for k in keywords):
                return i
        return None

    def find_col_with_value(*keywords, target="y/c duyệt"):
        """Tìm cột match tên VÀ thực sự chứa giá trị target; fallback về cột đầu tiên match tên."""
        candidates = [i for i, h in enumerate(headers) if any(k in h for k in keywords)]
        for i in candidates:
            vals = {row[i].strip().lower() for row in rows[1:] if i < len(row) and row[i].strip()}
            if target in vals:
                return i
        return candidates[0] if candidates else None

    col_status  = find_col_with_value("content duyệt", "trạng thái", "trang thai", "status")
    col_link    = find_col("link bài", "link viết", "link doc", "link url", "url bài", "link")
    col_keyword = find_col("từ khóa", "keyword", "tu khoa")
    col_nv      = find_col("nv-deadline", "nv deadline", "người viết")

    if col_status is None:
        raise ValueError(f"Không tìm thấy cột trạng thái. Headers: {rows[0]}")
    if col_link is None:
        raise ValueError(f"Không tìm thấy cột link bài viết. Headers: {rows[0]}")

    def safe(row, col):
        return row[col].strip() if col is not None and col < len(row) else ""

    articles = []
    for row in rows[1:]:
        if not any(c.strip() for c in row):
            continue
        status = safe(row, col_status)
        if "y/c duyệt" in status.lower() or "yc duyệt" in status.lower():
            articles.append({
                "keyword":     safe(row, col_keyword),
                "nv_deadline": safe(row, col_nv),
                "link":        safe(row, col_link),
            })

    return project_name, articles


# ── BƯỚC 2: ĐỌC CHECKLIST TỪ OUTPUT SHEET ────────────────────────────────────

# Cột metadata cố định ở đầu và cuối sheet output
FIXED_HEADER_NAMES = {"stt", "từ khóa", "keyword", "nv-deadline", "link", "url"}
FIXED_TAIL_NAMES   = {"tổng lỗi", "tong loi", "%", "tỉ lệ"}

def is_metadata_col(name: str) -> bool:
    """True nếu cột này là metadata (không phải checklist)."""
    n = name.strip().lower()
    if n in FIXED_HEADER_NAMES or n in FIXED_TAIL_NAMES:
        return True
    # Khớp chính xác các từ ngắn gây false-positive (không dùng substring)
    return False

def read_checklist_from_output(sheets_svc, project_name):
    """Fuzzy-match tab trong output sheet → đọc checklist columns từ header."""
    meta = sheets_svc.spreadsheets().get(spreadsheetId=OUTPUT_SHEET_ID).execute()
    all_titles = [s["properties"]["title"] for s in meta["sheets"]]

    matched = fuzzy_match(project_name, all_titles)
    if not matched:
        raise ValueError(
            f"Không tìm được tab output cho dự án '{project_name}'.\n"
            f"Tabs hiện có: {all_titles}"
        )
    print(f"  🗂  '{project_name}' → tab '{matched}'")

    result = sheets_svc.spreadsheets().values().get(
        spreadsheetId=OUTPUT_SHEET_ID, range=f"'{matched}'!1:1"
    ).execute()
    raw_headers = result.get("values", [[]])[0]

    checklist_cols = [
        {"index": i, "name": h.strip()}
        for i, h in enumerate(raw_headers)
        if h.strip() and not is_metadata_col(h)
    ]
    if not checklist_cols:
        raise ValueError(f"Không tìm thấy cột checklist trong tab '{matched}'. Headers: {raw_headers}")

    return matched, raw_headers, checklist_cols


# ── BƯỚC 3: ĐỌC GOOGLE DOC ───────────────────────────────────────────────────

def read_doc(docs_svc, doc_url):
    doc_id = get_doc_id(doc_url)
    if not doc_id:
        return None, "Không parse được Doc ID"
    try:
        doc = docs_svc.documents().get(documentId=doc_id).execute()
    except Exception as e:
        return None, str(e)

    lines = []
    for el in doc.get("body", {}).get("content", []):
        if "paragraph" in el:
            text = "".join(
                run["textRun"].get("content", "")
                for run in el["paragraph"].get("elements", [])
                if "textRun" in run
            ).rstrip("\n")
            if text:
                lines.append(text)
    return "\n".join(lines), None


# ── BƯỚC 4: CLAUDE CODE PHÂN TÍCH ────────────────────────────────────────────

def build_prompt(doc_text, checklist_cols, keyword):
    items = "\n".join(
        f"{i+1}. {c['name']}" for i, c in enumerate(checklist_cols)
    )
    return f"""Kiểm tra bài viết SEO sau theo từng tiêu chí checklist.

Từ khóa bài viết: "{keyword}"

Yêu cầu bắt buộc:
- Đọc TOÀN BỘ bài từ đầu đến cuối cho mỗi tiêu chí
- Tìm TẤT CẢ vị trí vi phạm (không dừng sau lỗi đầu tiên)
- Trả về JSON array duy nhất, KHÔNG có text nào khác
- Số item trong array phải bằng đúng {len(checklist_cols)}

Format JSON:
[
  {{"checklist": "tên tiêu chí", "ok": true, "error": ""}},
  {{"checklist": "tên tiêu chí", "ok": false, "error": "Lỗi 1: [vị trí] → sai: [trích dẫn] → sửa: [gợi ý]\\nLỗi 2: ..."}}
]

CHECKLIST ({len(checklist_cols)} tiêu chí):
{items}

NỘI DUNG BÀI VIẾT:
{doc_text}"""


def call_claude(prompt):
    # Xóa ANTHROPIC_API_KEY để claude CLI dùng Claude.ai session (không tốn credits)
    env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
    for attempt in range(2):
        result = subprocess.run(
            ["claude", "-p"],
            input=prompt,
            capture_output=True, text=True,
            timeout=600, env=env
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout
        if attempt == 0:
            print(f"    ⚠️  Lần 1 thất bại (exit {result.returncode}), thử lại...")
    raise RuntimeError(f"claude CLI lỗi: {result.stderr[:200] or result.stdout[:200]}")


def extract_json(text):
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    m = re.search(r"\[[\s\S]*\]", text)
    if m:
        return json.loads(m.group(0))
    raise ValueError(f"Không tìm thấy JSON hợp lệ:\n{text[:300]}")


def check_article(doc_text, checklist_cols, keyword):
    prompt = build_prompt(doc_text, checklist_cols, keyword)
    for attempt in range(2):
        try:
            raw = call_claude(prompt)
            parsed = extract_json(raw)
            if len(parsed) != len(checklist_cols):
                raise ValueError(f"Trả về {len(parsed)} items, cần {len(checklist_cols)}")
            return parsed
        except Exception as e:
            if attempt == 0:
                print(f"    ⚠️  Parse lỗi: {e}. Thử lại...")
            else:
                raise


# ── BƯỚC 5: APPEND KẾT QUẢ VÀO OUTPUT SHEET ─────────────────────────────────

def format_cell(item):
    if item.get("ok"):
        return "✅"
    error = item.get("error", "").strip()
    return f"❌\n{error}" if error else "❌"


def append_result(sheets_svc, tab_name, raw_headers, article, checklist_cols, results):
    row = [""] * len(raw_headers)

    for i, h in enumerate(raw_headers):
        hl = h.lower()
        if any(k in hl for k in ["từ khóa", "keyword"]):
            row[i] = article["keyword"]
        elif any(k in hl for k in ["nv", "deadline", "người viết"]):
            row[i] = article["nv_deadline"]
        elif any(k in hl for k in ["link", "url"]):
            row[i] = article["link"]

    result_map = {r["checklist"]: r for r in results}
    total_errors = 0
    for col in checklist_cols:
        r = result_map.get(col["name"]) or next(
            (x for x in results if x["checklist"] in col["name"] or col["name"] in x["checklist"]), None
        )
        if r:
            row[col["index"]] = format_cell(r)
            if not r.get("ok"):
                total_errors += 1
        else:
            row[col["index"]] = "—"

    for i, h in enumerate(raw_headers):
        if any(k in h.lower() for k in ["%", "tỉ lệ", "tong", "tổng lỗi"]):
            row[i] = total_errors
            break

    sheets_svc.spreadsheets().values().append(
        spreadsheetId=OUTPUT_SHEET_ID,
        range=f"'{tab_name}'!A1",
        valueInputOption="RAW",
        insertDataOption="INSERT_ROWS",
        body={"values": [row]},
    ).execute()

    return total_errors


# ── MAIN ──────────────────────────────────────────────────────────────────────

def run(input_sheet_url):
    print("🔄 Kết nối Google APIs...")
    sheets_svc = get_sheets_service()
    docs_svc   = get_docs_service()

    print("📋 Đọc danh sách bài Y/c duyệt...")
    project_name, articles = read_input_sheet(sheets_svc, input_sheet_url)
    print(f"  📌 Dự án : {project_name}")
    print(f"  📄 Bài cần duyệt: {len(articles)}")

    if not articles:
        print("⚠️  Không có bài nào ở trạng thái 'Y/c duyệt'. Dừng.")
        return

    print("📂 Tìm tab output và checklist...")
    tab_name, raw_headers, checklist_cols = read_checklist_from_output(sheets_svc, project_name)
    print(f"  📝 {len(checklist_cols)} tiêu chí: {[c['name'] for c in checklist_cols]}")

    print()
    for i, article in enumerate(articles, 1):
        label = article["keyword"] or article["link"]
        print(f"🔍 [{i}/{len(articles)}] {label}")

        content, err = read_doc(docs_svc, article["link"])
        if err:
            print(f"  ⚠️  Không đọc được Doc: {err}")
            dummy = [{"checklist": c["name"], "ok": False,
                      "error": "Không truy cập được Google Doc — kiểm tra quyền share"}
                     for c in checklist_cols]
            append_result(sheets_svc, tab_name, raw_headers, article, checklist_cols, dummy)
            continue

        try:
            results = check_article(content, checklist_cols, article["keyword"])
        except Exception as e:
            print(f"  ❌ Claude check thất bại: {e}")
            continue

        errors = append_result(sheets_svc, tab_name, raw_headers, article, checklist_cols, results)
        passed = len(checklist_cols) - errors
        print(f"  ✅ {passed}/{len(checklist_cols)} tiêu chí đạt → đã ghi vào sheet")

    url = f"https://docs.google.com/spreadsheets/d/{OUTPUT_SHEET_ID}"
    print(f"\n🎉 Hoàn tất! Xem kết quả:\n{url}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 skill.py <google_sheet_url>")
        sys.exit(1)
    run(sys.argv[1])
