# Integration Guide - Your Project Structure

## Your Exact Structure

```
project/
├── main.py                      # Entry point (you have this)
├── start.bat                    # Windows start script (you have this)
├── start.sh                     # Linux/Mac start script (you have this)
├── Dockerfile                   # Docker config (you have this)
├── docker-compose.yml           # Docker Compose (you have this)
├── app/
│   ├── main.py                  # FastAPI app (UPDATE THIS)
│   ├── api/                     # Your API routes (you have these)
│   │   ├── comics.py
│   │   ├── libraries.py
│   │   ├── reader.py
│   │   ├── progress.py
│   │   ├── collections.py
│   │   └── reading_lists.py
│   └── templates/               # ADD HTML FILES HERE (NEW)
├── static/                      # ADD CSS/JS HERE (already exists)
│   ├── css/                     # ADD style.css
│   └── js/                      # ADD app.js
└── storage/                     # Your storage (you have this)
    ├── cache/
    ├── covers/
    └── database/
```

## Step-by-Step Integration

### Step 1: Extract the Zip File

Extract `comic-server-frontend-alpine.zip` to a temporary location.

### Step 2: Copy Template Files

From the zip, copy all HTML files from `templates/` to your `app/templates/`:

```bash
# In your project directory
cp /path/to/extracted/templates/*.html app/templates/
```

You should have:
```
app/templates/
├── base.html
├── index.html
├── reader.html
├── search.html
├── continue_reading.html
├── collections.html
├── reading_lists.html
└── error.html
```

### Step 3: Copy Static Files

From the zip, copy CSS and JS files to your `static/` directory:

```bash
# In your project directory
cp /path/to/extracted/static/css/style.css static/css/
cp /path/to/extracted/static/js/app.js static/js/
```

You should have:
```
static/
├── css/
│   └── style.css
└── js/
    └── app.js
```

### Step 4: Update Your app/main.py

**IMPORTANT:** This is the main change you need to make.

Replace your existing `app/main.py` with the provided `app_main.py` file, OR manually add the frontend routes.

#### Option A: Replace (Recommended)

1. **Backup your current app/main.py:**
   ```bash
   cp app/main.py app/main.py.backup
   ```

2. **Copy the new app/main.py:**
   ```bash
   cp app_main.py app/main.py
   ```

3. **Verify imports match your project:**
   Make sure this line matches your actual imports:
   ```python
   from app.api import comics, libraries, reader, progress, collections, reading_lists
   ```

#### Option B: Manual Update (If you have custom code)

If your `app/main.py` has custom middleware or configuration, add these sections:

**1. Add imports at the top:**
```python
from pathlib import Path
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse
from fastapi.exceptions import HTTPException
from fastapi.middleware.cors import CORSMiddleware
```

**2. After creating your `app = FastAPI(...)`, add:**
```python
# Get directories relative to this file
BASE_DIR = Path(__file__).resolve().parent.parent

# Mount static files from project root
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")

# Setup templates from app/templates/
templates = Jinja2Templates(directory=str(BASE_DIR / "app" / "templates"))

# Add CORS if needed
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Exception handlers
@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    if "text/html" in request.headers.get("accept", ""):
        return templates.TemplateResponse(
            "error.html",
            {"request": request, "status_code": exc.status_code, "detail": exc.detail},
            status_code=exc.status_code
        )
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": exc.detail}
    )
```

**3. Add frontend routes at the end of the file:**
```python
# Frontend routes
@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

@app.get("/reader/{comic_id}", response_class=HTMLResponse)
async def reader(request: Request, comic_id: int):
    return templates.TemplateResponse("reader.html", {
        "request": request,
        "comic_id": comic_id
    })

@app.get("/search", response_class=HTMLResponse)
async def search(request: Request):
    return templates.TemplateResponse("search.html", {"request": request})

@app.get("/collections", response_class=HTMLResponse)
async def collections_view(request: Request):
    return templates.TemplateResponse("collections.html", {"request": request})

@app.get("/reading-lists", response_class=HTMLResponse)
async def reading_lists_view(request: Request):
    return templates.TemplateResponse("reading_lists.html", {"request": request})

@app.get("/continue-reading", response_class=HTMLResponse)
async def continue_reading(request: Request):
    return templates.TemplateResponse("continue_reading.html", {"request": request})
```

### Step 5: Verify Your Root main.py

Your root `main.py` should look like this:

```python
if __name__ == "__main__":
    import uvicorn
    
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=8000,
        reload=True
    )
```

If it's different, you can use the provided `root_main.py` as a reference.

### Step 6: Update requirements.txt

Add these if not already present:

```txt
jinja2>=3.1.2
aiofiles>=23.2.1
```

Install:
```bash
pip install jinja2 aiofiles
```

## Final Directory Structure

After integration, you should have:

```
project/
├── main.py                      # Entry point
├── start.bat
├── start.sh
├── Dockerfile
├── docker-compose.yml
├── app/
│   ├── main.py                  # ✅ UPDATED with frontend routes
│   ├── api/
│   │   ├── comics.py
│   │   ├── libraries.py
│   │   ├── reader.py
│   │   ├── progress.py
│   │   ├── collections.py
│   │   └── reading_lists.py
│   └── templates/               # ✅ NEW - All HTML files
│       ├── base.html
│       ├── index.html
│       ├── reader.html
│       ├── search.html
│       ├── continue_reading.html
│       ├── collections.html
│       ├── reading_lists.html
│       └── error.html
├── static/                      # ✅ UPDATED with CSS/JS
│   ├── css/
│   │   └── style.css           # ✅ NEW
│   └── js/
│       └── app.js              # ✅ NEW
└── storage/
    ├── cache/
    ├── covers/
    └── database/
```

## Running Your Server

### Using Your Existing Scripts

Your existing scripts should work:

```bash
# Linux/Mac
./start.sh

# Windows
start.bat
```

Or directly:
```bash
python main.py
```

### Docker

If using Docker, update your Dockerfile to include static and templates:

```dockerfile
# Add these lines to your Dockerfile
COPY static/ /app/static/
COPY app/templates/ /app/app/templates/
```

## Testing the Integration

### 1. Start the server
```bash
python main.py
```

### 2. Test the API (should still work)
- http://localhost:8000/docs - API documentation
- http://localhost:8000/api/comics/ - Comics API

### 3. Test the frontend (NEW!)
- http://localhost:8000 - Home page (library browser)
- http://localhost:8000/search - Search interface
- http://localhost:8000/collections - Collections browser
- http://localhost:8000/reading-lists - Reading lists

### 4. Check static files
Open browser DevTools (F12) → Network tab
- Verify `/static/css/style.css` loads (200 status)
- Verify `/static/js/app.js` loads (200 status)

## Troubleshooting

### Templates Not Found
**Error:** `TemplateNotFound: index.html`

**Check:**
```bash
ls app/templates/  # Should show all HTML files
```

**Fix in app/main.py:**
```python
templates = Jinja2Templates(directory=str(BASE_DIR / "app" / "templates"))
```

### Static Files 404
**Error:** 404 on `/static/css/style.css`

**Check:**
```bash
ls static/css/  # Should show style.css
ls static/js/   # Should show app.js
```

**Fix in app/main.py:**
```python
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
```

### API Stopped Working
**Check:** Make sure your API router includes are still there:
```python
app.include_router(comics.router, prefix="/api/comics", tags=["comics"])
app.include_router(libraries.router, prefix="/api/libraries", tags=["libraries"])
# ... etc
```

### Path Issues
If you get path errors, check the `BASE_DIR` calculation in `app/main.py`:
```python
# This should point to project root
BASE_DIR = Path(__file__).resolve().parent.parent
print(BASE_DIR)  # Should print: /path/to/project
```

## Quick Verification Checklist

- [ ] All HTML files in `app/templates/`
- [ ] CSS/JS files in `static/css/` and `static/js/`
- [ ] `app/main.py` updated with frontend routes
- [ ] Dependencies installed (`pip install jinja2 aiofiles`)
- [ ] Server starts without errors
- [ ] http://localhost:8000 shows home page
- [ ] http://localhost:8000/docs shows API docs
- [ ] Static files load (check browser DevTools)
- [ ] Can navigate between pages

## Summary of Changes

### What Changed
1. ✅ Added 8 HTML templates to `app/templates/`
2. ✅ Added CSS and JS to `static/`
3. ✅ Updated `app/main.py` with frontend routes
4. ✅ Added Jinja2 and aiofiles dependencies

### What Stayed the Same
1. ✅ All your API routes work exactly as before
2. ✅ Your database, models, and business logic unchanged
3. ✅ Your start scripts still work
4. ✅ Your Docker setup (with minor updates)

## Next Steps

Once everything is running:

1. **Open** http://localhost:8000
2. **Add a library** using the "Add Library" button
3. **Scan your comics** to populate the database
4. **Browse and read** your collection!

---

**You're all set!** Your comic server now has a beautiful Alpine.js-powered frontend. 🎉

Questions? Check:
- [ALPINE_GUIDE.md](./ALPINE_GUIDE.md) - How Alpine.js works
- [README.md](./README.md) - Full documentation
- Browser console (F12) for frontend errors
- Server logs for backend errors
