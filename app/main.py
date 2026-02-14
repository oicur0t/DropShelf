"""DropShelf OPDS Server - FastAPI application.

Provides OPDS 1.2 feeds with two-phase scanning:
- Phase 1: Instant filename scan (~1s)
- Phase 2: Background metadata enrichment (~5 min)
"""
import html
import logging
import secrets
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from starlette.middleware.base import BaseHTTPMiddleware

from app.config import config
from app.metadata import BookMetadata
from app.scanner import get_scanner
from app.opds import (
    create_feed,
    create_root_catalog,
    feed_to_xml,
)

# Configure logging
logging.basicConfig(
    level=getattr(logging, config.LOG_LEVEL),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# Validate config on startup
try:
    config.validate()
except ValueError as e:
    logger.error(f"Configuration error: {e}")
    raise

# Create FastAPI app
app = FastAPI(
    title="DropShelf OPDS Server",
    description="OPDS 1.2 server with two-phase scanning",
    version="1.0.0",
)

class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Content-Security-Policy"] = "default-src 'self'; style-src 'self' 'unsafe-inline'"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        return response

app.add_middleware(SecurityHeadersMiddleware)

# Initialize scanner
scanner = get_scanner()

# HTTP Basic Auth security
security = HTTPBasic()


def verify_credentials(credentials: HTTPBasicCredentials = Depends(security)) -> HTTPBasicCredentials:
    """Verify HTTP Basic Auth credentials if auth is enabled."""
    if not config.AUTH_ENABLED:
        # Auth disabled, allow access
        return credentials

    if not config.AUTH_USERNAME or not config.AUTH_PASSWORD:
        # Auth enabled but no credentials configured - deny access
        raise HTTPException(
            status_code=401,
            detail="Authentication enabled but no credentials configured",
            headers={"WWW-Authenticate": "Basic"},
        )

    username_ok = secrets.compare_digest(credentials.username, config.AUTH_USERNAME)
    password_ok = secrets.compare_digest(credentials.password, config.AUTH_PASSWORD)
    if not (username_ok and password_ok):
        raise HTTPException(
            status_code=401,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Basic"},
        )

    return credentials


@app.on_event("startup")
async def startup_event():
    """Two-phase scan on startup."""
    logger.info("Startup: Initializing...")

    # Try to load from cache first
    books = scanner.load_cache()

    if books:
        logger.info(f"Startup: Loaded {len(books)} books from cache")
        # Start background enrichment
        scanner.start_background_enrichment()
    else:
        # Phase 1: Quick scan
        logger.info("Startup: Phase 1 - Quick filename scan...")
        books = scanner.phase1_quick_scan()
        logger.info(f"Startup: Phase 1 complete, {len(books)} books ready")

        # Start Phase 2: Background enrichment
        scanner.start_background_enrichment()


def get_base_url(request: Request) -> str:
    """Get base URL from request."""
    scheme = request.headers.get("X-Forwarded-Proto", request.url.scheme)
    host = request.headers.get("X-Forwarded-Host", request.headers.get("host", ""))
    return f"{scheme}://{host}"


@app.get("/", response_class=HTMLResponse)
async def root(request: Request, credentials: HTTPBasicCredentials = Depends(verify_credentials)) -> str:
    """Landing page."""
    base = get_base_url(request)
    status = scanner.get_enrichment_status()
    title = html.escape(config.FEED_TITLE)
    return f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>{title}</title>
        <style>
            body {{
                font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, sans-serif;
                max-width: 800px;
                margin: 40px auto;
                padding: 20px;
                line-height: 1.6;
            }}
            h1 {{ color: #333; }}
            .links {{ margin-top: 20px; }}
            .links a {{
                display: block;
                padding: 10px;
                margin: 5px 0;
                background: #f5f5f5;
                text-decoration: none;
                color: #0066cc;
                border-radius: 4px;
            }}
            .links a:hover {{ background: #e8e8e8; }}
            .status {{
                margin-top: 20px;
                padding: 10px;
                background: #e8f4f8;
                border-radius: 4px;
                font-size: 14px;
            }}
        </style>
    </head>
    <body>
        <h1>{title}</h1>
        <p>Welcome to the OPDS catalog. Add this feed to your favorite ebook reader.</p>

        <div class="status">
            <strong>Library Status:</strong><br>
            Total books: {status['total_books']}<br>
            With full metadata: {status['enriched_books']}<br>
            Enrichment in progress: {status['enriching']}
        </div>

        <div class="links">
            <a href="{base}/opds">📚 OPDS Root Catalog</a>
            <a href="{base}/opds/all">📖 All Books</a>
            <a href="{base}/opds/recent">🆕 Recently Added</a>
        </div>

        <h2>For ebook readers (KyBook 3, etc.)</h2>
        <p>Add this URL as an OPDS catalog:</p>
        <code style="background: #f5f5f5; padding: 5px;">{base}/opds</code>
    </body>
    </html>
    """


@app.get("/health")
async def health() -> dict[str, str]:
    """Health check endpoint."""
    return {"status": "ok", "message": "DropShelf OPDS Server is running"}


@app.get("/opds")
async def opds_root(request: Request, credentials: HTTPBasicCredentials = Depends(verify_credentials)) -> Response:
    """Root OPDS navigation catalog."""
    base = get_base_url(request)
    feed = create_root_catalog(base)
    xml = feed_to_xml(feed)
    return Response(content=xml, media_type="application/atom+xml;profile=opds-catalog;kind=navigation")


@app.get("/opds/all")
async def opds_all(
    request: Request,
    credentials: HTTPBasicCredentials = Depends(verify_credentials),
    page: int = Query(1, ge=1, description="Page number"),
) -> Response:
    """All books paginated."""
    base = get_base_url(request)
    offset = (page - 1) * config.MAX_RESULTS

    # Get books from scanner (instant - from cache)
    books = scanner.load_cache()

    # Sort by name
    books = sorted(books, key=lambda b: b.filename)
    total = len(books)

    # Paginate
    if offset:
        books = books[offset:]
    if config.MAX_RESULTS:
        books = books[:config.MAX_RESULTS]

    feed = create_feed(
        books=books,
        feed_title=f"All Books (Page {page})",
        feed_id=f"{base}/opds/all",
        base_url=base,
        page=page,
        total_results=total,
    )

    xml = feed_to_xml(feed)
    return Response(content=xml, media_type="application/atom+xml;profile=opds-catalog;kind=acquisition")


@app.get("/opds/recent")
async def opds_recent(
    request: Request,
    credentials: HTTPBasicCredentials = Depends(verify_credentials),
    page: int = Query(1, ge=1, description="Page number"),
) -> Response:
    """Recently added books sorted by modification time."""
    base = get_base_url(request)
    offset = (page - 1) * config.MAX_RESULTS

    # Get books from scanner
    books = scanner.load_cache()

    # Sort by mtime (newest first)
    books = sorted(books, key=lambda b: b.mtime, reverse=True)
    total = len(books)

    # Paginate
    if offset:
        books = books[offset:]
    if config.MAX_RESULTS:
        books = books[:config.MAX_RESULTS]

    feed = create_feed(
        books=books,
        feed_title=f"Recently Added (Page {page})",
        feed_id=f"{base}/opds/recent",
        base_url=base,
        page=page,
        total_results=total,
    )

    xml = feed_to_xml(feed)
    return Response(content=xml, media_type="application/atom+xml;profile=opds-catalog;kind=acquisition")


@app.get("/opds/search")
async def opds_search(
    request: Request,
    credentials: HTTPBasicCredentials = Depends(verify_credentials),
    q: str = Query(..., min_length=1, description="Search query"),
    page: int = Query(1, ge=1, description="Page number"),
) -> Response:
    """Search books by title or author."""
    base = get_base_url(request)
    offset = (page - 1) * config.MAX_RESULTS

    # Get books and filter
    books = scanner.load_cache()
    query_lower = q.lower()

    books = [
        b for b in books
        if query_lower in b.title.lower()
        or query_lower in b.author.lower()
    ]

    # Sort by name
    books = sorted(books, key=lambda b: b.filename)
    total = len(books)

    # Paginate
    if offset:
        books = books[offset:]
    if config.MAX_RESULTS:
        books = books[:config.MAX_RESULTS]

    feed = create_feed(
        books=books,
        feed_title=f"Search: {q} (Page {page})",
        feed_id=f"{base}/opds/search?q={q}",
        base_url=base,
        page=page,
        total_results=total,
        search_query=q,
    )

    xml = feed_to_xml(feed)
    return Response(content=xml, media_type="application/atom+xml;profile=opds-catalog;kind=acquisition")


@app.get("/download/{filename}")
async def download_book(filename: str, credentials: HTTPBasicCredentials = Depends(verify_credentials)) -> FileResponse:
    """Download a book file."""
    books_path = config.BOOKS_DIR.resolve()
    requested_path = (books_path / filename).resolve()

    if not requested_path.is_relative_to(books_path):
        raise FileNotFoundError("Invalid file path")

    if not requested_path.is_file():
        raise FileNotFoundError(f"File not found: {filename}")

    suffix = requested_path.suffix.lower()
    media_type = {
        ".epub": "application/epub+zip",
        ".pdf": "application/pdf",
        ".mobi": "application/x-mobipocket-ebook",
    }.get(suffix, "application/octet-stream")

    return FileResponse(
        path=requested_path,
        media_type=media_type,
        filename=requested_path.name,
    )


@app.get("/cover/{filename}")
async def get_cover(filename: str, credentials: HTTPBasicCredentials = Depends(verify_credentials)) -> Response:
    """Get cover image (placeholder for now)."""
    books_path = config.BOOKS_DIR.resolve()
    requested_path = (books_path / filename).resolve()

    if not requested_path.is_relative_to(books_path) or not requested_path.is_file():
        raise FileNotFoundError("Invalid file")

    # Return 1x1 transparent PNG placeholder
    placeholder_png = (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01"
        b"\x00\x00\x05\x00\x01\x0d\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
    )

    return Response(content=placeholder_png, media_type="image/png")


@app.post("/admin/cache/refresh")
async def admin_refresh_cache(credentials: HTTPBasicCredentials = Depends(verify_credentials)) -> dict[str, str]:
    """Force a cache refresh."""
    books = scanner.phase1_quick_scan()
    scanner.start_background_enrichment()
    logger.info(f"Cache refresh triggered: {len(books)} books")
    return {"status": "ok", "message": f"Cache refreshed with {len(books)} books"}


@app.get("/admin/stats")
async def admin_stats(credentials: HTTPBasicCredentials = Depends(verify_credentials)) -> dict[str, Any]:
    """Get server statistics."""
    status = scanner.get_enrichment_status()
    books = scanner.load_cache()

    format_counts: dict[str, int] = {}
    for book in books:
        fmt = book.format
        format_counts[fmt] = format_counts.get(fmt, 0) + 1

    return {
        "total_books": len(books),
        "formats": format_counts,
        "enrichment_status": status,
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=8080,
        reload=False,
        log_level=config.LOG_LEVEL.lower(),
    )
