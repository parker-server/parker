Welcome to Parker! This guide will help you get your personal comic server up and running using Docker.

### Prerequisites
- Docker installed on your host machine.
- A directory containing your comic collection (currently supporting .cbz and .cbr).
- (Optional) Docker Compose for easier management.

## Channel selection
Parker publishes two Docker image channels:

- **Stable (recommended):**
The latest tag is built from versioned releases and is the recommended option for most users.

`parker:latest`

- **Edge**:
The edge tag is built automatically from every commit to master.
It includes the newest features and fixes, but may be less stable.

`parker:edge`


### Quick Start (Docker Run)
If you want to test Parker quickly, run the following command. Replace the paths on the left side of the `:` with your actual local directories:

Generate an initial admin password first and keep it somewhere safe until you
log in:

```bash
export PARKER_INITIAL_ADMIN_PASSWORD="$(openssl rand -base64 24)"
echo "$PARKER_INITIAL_ADMIN_PASSWORD"
```

```bash
docker run -d \
  --name parker \
  -p 8000:8000 \
  -v /path/to/config:/app/storage \
  -v /path/to/comics:/comics:ro \
  -e BASE_URL=/ \
  -e COMICS_PATH=/comics \
  -e SECRET_KEY="$(openssl rand -hex 32)" \
  -e INITIAL_ADMIN_PASSWORD="$PARKER_INITIAL_ADMIN_PASSWORD" \
  ghcr.io/parker-server/parker:latest
```

Parker listens on port `8000` inside the container. To use a different host port, change the left side of the port mapping. For example, `-p 9000:8000` exposes Parker at `http://localhost:9000`.

### Advanced Configuration via CLI
You can configure any setting from the .env file directly in your docker run command using the -e flag. This is particularly useful for setting up your environment without manually editing files.

Example: Running on a subpath with Proxy Trust

```bash
docker run -d \
  --name parker \
  -p 8000:8000 \
  -e BASE_URL=/comics \
  -e COMICS_PATH=/comics \
  -e SECRET_KEY="$(openssl rand -hex 32)" \
  -e INITIAL_ADMIN_PASSWORD="$PARKER_INITIAL_ADMIN_PASSWORD" \
  -e TRUSTED_PROXIES="172.18.0.1,192.168.1.50" \
  -e ALLOWED_ORIGINS="https://comics.yourdomain.com" \
  -v /path/to/config:/app/storage \
  -v /path/to/comics:/comics:ro \
  ghcr.io/parker-server/parker:latest
```

### Recommended Setup (Docker Compose)
For a more permanent installation, use a `docker-compose.yml` file. This allows you to manage environment variables in a dedicated `.env` file.

docker-compose.yml

```yml
services:
  parker:
    image: ghcr.io/parker-server/parker:latest
    container_name: parker
    ports:
      - "${PARKER_PORT:-8000}:8000"
    env_file: .env  # Loads your configuration
    volumes:
      - /path/to/config:/app/storage
      - /path/to/comics:/comics:ro
    restart: unless-stopped
```

Example .env
```
PARKER_PORT=8000
BASE_URL=/
COMICS_PATH=/comics
# Generate a unique value first: openssl rand -hex 32
SECRET_KEY=
# Optional; defaults to admin
INITIAL_ADMIN_USERNAME=admin
# Required only before Parker creates the first administrator
INITIAL_ADMIN_PASSWORD=
```

### Initial Configuration
Parker requires `SECRET_KEY` to be set to a unique random value before startup. It will refuse to start if the key is empty or still uses a known placeholder.

For a new database, Parker also requires `INITIAL_ADMIN_PASSWORD` so it can
create the first administrator without using a shared default password.
`INITIAL_ADMIN_USERNAME` defaults to `admin` if omitted. These values are
ignored after an active administrator exists.

Once the container is running, access the web UI at http://localhost:8000, or the host port you mapped with `PARKER_PORT` / `-p`.

Admin Account: log in with the bootstrap username and password you configured before first startup.

Once logged in, navigate to the administration area at `/admin`, such as `http://localhost:8000/admin`.

Click the 'Libraries' card and click the `Add Library` button.

Use `Browse` to select a folder under the configured `COMICS_PATH` root. For the Docker examples above, that root is `/comics`.

If entering a path manually, use the path inside the container, such as `/comics` or `/comics/DC`, not the host path from the left side of the Docker volume mapping.

Click the `Create Library` button which will save the library.

You will see a row on the page representing your new library.  Click the `Scan` button and confirm to kick off your initial scan.

The page will poll for the job to know when it's complete.  You can also review jobs on the 'Scan Jobs' card from the admin home.


### Building from source

If you prefer to get into the trenches you can instead directly clone the source code

1. Clone the repository:

   ```bash
   git clone https://github.com/parker-server/parker.git
   cd parker
   ```
   
2. Configure the `docker-compose.yml` with volume mappings, port, etc as explained in the above sections

3. ```bash
   docker-compose up -d --build
   ```

