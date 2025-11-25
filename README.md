# Comic Server - HTMX Frontend

A modern, HTMX-based web frontend for your FastAPI comic server backend. Browse, read, and track your digital comic collection with a sleek interface.

## Features

### 📚 Library Management
- Browse all libraries
- Add new libraries
- Scan libraries for comics
- Automatic metadata parsing from ComicInfo.xml

### 📖 Comic Reader
- Full-screen immersive reading experience
- Keyboard navigation (Arrow keys, Space, Home, End)
- Click navigation (left/right sides of page)
- Multiple fit modes (Fit to Screen, Fit Width, Fit Height, Original Size)
- Reading progress tracking
- Page number display and quick navigation

### 🔍 Advanced Search
- Search by title, series, character
- Filter by publisher, year, format
- Search by writer, artist, team
- Complex filter combinations

### 📊 Reading Progress
- Continue reading from where you left off
- View in-progress comics
- Track recently read comics
- View completed comics
- Mark comics as read/unread

### 📂 Collections & Reading Lists
- Browse collections (grouped comics)
- View reading lists (ordered sequences)
- Perfect for story arcs and crossovers

## Technology Stack

- **Backend**: FastAPI
- **Frontend**: HTMX + Alpine.js + Tailwind CSS
- **Templates**: Jinja2
- **No build step required!**

### Why Alpine.js?

Alpine.js provides reactive components and state management with minimal overhead:
- **Reactive UI**: Modal dialogs, tabs, and dropdowns with smooth transitions
- **State Management**: View modes, filters, and form state
- **Clean Syntax**: Declarative `x-data`, `x-show`, `x-model` directives
- **Lightweight**: Only ~15KB minified
- **No Build Step**: Works directly in the browser via CDN

## Installation

### Prerequisites
```bash
- Python 3.8+
- Your existing FastAPI backend with the API endpoints
```

### Setup

1. **Install dependencies**:
```bash
pip install fastapi uvicorn jinja2 python-multipart
```

2. **Project structure**:
```
comic-server/
├── main.py                 # Main FastAPI app
├── api/                    # Your existing API modules
│   ├── comics.py
│   ├── libraries.py
│   ├── reader.py
│   ├── progress.py
│   ├── collections.py
│   └── reading_lists.py
├── templates/              # HTML templates
│   ├── base.html
│   ├── index.html
│   ├── reader.html
│   ├── search.html
│   ├── continue_reading.html
│   ├── collections.html
│   └── reading_lists.html
└── static/                 # Static assets
    ├── css/
    │   └── style.css
    └── js/
        └── app.js
```

3. **Run the server**:
```bash
python main.py
```

4. **Access the app**:
Open http://localhost:8000 in your browser

## API Endpoints Required

Your backend needs these endpoints (which you already have):

### Libraries
- `GET /api/libraries/` - List all libraries
- `POST /api/libraries/` - Create library
- `GET /api/libraries/{id}` - Get library details
- `DELETE /api/libraries/{id}` - Delete library
- `POST /api/libraries/{id}/scan` - Scan library

### Comics
- `GET /api/comics/` - List all comics
- `POST /api/comics/search` - Advanced search
- `GET /api/comics/{id}` - Get comic details
- `GET /api/comics/{id}/pages` - Get page list
- `GET /api/comics/{id}/page/{index}` - Get page image
- `GET /api/comics/{id}/thumbnail` - Get thumbnail

### Progress
- `GET /api/progress/{comic_id}` - Get progress
- `POST /api/progress/{comic_id}` - Update progress
- `POST /api/progress/{comic_id}/mark-read` - Mark as read
- `DELETE /api/progress/{comic_id}` - Clear progress
- `GET /api/progress/?filter={filter}` - Get filtered progress

### Collections
- `GET /api/collections/` - List collections
- `GET /api/collections/{id}` - Get collection details

### Reading Lists
- `GET /api/reading-lists/` - List reading lists
- `GET /api/reading-lists/{id}` - Get reading list details

## Usage

### Adding Comics

1. Click "Add Library" on the home page
2. Enter library name and path to your comics folder
3. Click "Scan" to import comics
4. Comics will be automatically organized by series

### Reading Comics

1. Click any comic thumbnail to open the reader
2. Navigate using:
   - **Arrow keys** or **A/D** - Previous/Next page
   - **Space** - Next page
   - **Home/End** - First/Last page
   - **F** - Toggle fullscreen
   - **Click** - Left side = previous, right side = next

### Searching

1. Go to Search page
2. Enter search criteria:
   - Text search (title, series)
   - Publisher filter
   - Year range
   - Format type
   - Character/Team names
   - Creator names
3. Click "Search"

### Tracking Progress

1. Progress is automatically saved as you read
2. View "Continue Reading" to see in-progress comics
3. Use progress bar to see completion percentage
4. Mark comics as read or clear progress

## Keyboard Shortcuts

### Global
- `Ctrl/Cmd + K` - Focus search
- `Ctrl/Cmd + /` - Go to search page
- `Escape` - Close modals

### Reader
- `Arrow Left` or `A` - Previous page
- `Arrow Right`, `D`, or `Space` - Next page
- `Home` - First page
- `End` - Last page
- `F` - Fullscreen

## Customization

### Styling
Edit `static/css/style.css` to customize colors and styles.

### Templates
All templates are in `templates/` directory. Modify as needed.

### Adding Features
The modular design makes it easy to add new features:
1. Add route in `main.py`
2. Create template in `templates/`
3. Add API calls in template JavaScript

## Browser Support

- Chrome/Edge (recommended)
- Firefox
- Safari
- Modern mobile browsers

## Performance Tips

1. **Thumbnails**: Thumbnails are cached for fast loading
2. **Lazy loading**: Comic covers load as you scroll
3. **CDN**: HTMX and Tailwind load from CDN

## Troubleshooting

### Comics not showing
- Verify library path is correct
- Check file permissions
- Run library scan

### Images not loading
- Check that image service is configured
- Verify comic files are not corrupted
- Check browser console for errors

### Search not working
- Ensure search endpoint is working
- Check request format in browser DevTools

## Contributing

Feel free to submit issues and enhancement requests!

## License

MIT License - feel free to use and modify as needed.

## Credits

Built with:
- [HTMX](https://htmx.org/) - High power tools for HTML
- [Tailwind CSS](https://tailwindcss.com/) - Utility-first CSS
- [FastAPI](https://fastapi.tiangolo.com/) - Modern Python web framework
