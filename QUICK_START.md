# Quick Start - 5 Steps ✅

## Your Structure
```
project/
├── main.py          (you have this)
├── app/
│   ├── main.py      (UPDATE THIS ⚠️)
│   ├── api/         (you have this)
│   └── templates/   (ADD FILES HERE 📁)
└── static/          (ADD FILES HERE 📁)
```

## Steps

### 1️⃣ Copy Templates
From the zip → `app/templates/`
```bash
cp templates/*.html app/templates/
```

Should have 8 files:
- base.html
- index.html
- reader.html
- search.html
- continue_reading.html
- collections.html
- reading_lists.html
- error.html

### 2️⃣ Copy Static Files
From the zip → `static/`
```bash
cp static/css/style.css static/css/
cp static/js/app.js static/js/
```

### 3️⃣ Update app/main.py
Replace with `app_main.py` from download:
```bash
cp app_main.py app/main.py
```

**OR** manually add:
- Import Jinja2 and StaticFiles
- Mount static files
- Setup templates
- Add frontend routes (see INTEGRATION_GUIDE.md)

### 4️⃣ Install Dependencies
```bash
pip install jinja2 aiofiles
```

### 5️⃣ Run!
```bash
python main.py
# OR
./start.sh
```

## Test It
- ✅ http://localhost:8000 → Home page
- ✅ http://localhost:8000/docs → API docs
- ✅ http://localhost:8000/api/comics/ → JSON response

## Files You Need from Zip

### From `templates/` folder:
- [x] base.html
- [x] index.html
- [x] reader.html
- [x] search.html
- [x] continue_reading.html
- [x] collections.html
- [x] reading_lists.html
- [x] error.html

### From `static/` folder:
- [x] css/style.css
- [x] js/app.js

### Reference files:
- [x] app_main.py (use as your new app/main.py)
- [x] root_main.py (reference for main.py if needed)

## Key Paths in app/main.py

```python
# Templates location
templates = Jinja2Templates(directory=str(BASE_DIR / "app" / "templates"))

# Static files location  
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
```

**BASE_DIR** = project root (one level up from app/main.py)

## Troubleshooting

**Templates not found?**
```bash
ls app/templates/  # Should list 8 HTML files
```

**Static files 404?**
```bash
ls static/css/style.css  # Should exist
ls static/js/app.js      # Should exist
```

**API broken?**
Check that app/main.py still has:
```python
app.include_router(comics.router, prefix="/api/comics", tags=["comics"])
```

## Need More Details?
See [INTEGRATION_GUIDE.md](./INTEGRATION_GUIDE.md) for complete instructions.

---

**That's it!** 5 steps and you're done. 🚀
