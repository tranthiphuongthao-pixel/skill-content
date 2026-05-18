# SKILL: Branding Check

Tự động kiểm tra mức độ áp dụng branding cho toàn bộ bài viết SEO của một dự án.

## Kết nối nền tảng ngoài
- **Google Sheets API** — đọc danh sách bài từ content sheet + checklist branding + ghi kết quả vào tab "Báo cáo Branding"
- **Google Docs API** — đọc nội dung từng bài viết
- **Claude CLI** (`claude -p`) — phân tích mức độ áp dụng branding theo từng tiêu chí

## Cách dùng

```
/branding-check <link_google_sheet_danh_sach> <tên_dự_án>
```

Ví dụ:
```
/branding-check https://docs.google.com/spreadsheets/d/1abc.../edit SEONGON
```

## Cấu hình

Skill này dùng chung file `.env` với `seo-content-review`:
```
~/.claude/skills/seo-content-reviewer/.env
```

Cần có:
- `GOOGLE_CLIENT_ID`
- `GOOGLE_CLIENT_SECRET`
- `GOOGLE_REFRESH_TOKEN`

## Luồng hoạt động

```
Input: Google Sheet content + tên dự án
  └─ Đọc tab "Checklist branding" từ Master Sheet
  └─ Lọc danh sách bài cần kiểm tra

Với mỗi bài viết:
  └─ Đọc nội dung Google Doc
  └─ Claude CLI chấm theo từng tiêu chí branding
  └─ Phân loại: ✅ đạt / ❌ chưa đạt / 👁 cần kiểm tra thủ công

Output → tab "Báo cáo Branding" trong Master Sheet
  └─ Kết quả từng tiêu chí + ghi chú chi tiết lỗi
```

## Cấu trúc file

```
branding-check/
├── SKILL.md      ← file này
└── skill.py      ← script chính
```
