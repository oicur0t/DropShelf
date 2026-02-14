# DropShelf

A lightweight, secure OPDS 1.2 server for serving ebooks (EPUB, PDF, MOBI) over HTTPS.

## Features

- **OPDS 1.2 Compliant**: Standard catalog feeds for compatible ebook readers
- **Format Support**: EPUB, PDF, MOBI with automatic metadata extraction
- **Two-Phase Scanning**: Instant response with filename metadata, background enrichment
- **HTTP Basic Auth**: Secure catalog access with username/password authentication
- **Search & Browse**: Search by title/author, sort by recent added
- **Secure**: Path traversal protection, read-only mounts, HTTPS via reverse proxy
- **Caching**: Persistent disk-based cache with configurable TTL

## Quick Start

### 1. Clone and Configure

```bash
# Clone the repository
git clone https://github.com/oicur0t/DropShelf.git
cd DropShelf

# Copy environment template and edit
cp .env.example .env
nano .env  # Edit with your settings
```

### 2. Configure Environment (.env)

```bash
# Domain for OPDS access (without https://)
OPDS_DOMAIN=opds.example.com

# Books directory path on host
BOOKS_DIR_HOST=/path/to/your/books

# Container settings
BOOKS_DIR=/books
CACHE_TTL=300
MAX_RESULTS=50
LOG_LEVEL=INFO

# Feed metadata
FEED_TITLE=My Book Library
FEED_AUTHOR=DropShelf OPDS Server

# Traefik settings
TRAEFIK_NETWORK=traefik_network
CERT_RESOLVER=letsencrypt
```

### 3. Build and Deploy

```bash
# Ensure Traefik network exists (or create it)
docker network inspect ${TRAEFIK_NETWORK} >/dev/null 2>&1 || docker network create ${TRAEFIK_NETWORK}

# Build and start
docker compose up -d --build
```

### 4. Configure DNS

Add A record at your DNS provider: `OPDS_DOMAIN -> <your server IP>`

### 5. Test

```bash
# Test locally (use HOST_PORT from .env, default 45252)
curl http://localhost:${HOST_PORT}/health

# Test via domain (after DNS propagation)
curl https://${OPDS_DOMAIN}/health
curl https://${OPDS_DOMAIN}/opds
```

### 6. Add to Ebook Reader (KyBook 3, etc.)

**Without Authentication:**
1. Open your ebook reader app
2. Go to **Catalogs** → **Add Catalog**
3. Enter URL: `https://${OPDS_DOMAIN}/opds`
4. Browse and download books

**With Basic Auth (Recommended for KyBook 3):**
1. Open your ebook reader app
2. Go to **Catalogs** → **Add Catalog**
3. Enter URL with credentials: `https://username:password@${OPDS_DOMAIN}/opds`
4. Browse and download books

## Authentication

DropShelf supports HTTP Basic Authentication for securing your OPDS catalog. This is implemented at the application level, making it compatible with any reverse proxy (Traefik, Nginx, Caddy, etc.).

### Enabling Basic Auth

1. **Update .env:**
```bash
# Enable Basic Auth
AUTH_ENABLED=true
AUTH_USERNAME=your_username
AUTH_PASSWORD=your_password
```

2. **Restart container:**
```bash
docker compose down
docker compose up -d
```

### Using with KyBook 3

KyBook 3 supports credentials embedded in the OPDS URL:
```
https://username:password@your-domain.com/opds
```

When adding the catalog, use this format with your configured username and password.

### Testing Authentication

Test with curl:
```bash
curl -u username:password https://your-domain.com/opds
```

### Disabling Auth

To disable authentication, set `AUTH_ENABLED=false` in `.env` and restart the container.

## API Endpoints

| Endpoint | Description |
|----------|-------------|
| `GET /` | HTML landing page |
| `GET /opds` | Root OPDS navigation catalog |
| `GET /opds/all?page=N` | All books, paginated |
| `GET /opds/recent?page=N` | Recently added, sorted by mtime |
| `GET /opds/search?q=query` | Search by title/author |
| `GET /download/{filename}` | Download book file |
| `GET /health` | Health check |
| `POST /admin/cache/refresh` | Force cache rescan |
| `GET /admin/stats` | Server statistics |

## Configuration

Configuration is done via `.env` file:

| Variable | Default | Description |
|----------|---------|-------------|
| `OPDS_DOMAIN` | - | Domain name for OPDS access |
| `BOOKS_DIR_HOST` | - | Host path to books directory |
| `BOOKS_DIR` | `/books` | Container path to books |
| `CACHE_TTL` | `300` | Cache timeout in seconds |
| `MAX_RESULTS` | `50` | Books per page |
| `LOG_LEVEL` | `INFO` | Logging level |
| `FEED_TITLE` | `My Book Library` | OPDS feed title |
| `FEED_AUTHOR` | `DropShelf` | OPDS feed author |
| `AUTH_ENABLED` | `false` | Enable HTTP Basic Auth |
| `AUTH_USERNAME` | - | Username for authentication |
| `AUTH_PASSWORD` | - | Password for authentication |
| `TRAEFIK_NETWORK` | `traefik_network` | Docker network for Traefik |
| `CERT_RESOLVER` | `letsencrypt` | Traefik cert resolver name |

## Project Structure

```
├── app/
│   ├── main.py          # FastAPI application
│   ├── opds.py          # OPDS XML feed generation
│   ├── metadata.py      # EPUB/PDF metadata extraction
│   ├── scanner.py       # Directory scanning & caching
│   └── config.py        # Configuration
├── .env.example         # Environment template
├── docker-compose.yml
├── Dockerfile
├── requirements.txt
└── README.md
```

## Security

- **HTTP Basic Auth**: Optional username/password authentication for OPDS catalog
- **Path Traversal Protection**: All file paths validated to stay within books directory
- **Read-Only Mounts**: Server never writes to book directory
- **Non-Root User**: Container runs as unprivileged user
- **HTTPS Only**: Reverse proxy handles SSL termination

## Troubleshooting

### Books not appearing

```bash
# Check logs
docker logs opds

# Clear cache
curl -X POST https://${OPDS_DOMAIN}/admin/cache/clear

# Check book mount
docker exec opds ls /books
```

### Traefik connection issues

```bash
# Check if Traefik network exists
docker network inspect ${TRAEFIK_NETWORK}

# Verify network connectivity
docker network inspect ${TRAEFIK_NETWORK} | grep opds
```

### Performance tuning

For large libraries (>10k books):

- Increase `CACHE_TTL` to 600 or 900
- Increase `MAX_RESULTS` for fewer pages
- Scale workers: update Dockerfile CMD to `--workers 4`

## Development

```bash
# Run locally with Python
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export BOOKS_DIR=./test_books
python -m app.main
```

## License

MIT
