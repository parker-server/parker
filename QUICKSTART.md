# Parker Comic Server

Parker is a self‑hosted comic book server for CBZ/CBR archives. It’s designed to be simple to run, easy to use, and powerful enough to organize large collections.

---

## 🚀 Quickstart

1. Get the docker image (recommended)

Parker publishes two Docker image channels:

- **Stable (recommended):**
The latest tag is built from versioned releases and is the recommended option for most users.

  ```bash
  docker run -d \
    -p 8000:8000 \
    -v /some/path/config:/app/storage \
    -v /some/path/comics:/comics \
    ghcr.io/parker-server/parker:latest
  ```
 
- **Edge**:
The edge tag is built automatically from every commit to master.
It includes the newest features and fixes, but may be less stable

  ```bash
  docker run -d \
    -p 8000:8000 \
    -v /some/path/config:/app/storage \
    -v /some/path/comics:/comics \
    ghcr.io/parker-server/parker:edge
  ```

2. Once up and running you can access Parker at http://localhost:8000.  The default username is ```admin``` and the password is ```admin```
3. Once logged in, navigate to the administration area at http://localhost:8000/admin
4. Click the 'Libraries' card and click the ```Add Library``` button
5. Enter a name and a valid path to the root of your comics folder. Note: if running on Windows, paths must be expressed with reverse slashes.
  Example: If your comic library resides at ```C:\Users\parker\MyComics```, you would enter ```C:/Users/parker/MyComics``` into the folder path box.
6. Click the ```Create Library``` button which will save the library.
7. You will see a row on the page representing your new library.  Click the ```Scan``` button and confirm to kick off your initial scan
8. The page will poll for the job to know when it's complete.  You can also review jobs on the 'Scan Jobs' card from the admin home.



#### If you prefer to get into the trenches you can instead directly clone the source code

1. Clone the repository:
   ```bash
   git clone https://github.com/parker-server/parker.git
   cd parker
   
2. Configure the docker-compose.yml with volume mappings, port, etc
3. ```docker-compose up -d --build```


## ✨ What You Get
- Browse comics by Library → Series → Volume → Issue
- Web Reader with manga mode, double‑page spreads, and swipe navigation
- Smart Lists and Pull Lists to organize your reading
- Reports Dashboard to spot missing issues, duplicates, and storage usage
- User accounts with library permissions
- Optional OPDS feed for external reader apps
- Optional WebP transcoding for faster remote reading

## 📌 Status- Current version: 1.1 (Stable)
- Core features are ready to use
- Expect ongoing updates and improvements

## 🤝 Contributing
Parker is open source and evolving. Feedback, bug reports, and pull requests are welcome!

## 📜 License
MIT License


