"""File scanning and caching for book directory.

Efficiently scans book directory with configurable TTL caching.
Supports pagination and search filtering.
"""
import logging
import os
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from app.config import config
from app.metadata import BookMetadata, get_metadata

logger = logging.getLogger(__name__)


class BookCache:
    """Thread-safe cache for book metadata with TTL."""

    def __init__(self, ttl: int = 300):
        """Initialize cache with TTL in seconds.

        Args:
            ttl: Time-to-live for cache entries in seconds
        """
        self.ttl = ttl
        self._cache: list[BookMetadata] = []
        self._by_path: dict[Path, BookMetadata] = {}
        self._cache_time: float = 0
        self._scanning = False

    def is_valid(self) -> bool:
        """Check if cache is still valid."""
        return time.time() - self._cache_time < self.ttl

    def invalidate(self) -> None:
        """Force cache invalidation."""
        self._cache_time = 0

    def get_books(
        self,
        directory: Path,
        limit: int | None = None,
        offset: int = 0,
        search_query: str | None = None,
        sort_by: str = "name",
        reverse: bool = False,
    ) -> tuple[list[BookMetadata], int]:
        """Get books from cache, scanning if necessary.

        Args:
            directory: Directory to scan
            limit: Maximum number of results to return
            offset: Number of results to skip
            search_query: Optional search query for title/author
            sort_by: Sort field ('name', 'mtime', 'author', 'title')
            reverse: Reverse sort order

        Returns:
            Tuple of (books list, total count)
        """
        logger.info(f"get_books called: directory={directory}, limit={limit}, offset={offset}")

        # Check cache validity
        if not self.is_valid() or not self._cache:
            logger.info("Cache invalid or empty, triggering scan")
            self._scan_directory(directory)
        else:
            logger.info(f"Cache valid (age: {time.time() - self._cache_time:.0f}s)")

        # Filter by search query
        books = self._cache
        if search_query:
            query_lower = search_query.lower()
            books = [
                b for b in books
                if query_lower in b.title.lower()
                or query_lower in b.author.lower()
            ]

        # Sort
        sort_key = {
            "name": lambda b: b.filename,
            "mtime": lambda b: b.mtime,
            "author": lambda b: b.author.lower(),
            "title": lambda b: b.title.lower(),
        }.get(sort_by, lambda b: b.filename)

        books = sorted(books, key=sort_key, reverse=reverse)

        total = len(books)

        # Paginate
        if offset:
            books = books[offset:]
        if limit:
            books = books[:limit]

        return books, total

    def get_book_by_filename(self, directory: Path, filename: str) -> BookMetadata | None:
        """Get specific book by filename.

        Args:
            directory: Books directory
            filename: Filename to look up

        Returns:
            BookMetadata or None if not found
        """
        # Ensure cache is populated
        if not self.is_valid() or not self._cache:
            self._scan_directory(directory)

        return self._by_path.get(directory / filename)

    def _scan_directory(self, directory: Path) -> None:
        """Scan directory and populate cache.

        Uses parallel processing for metadata extraction.

        Args:
            directory: Directory to scan
        """
        if self._scanning:
            # Wait for ongoing scan
            while self._scanning:
                time.sleep(0.1)
            return

        self._scanning = True
        logger.info(f"Starting scan of {directory}")

        try:
            # Find all supported book files
            book_files = []
            for entry in directory.rglob("*"):
                if entry.is_file() and entry.suffix.lower() in config.ALLOWED_EXTENSIONS:
                    book_files.append(entry)

            total = len(book_files)
            logger.info(f"Found {total} book files to scan")

            # Extract metadata (single worker for NFS to avoid contention)
            books = []
            processed = 0
            with ThreadPoolExecutor(max_workers=1) as executor:
                # Submit all tasks
                futures = {executor.submit(get_metadata, f): f for f in book_files}

                # Collect results as they complete
                for future in futures:
                    try:
                        books.append(future.result())
                        processed += 1
                        if processed % 100 == 0 or processed == total:
                            logger.info(f"Scanned {processed}/{total} books")
                    except Exception:
                        # Skip files that fail metadata extraction
                        pass

            self._cache = books
            self._by_path = {b.filepath: b for b in books}
            self._cache_time = time.time()
            logger.info(f"Scan complete: {len(books)} books cached")

        finally:
            self._scanning = False


# Singleton cache instance
_cache: BookCache | None = None


def get_cache() -> BookCache:
    """Get singleton cache instance."""
    global _cache
    if _cache is None:
        _cache = BookCache(ttl=config.CACHE_TTL)
    return _cache


def scan_directory(
    directory: Path,
    limit: int | None = None,
    offset: int = 0,
    search_query: str | None = None,
    sort_by: str = "name",
    reverse: bool = False,
) -> tuple[list[BookMetadata], int]:
    """Scan directory and return paginated books.

    Args:
        directory: Directory to scan
        limit: Maximum results to return
        offset: Number of results to skip
        search_query: Optional search query
        sort_by: Sort field
        reverse: Reverse sort order

    Returns:
        Tuple of (books list, total count)
    """
    cache = get_cache()
    return cache.get_books(directory, limit, offset, search_query, sort_by, reverse)


def get_book_by_filename(directory: Path, filename: str) -> BookMetadata | None:
    """Get specific book by filename.

    Args:
        directory: Books directory
        filename: Filename to look up

    Returns:
        BookMetadata or None if not found
    """
    cache = get_cache()
    return cache.get_book_by_filename(directory, filename)


def clear_cache() -> None:
    """Clear the cache forcing a rescan."""
    cache = get_cache()
    cache.invalidate()
