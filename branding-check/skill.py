#!/usr/bin/env python3
"""
Branding Checker — Skill 2
Usage: python3 skill.py <content_sheet_url> <project_name>
"""
import os, re, sys, json, subprocess
from datetime import datetime
from dotenv import load_dotenv
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
import requests
from bs4 import BeautifulSoup

# ── Load credentials từ Skill 1 ──────────────────────────────────────────────
_env_path = os.path.expanduser("~/.claude/skills/seo-content-reviewer/.env")
load_dotenv(_env_path)

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/documents.readonly",
]

MASTER_SHEET_ID   = "1LoNZUU_ndBPdTPhTMMu3lGmOgK5xR3piacpCP_aY2VM"
CHECKLIST_SHEET   = "Checklist branding"
OUTPUT_SHEET_NAME = "Báo cáo Branding"

VISUAL_KEYWORDS = [
    "hình ảnh", "thumbnail", "video", "ảnh", "infographic",
    "brand guide", "màu sắc", "layout", "thiết kế", "visual",
    "banner", "icon", "logo", "font", "typography",
]


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

def get_doc_id(url):
    m = re.search(r"/document/d/([a-zA-Z0-9-_]+)", url)
    return m.group(1) if m else None

def is_visual_criterion(text):
    t = text.lower()
    return any(kw in t for kw in VISUAL_KEYWORDS)


# ── BƯỚC 1: ĐỌC CHECKLIST DỰ ÁN TỪ MASTER SHEET ─────────────────────────────

def read_checklist_for_project(sheets_svc, project_name):
    """
    Đọc sheet 'Checklist branding' trong MASTER_SHEET_ID.

    Cấu trúc thực tế: ['Loại', 'Mô tả', 'Dự án A', 'Dự án B', ...]
    Không có cột icon/Tr — tiêu chí áp dụng khi cột dự án có nội dung.

    Cũng hỗ trợ cấu trúc có cột icon: ['Loại', 'Mô tả', 'Tr', 'Dự án A', ...]
    → tự phát hiện bằng cách kiểm tra cột trước cột dự án.
    """
    result = sheets_svc.spreadsheets().values().get(
        spreadsheetId=MASTER_SHEET_ID,
        range=f"'{CHECKLIST_SHEET}'!A:Z",
    ).execute()
    rows = result.get("values", [])
    if not rows:
        raise ValueError(f"Sheet '{CHECKLIST_SHEET}' rỗng hoặc không tìm thấy.")

    headers = rows[0]

    # Tìm cột tên dự án (case-insensitive)
    project_col = None
    for i, h in enumerate(headers):
        if project_name.lower().strip() in h.lower().strip():
            project_col = i
            break

    if project_col is None:
        raise ValueError(
            f"Không tìm thấy cột dự án '{project_name}' trong sheet checklist.\n"
            f"Các header hiện có: {headers}"
        )

    # Phát hiện cột icon: cột ngay trước project_col có chứa ✅/— không?
    icon_col = None
    if project_col >= 1:
        prev_vals = {
            row[project_col - 1].strip()
            for row in rows[1:]
            if len(row) > project_col - 1 and row[project_col - 1].strip()
        }
        icon_like = {"✅", "—", "-", "TRUE", "FALSE", "X", "☑"}
        if prev_vals and prev_vals.issubset(icon_like | {""}):
            icon_col = project_col - 1

    checklist = []
    for row in rows[1:]:
        if len(row) <= project_col:
            continue

        huong_dan = row[project_col].strip()
        loai      = row[0].strip() if len(row) > 0 else ""
        mo_ta     = row[1].strip() if len(row) > 1 else ""

        if not huong_dan or huong_dan in ("Không áp dụng", "—", "-"):
            continue

        # Nếu có cột icon: bỏ qua hàng không có ✅
        if icon_col is not None:
            icon = row[icon_col].strip() if len(row) > icon_col else ""
            if "✅" not in icon and icon.upper() not in ("TRUE", "X", "1", "CÓ", "☑"):
                continue

        if mo_ta:  # bỏ qua hàng không có mô tả tiêu chí
            checklist.append({
                "loai":      loai,
                "mo_ta":     mo_ta,
                "huong_dan": huong_dan,
            })

    print(f"  📋 Đọc được {len(checklist)} tiêu chí cho dự án '{project_name}'")
    return checklist


# ── BƯỚC 2: ĐỌC DANH SÁCH BÀI CÓ TICK BRANDING ──────────────────────────────

def get_gid(url):
    m = re.search(r"gid=(\d+)", url)
    return int(m.group(1)) if m else None


def read_branded_articles(sheets_svc, content_sheet_url):
    """
    Đọc file content dự án, lọc bài có cột 'Áp dụng branding' được tick.
    Nhận diện cột theo TÊN, không hardcode index.
    """
    sid = get_sheet_id(content_sheet_url)
    if not sid:
        raise ValueError(f"URL không hợp lệ: {content_sheet_url}")

    meta = sheets_svc.spreadsheets().get(spreadsheetId=sid).execute()

    # Ưu tiên tab đúng gid từ URL, fallback sang tab đầu tiên
    target_gid = get_gid(content_sheet_url)
    tab = None
    for s in meta["sheets"]:
        if target_gid is not None and s["properties"]["sheetId"] == target_gid:
            tab = s["properties"]["title"]
            break
    if tab is None:
        tab = meta["sheets"][0]["properties"]["title"]

    result = sheets_svc.spreadsheets().values().get(
        spreadsheetId=sid,
        range=f"'{tab}'!A:AZ",
    ).execute()
    rows = result.get("values", [])
    if not rows:
        raise ValueError("File content dự án rỗng.")

    headers = rows[0]
    hlow    = [h.lower().strip() for h in headers]

    def find_col(*keywords):
        for i, h in enumerate(hlow):
            if any(k in h for k in keywords):
                return i
        return None

    col_keyword  = find_col("từ khóa", "keyword", "tu khoa")
    col_link_web = find_col("link bài đăng", "link đăng", "link bài viết web", "link website")
    col_link_doc = find_col("link doc", "google doc", "link bài viết", "link bai viet")
    col_branding = find_col("branding", "áp dụng branding", "ap dung branding")

    # Nếu link_web không tìm được, thử tìm cột "link" chung (cẩn thận không nhầm link doc)
    if col_link_web is None:
        for i, h in enumerate(hlow):
            if h == "link" or h == "link bài":
                col_link_web = i
                break

    def safe(row, col):
        return row[col].strip() if col is not None and col < len(row) else ""

    branded    = []
    total_rows = 0
    for row in rows[1:]:
        if not any(c.strip() for c in row):
            continue
        total_rows += 1
        flag = safe(row, col_branding).upper()
        if flag in ("TRUE", "✅", "☑", "X", "1", "CÓ"):
            branded.append({
                "keyword":  safe(row, col_keyword),
                "link_web": safe(row, col_link_web),
                "link_doc": safe(row, col_link_doc),
            })

    print(f"  📄 Tổng số bài: {total_rows} | Đã tick branding: {len(branded)}")
    return branded, total_rows


# ── BƯỚC 3: ĐỌC NỘI DUNG BÀI VIẾT ───────────────────────────────────────────

def fetch_from_website(url):
    """Crawl website, trả về text nếu > 200 ký tự, else None."""
    try:
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "vi-VN,vi;q=0.9,en;q=0.8",
        }
        resp = requests.get(url, headers=headers, timeout=10)
        resp.raise_for_status()

        soup = BeautifulSoup(resp.text, "html.parser")
        for tag in soup(["nav", "footer", "aside", "script", "style",
                         "header", "noscript"]):
            tag.decompose()

        main = (
            soup.find("article")
            or soup.find("main")
            or soup.find(class_=re.compile(r"post[-_]?content|entry[-_]?content|article[-_]?body", re.I))
            or soup.find(class_=re.compile(r"content|body", re.I))
            or soup.find("body")
        )
        text = main.get_text(separator="\n", strip=True) if main else ""
        if len(text) > 200:
            return text
        return None
    except Exception as e:
        print(f"    ⚠️  Website lỗi: {e}")
        return None


def fetch_from_doc(docs_svc, doc_url):
    """Đọc Google Doc, trả về text hoặc None."""
    doc_id = get_doc_id(doc_url)
    if not doc_id:
        return None
    try:
        doc   = docs_svc.documents().get(documentId=doc_id).execute()
        lines = []
        for el in doc.get("body", {}).get("content", []):
            if "paragraph" in el:
                para = "".join(
                    run["textRun"].get("content", "")
                    for run in el["paragraph"].get("elements", [])
                    if "textRun" in run
                ).rstrip("\n")
                if para:
                    lines.append(para)
        text = "\n".join(lines)
        return text if text.strip() else None
    except Exception as e:
        print(f"    ⚠️  Google Doc lỗi: {e}")
        return None


def fetch_article_content(link_web, link_doc, docs_svc):
    if link_web:
        text = fetch_from_website(link_web)
        if text:
            print(f"    🌐 Website OK ({len(text)} ký tự)")
            return text, "website"
        print("    → Fallback sang Google Doc...")

    if link_doc:
        text = fetch_from_doc(docs_svc, link_doc)
        if text:
            print(f"    📄 Google Doc OK ({len(text)} ký tự)")
            return text, "google_doc"

    print("    ❌ Không đọc được nội dung")
    return "", "unavailable"


# ── BƯỚC 4: ĐÁNH GIÁ BRANDING QUA CLAUDE ────────────────────────────────────

def build_eval_prompt(content, checklist, keyword):
    items = []
    for i, item in enumerate(checklist, 1):
        items.append(
            f"{i}. [{item['loai']}] {item['mo_ta']}\n"
            f"   Hướng dẫn: {item['huong_dan']}"
        )
    criteria_block = "\n".join(items)

    return f"""Bạn là chuyên gia đánh giá nội dung SEO theo checklist branding.
Đánh giá bài viết ở mức TỔNG QUAN — không cần khắt khe chi tiết.

Từ khóa bài viết: "{keyword}"

CHECKLIST ({len(checklist)} tiêu chí):
{criteria_block}

NỘI DUNG BÀI VIẾT (4000 ký tự đầu):
---
{content[:4000]}
---

Yêu cầu:
- Trả về JSON array gồm ĐÚNG {len(checklist)} item, theo đúng thứ tự checklist
- Với tiêu chí về hình ảnh/visual/thumbnail/video/màu sắc/thiết kế → ket_qua = "Không đánh giá được qua text"
- Với các tiêu chí còn lại → ket_qua = "ĐẠT" | "CẦN CẢI THIỆN" | "CHƯA ĐẠT"
- nhan_xet: 1-2 câu ngắn gọn lý do (tiếng Việt)
- KHÔNG viết bất kỳ text nào ngoài JSON

Format:
[
  {{"tieu_chi": "tên tiêu chí", "ket_qua": "ĐẠT", "nhan_xet": "lý do ngắn"}},
  ...
]"""


def call_claude(prompt):
    env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
    for attempt in range(2):
        result = subprocess.run(
            ["claude", "-p"],
            input=prompt,
            capture_output=True, text=True,
            timeout=600, env=env,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout
        if attempt == 0:
            print(f"    ⚠️  Claude lần 1 thất bại (exit {result.returncode}), thử lại...")
    raise RuntimeError(f"Claude CLI lỗi: {(result.stderr or result.stdout)[:300]}")


def extract_json_array(text):
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    m = re.search(r"\[[\s\S]*\]", text)
    if m:
        return json.loads(m.group(0))
    raise ValueError(f"Không tìm thấy JSON array:\n{text[:400]}")


def evaluate_branding(content, checklist, keyword):
    """
    Gọi Claude 1 lần per bài, đánh giá tất cả tiêu chí.
    Trả về list[{loai, mo_ta, ket_qua, nhan_xet}].
    """
    prompt = build_eval_prompt(content, checklist, keyword)

    for attempt in range(2):
        try:
            raw    = call_claude(prompt)
            parsed = extract_json_array(raw)
            if len(parsed) != len(checklist):
                raise ValueError(
                    f"Claude trả về {len(parsed)} items, cần {len(checklist)}"
                )
            # Merge với loai/mo_ta từ checklist gốc
            results = []
            for item, crit in zip(parsed, checklist):
                results.append({
                    "loai":     crit["loai"],
                    "mo_ta":    crit["mo_ta"],
                    "ket_qua":  item.get("ket_qua", "—"),
                    "nhan_xet": item.get("nhan_xet", ""),
                })
            return results
        except Exception as e:
            if attempt == 0:
                print(f"    ⚠️  Parse lỗi ({e}), thử lại...")
            else:
                print(f"    ❌ Đánh giá thất bại: {e}")
                # Trả về placeholder để không bỏ bài
                return [{
                    "loai":     c["loai"],
                    "mo_ta":    c["mo_ta"],
                    "ket_qua":  "Lỗi đánh giá",
                    "nhan_xet": str(e)[:100],
                } for c in checklist]


# ── BƯỚC 5: XUẤT DASHBOARD ───────────────────────────────────────────────────

def _color(r, g, b):
    return {"red": r / 255, "green": g / 255, "blue": b / 255}

NAVY    = _color(31, 56, 100)
WHITE   = _color(255, 255, 255)
GREEN   = _color(198, 239, 206)
RED     = _color(255, 199, 206)
ORANGE  = _color(255, 235, 156)
GRAY    = _color(242, 242, 242)
DARK    = _color(32, 32, 32)


def _cell_fmt(bg, bold=False, fg=None, size=10, halign=None):
    fmt = {
        "backgroundColor": bg,
        "textFormat": {
            "bold": bold,
            "foregroundColor": fg or DARK,
            "fontSize": size,
        },
    }
    if halign:
        fmt["horizontalAlignment"] = halign
    return fmt


def get_or_create_sheet(sheets_svc, title):
    """Trả về sheetId của sheet title, tạo mới nếu chưa có, rồi xóa nội dung cũ."""
    meta = sheets_svc.spreadsheets().get(spreadsheetId=MASTER_SHEET_ID).execute()
    for s in meta["sheets"]:
        if s["properties"]["title"] == title:
            sid = s["properties"]["sheetId"]
            sheets_svc.spreadsheets().values().clear(
                spreadsheetId=MASTER_SHEET_ID,
                range=f"'{title}'",
            ).execute()
            return sid

    resp = sheets_svc.spreadsheets().batchUpdate(
        spreadsheetId=MASTER_SHEET_ID,
        body={"requests": [{"addSheet": {"properties": {"title": title}}}]},
    ).execute()
    return resp["replies"][0]["addSheet"]["properties"]["sheetId"]


def calc_pct(article):
    results = article.get("eval_results", [])
    checkable = [r for r in results if r["ket_qua"] not in ("Không đánh giá được qua text", "Lỗi đánh giá")]
    if not checkable:
        return 0
    dat = sum(1 for r in checkable if r["ket_qua"] == "ĐẠT")
    return round(dat / len(checkable) * 100)


def export_dashboard(sheets_svc, project_name, total_articles,
                     branded_count, articles_results):
    now       = datetime.now().strftime("%d/%m/%Y %H:%M")
    sheet_sid = get_or_create_sheet(sheets_svc, OUTPUT_SHEET_NAME)

    # ── Build tất cả rows ─────────────────────────────────────────────────────
    rows_data = []   # list of list[str]

    # Row 0: Tiêu đề
    rows_data.append(["DASHBOARD BRANDING CHECKER", "", "", "", "", "", "", f"Cập nhật: {now}"])
    rows_data.append([""] * 8)

    # Row 2: Tổng quan header
    rows_data.append(["TỔNG QUAN DỰ ÁN", "", "", ""])
    rows_data.append(["Dự án", "Tổng bài trong file", "Đã áp dụng branding", "% Đạt checklist TB"])

    avg_pct = (
        round(sum(calc_pct(a) for a in articles_results) / len(articles_results))
        if articles_results else 0
    )
    rows_data.append([project_name, str(total_articles), str(branded_count), f"{avg_pct}%"])
    rows_data.append([""] * 8)

    # Row 6+: Chi tiết
    rows_data.append([f"CHI TIẾT — {project_name}", "", "", "", "", "", "", ""])
    rows_data.append(["STT", "Từ khóa", "Link", "Nguồn đọc",
                      "Loại tiêu chí", "Tiêu chí", "Kết quả", "Nhận xét"])

    detail_start = len(rows_data)  # dòng bắt đầu chi tiết (để format màu sau)
    color_requests = []            # batchUpdate format

    current_row = detail_start  # 0-indexed row index trong sheet

    for idx, article in enumerate(articles_results, 1):
        eval_results = article.get("eval_results", [])
        article_pct  = calc_pct(article)
        link_display = article.get("link_web") or article.get("link_doc") or ""
        source       = article.get("source", "")

        for i, r in enumerate(eval_results):
            if i == 0:
                row = [
                    str(idx),
                    article.get("keyword", ""),
                    link_display,
                    source,
                    r["loai"], r["mo_ta"], r["ket_qua"], r["nhan_xet"],
                ]
            else:
                row = ["", "", "", "", r["loai"], r["mo_ta"], r["ket_qua"], r["nhan_xet"]]
            rows_data.append(row)

            # Format màu cột G (index 6) theo kết quả
            kq = r["ket_qua"]
            if kq == "ĐẠT":
                bg = GREEN
            elif kq == "CHƯA ĐẠT":
                bg = RED
            elif kq == "CẦN CẢI THIỆN":
                bg = ORANGE
            else:
                bg = GRAY

            color_requests.append({
                "repeatCell": {
                    "range": {
                        "sheetId": sheet_sid,
                        "startRowIndex": current_row,
                        "endRowIndex":   current_row + 1,
                        "startColumnIndex": 6,
                        "endColumnIndex":   7,
                    },
                    "cell": {"userEnteredFormat": {"backgroundColor": bg}},
                    "fields": "userEnteredFormat.backgroundColor",
                }
            })
            current_row += 1

        # Dòng tổng % bài
        rows_data.append(["", "", "", f"→ Bài đạt: {article_pct}%", "", "", "", ""])
        rows_data.append([""] * 8)
        current_row += 2

    # ── Ghi dữ liệu ──────────────────────────────────────────────────────────
    sheets_svc.spreadsheets().values().update(
        spreadsheetId=MASTER_SHEET_ID,
        range=f"'{OUTPUT_SHEET_NAME}'!A1",
        valueInputOption="RAW",
        body={"values": rows_data},
    ).execute()

    # ── Format requests ───────────────────────────────────────────────────────
    format_requests = [
        # Row 1: Header dashboard (navy)
        {
            "repeatCell": {
                "range": {
                    "sheetId": sheet_sid,
                    "startRowIndex": 0, "endRowIndex": 1,
                    "startColumnIndex": 0, "endColumnIndex": 8,
                },
                "cell": {"userEnteredFormat": _cell_fmt(NAVY, bold=True, fg=WHITE, size=12)},
                "fields": "userEnteredFormat(backgroundColor,textFormat)",
            }
        },
        # Row 3: "TỔNG QUAN DỰ ÁN"
        {
            "repeatCell": {
                "range": {
                    "sheetId": sheet_sid,
                    "startRowIndex": 2, "endRowIndex": 3,
                    "startColumnIndex": 0, "endColumnIndex": 4,
                },
                "cell": {"userEnteredFormat": _cell_fmt(NAVY, bold=True, fg=WHITE, size=11)},
                "fields": "userEnteredFormat(backgroundColor,textFormat)",
            }
        },
        # Row 4: header bảng tổng quan
        {
            "repeatCell": {
                "range": {
                    "sheetId": sheet_sid,
                    "startRowIndex": 3, "endRowIndex": 4,
                    "startColumnIndex": 0, "endColumnIndex": 4,
                },
                "cell": {"userEnteredFormat": _cell_fmt(GRAY, bold=True)},
                "fields": "userEnteredFormat(backgroundColor,textFormat)",
            }
        },
        # Row 7: "CHI TIẾT"
        {
            "repeatCell": {
                "range": {
                    "sheetId": sheet_sid,
                    "startRowIndex": 6, "endRowIndex": 7,
                    "startColumnIndex": 0, "endColumnIndex": 8,
                },
                "cell": {"userEnteredFormat": _cell_fmt(NAVY, bold=True, fg=WHITE, size=11)},
                "fields": "userEnteredFormat(backgroundColor,textFormat)",
            }
        },
        # Row 8: header bảng chi tiết
        {
            "repeatCell": {
                "range": {
                    "sheetId": sheet_sid,
                    "startRowIndex": 7, "endRowIndex": 8,
                    "startColumnIndex": 0, "endColumnIndex": 8,
                },
                "cell": {"userEnteredFormat": _cell_fmt(GRAY, bold=True)},
                "fields": "userEnteredFormat(backgroundColor,textFormat)",
            }
        },
        # Auto-resize tất cả cột
        {
            "autoResizeDimensions": {
                "dimensions": {
                    "sheetId": sheet_sid,
                    "dimension": "COLUMNS",
                    "startIndex": 0, "endIndex": 8,
                }
            }
        },
    ]

    all_requests = format_requests + color_requests
    if all_requests:
        sheets_svc.spreadsheets().batchUpdate(
            spreadsheetId=MASTER_SHEET_ID,
            body={"requests": all_requests},
        ).execute()

    url = f"https://docs.google.com/spreadsheets/d/{MASTER_SHEET_ID}"
    print(f"  ✅ Dashboard đã xuất → sheet '{OUTPUT_SHEET_NAME}'")
    return url


# ── MAIN ──────────────────────────────────────────────────────────────────────

def run(content_sheet_url, project_name):
    print("🔄 Kết nối Google APIs...")
    sheets_svc = get_sheets_service()
    docs_svc   = get_docs_service()

    print(f"\n📋 Bước 1: Đọc checklist branding — dự án '{project_name}'")
    checklist = read_checklist_for_project(sheets_svc, project_name)
    if not checklist:
        print("⚠️  Không tìm thấy tiêu chí nào cho dự án này. Kiểm tra lại tên dự án.")
        return

    print(f"\n📊 Bước 2: Đọc danh sách bài từ file content...")
    branded_articles, total_rows = read_branded_articles(sheets_svc, content_sheet_url)

    print(f"\n📌 Dự án     : {project_name}")
    print(f"📝 Checklist : {len(checklist)} tiêu chí áp dụng")
    print(f"📄 Tổng bài  : {total_rows} | Cần kiểm tra: {len(branded_articles)}")

    if not branded_articles:
        print("\n⚠️  Không có bài nào tick 'Áp dụng branding'. Dừng.")
        return

    articles_results = []

    print(f"\n🔍 Bước 3-4: Đọc nội dung và đánh giá từng bài...")
    for i, article in enumerate(branded_articles, 1):
        label = article["keyword"] or article["link_web"] or article["link_doc"] or f"Bài #{i}"
        print(f"\n  [{i}/{len(branded_articles)}] {label}")

        content, source = fetch_article_content(
            article["link_web"], article["link_doc"], docs_svc
        )
        article["source"] = source

        if not content:
            article["eval_results"] = []
            articles_results.append(article)
            continue

        eval_results = evaluate_branding(content, checklist, article["keyword"])
        dat_count    = sum(1 for r in eval_results if r["ket_qua"] == "ĐẠT")
        can_check    = sum(1 for r in eval_results if r["ket_qua"] != "Không đánh giá được qua text")
        print(f"    📊 Kết quả: {dat_count}/{can_check} tiêu chí đạt (có thể check qua text)")

        article["eval_results"] = eval_results
        articles_results.append(article)

    print(f"\n📊 Bước 5: Xuất dashboard...")
    url = export_dashboard(
        sheets_svc, project_name,
        total_rows, len(branded_articles), articles_results,
    )

    print(f"\n🎉 Hoàn tất! Xem kết quả tại:\n{url}")
    print(f"   → Sheet: '{OUTPUT_SHEET_NAME}'")


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python3 skill.py <content_sheet_url> <ten_du_an>")
        print("Ví dụ: python3 skill.py https://docs.google.com/spreadsheets/d/1abc... 'VNPAY Số'")
        sys.exit(1)
    run(sys.argv[1], sys.argv[2])
