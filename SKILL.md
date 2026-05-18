# SKILL: SEO Content Review

Tự động kiểm tra bài viết SEO theo checklist branding từ Google Sheet.

## Kết nối nền tảng ngoài
- **Google Sheets API** — đọc danh sách bài + checklist + ghi kết quả
- **Google Docs API** — đọc toàn bộ nội dung bài viết
- **Claude CLI** (`claude -p`) — phân tích nội dung theo từng tiêu chí

## Cách dùng

```
/seo-review <link_google_sheet_danh_sach>
```

Ví dụ:
```
/seo-review https://docs.google.com/spreadsheets/d/1abc.../edit
```

## Cấu hình

### Cài thư viện
```bash
pip install google-api-python-client google-auth python-dotenv
```

### Tạo file `.env` (không commit)
```bash
cp .env.example .env
# Điền GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, GOOGLE_REFRESH_TOKEN
```

### Lấy Google refresh token
1. Vào https://developers.google.com/oauthplayground/
2. ⚙️ → "Use your own OAuth credentials" → điền Client ID & Secret
3. Thêm scopes: `spreadsheets` + `documents.readonly` → Authorize
4. Exchange code → copy `refresh_token` vào `.env`

## Luồng hoạt động

```
Input Google Sheet (tab đầu tiên)
  └─ Lọc bài có trạng thái "Y/c duyệt"
  └─ Lấy link Google Doc từng bài

Output Google Sheet (cố định, ID trong .env)
  └─ Fuzzy-match tên dự án → tìm đúng tab
  └─ Đọc checklist từ header các cột

Với mỗi bài:
  └─ Đọc toàn bộ Google Doc
  └─ Claude CLI kiểm tra từng tiêu chí (quét toàn bài, báo đủ lỗi)
  └─ Append kết quả ✅/❌ + chi tiết lỗi vào output sheet
```

## Cấu trúc file

```
seo-content-review/
├── SKILL.md          ← file này
├── skill.py          ← script chính
├── .env.example      ← template cấu hình
└── .gitignore        ← loại trừ .env
```
