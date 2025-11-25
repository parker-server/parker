# Quick Start Guide

Get your Comic Server up and running in 5 minutes!

## Prerequisites

- Python 3.8 or higher
- Your comic files (CBZ, CBR format)
- Comics should have ComicInfo.xml for metadata

## Installation

### Option 1: Quick Setup (Recommended)

1. **Clone or download the project**
```bash
cd comic-server
```

2. **Install dependencies**
```bash
pip install -r requirements.txt
```

3. **Copy environment file**
```bash
cp .env.example .env
# Edit .env with your settings
```

4. **Run the server**
```bash
python main.py
```

5. **Open your browser**
```
http://localhost:8000
```

### Option 2: Docker Setup

1. **Build and run with Docker Compose**
```bash
docker-compose up -d
```

2. **Access the application**
```
http://localhost:8000
```

## First-Time Setup

### 1. Add Your First Library

1. Click "Add Library" button
2. Enter a name (e.g., "My Comics")
3. Enter the path to your comics folder
   - Linux/Mac: `/home/user/Comics`
   - Windows: `C:\Users\YourName\Comics`
4. Click "Add Library"

### 2. Scan Your Comics

1. Click the "Scan" button next to your library
2. Wait for the scan to complete
3. Your comics will appear on the home page

### 3. Start Reading!

1. Click any comic cover to open the reader
2. Use keyboard shortcuts:
   - Arrow keys or Space to navigate
   - F for fullscreen
   - Escape to exit

## Directory Structure

```
comics/                    # Your comics folder
├── DC/
│   ├── Batman/
│   │   ├── Batman #1.cbz
│   │   └── Batman #2.cbz
│   └── Superman/
└── Marvel/
    └── Spider-Man/

data/                      # Application data (auto-created)
├── comics.db             # Database
└── cache/                # Thumbnail cache
```

## Comic File Requirements

### Supported Formats
- `.cbz` (ZIP archive with images)
- `.cbr` (RAR archive with images)
- `.cb7` (7-Zip archive with images)

### Metadata (ComicInfo.xml)
For best results, your comics should include a `ComicInfo.xml` file:

```xml
<?xml version="1.0"?>
<ComicInfo>
  <Title>The Amazing Spider-Man</Title>
  <Series>Amazing Spider-Man</Series>
  <Number>1</Number>
  <Year>1963</Year>
  <Writer>Stan Lee</Writer>
  <Penciller>Steve Ditko</Penciller>
  <Publisher>Marvel Comics</Publisher>
</ComicInfo>
```

Tools to add metadata:
- [ComicTagger](https://github.com/comictagger/comictagger)
- [Mylar](https://github.com/mylar3/mylar3)

## Common Issues

### Comics not showing up

**Problem**: Scanned library but no comics appear

**Solution**:
1. Check that your path is correct
2. Verify files are .cbz, .cbr, or .cb7
3. Check file permissions
4. Look at server logs for errors

### Images not loading

**Problem**: Comic covers are broken images

**Solution**:
1. Check that image files exist in comic archives
2. Verify file isn't corrupted (try opening manually)
3. Clear browser cache
4. Restart the server

### Slow performance

**Problem**: Comics load slowly

**Solution**:
1. Thumbnails will cache after first load
2. Check your storage speed
3. Reduce image quality in settings (future feature)
4. Use SSD storage if possible

## Configuration

Edit `.env` file to customize:

```env
# Change server port
PORT=9000

# Change comics directory
COMICS_PATH=/mnt/nas/comics

# Enable debug mode
LOG_LEVEL=DEBUG
```

## Keyboard Shortcuts

### Global
- `Ctrl/Cmd + K` - Focus search
- `Ctrl/Cmd + /` - Go to search
- `Escape` - Close modals

### Reader
- `←` / `A` - Previous page
- `→` / `D` / `Space` - Next page
- `Home` - First page
- `End` - Last page
- `F` - Fullscreen

## Next Steps

1. **Organize your library**: Use folders to organize by publisher/series
2. **Add metadata**: Use ComicTagger to add rich metadata
3. **Create reading lists**: Group related comics together
4. **Track progress**: Your reading position is saved automatically
5. **Search**: Use advanced search to find comics by character, writer, etc.

## Getting Help

- Check the full README.md for detailed documentation
- Look at server logs for error messages
- Verify your comic files are valid archives

## Tips for Best Experience

1. **Organize files logically**:
   ```
   Publisher/Series/Series Name #001.cbz
   ```

2. **Use consistent naming**:
   - Good: `Batman #001 (1940).cbz`
   - Bad: `batman1.cbz`

3. **Include metadata**: Comics with ComicInfo.xml are searchable and organizable

4. **Keep backups**: Your comics database is in `data/comics.db`

## Enjoy Reading! 📚

Your comic server is now ready to use. Happy reading!
