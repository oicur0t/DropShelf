"""Two-phase scanner for instant response with progressive metadata enrichment.

Phase 1: Fast filename scan (instant, ~1s)
- Walk directory
- Parse metadata from filenames
- Save to cache
- Return immediately

Phase 2: Slow metadata enrichment (background, ongoing)
- Extract EPUB/PDF metadata
- Update cache progressively
- No user impact
"""

import json
import logging
import os
import time
import threading
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from pathlib import Path
from typing import Any

from app.config import config
from app.metadata import BookMetadata, get_metadata, parse_filename

logger = logging.getLogger(__name__)


class TwoPhaseScanner:
    """Two-phase scanner with instant response and background enrichment."""

    def __init__(self, cache_dir: Path, books_dir: Path):
        """Initialize scanner.

        Args:
            cache_dir: Directory for cache files
            books_dir: Directory containing books
        """
        self.cache_dir = cache_dir
        self.books_dir = books_dir
        self.cache_file = cache_dir / "metadata.json"
        self.enriching = False
        self.enrich_progress = {"total": 0, "processed": 0, "errors": 0}
        self._enrich_thread: threading.Thread | None = None
        self._cache: dict[str, dict[str, Any]] = {}
        self._cache_mtime: float = 0

    def load_cache(self) -> list[BookMetadata]:
        """Load cache from disk.

        Returns:
            List of BookMetadata objects
        """
        if self.cache_file.exists():
            try:
                with open(self.cache_file, "r") as f:
                    data = json.load(f)
                    self._cache = data.get("books", {})
                    self._cache_mtime = data.get("mtime", 0)
                    self.enrich_progress = data.get("enrich_progress", {"total": 0, "processed": 0, "errors": 0})

                # Convert dicts back to BookMetadata objects
                books = []
                for filename, metadata in self._cache.items():
                    books.append(BookMetadata(
                        title=metadata["title"],
                        author=metadata["author"],
                        filepath=self.books_dir / filename,
                        format=metadata["format"],
                        mtime=metadata["mtime"],
                        has_full_metadata=metadata.get("has_full_metadata", False),
                    ))
                return books
            except Exception as e:
                logger.error(f"Failed to load cache: {e}")

        return []

    def save_cache(self) -> None:
        """Save cache to disk."""
        self.cache_dir.mkdir(parents=True, exist_ok=True)

        data = {
            "mtime": time.time(),
            "enrich_progress": self.enrich_progress,
            "books": self._cache,
        }

        with open(self.cache_file, "w") as f:
            json.dump(data, f, indent=2, default=str)

    def phase1_quick_scan(self) -> list[BookMetadata]:
        """Phase 1: Fast filename scan.

        Walk directory and parse metadata from filenames only.
        Returns immediately (~1 second).

        Returns:
            List of BookMetadata with filename-parsed metadata
        """
        logger.info("Phase 1: Quick filename scan starting...")
        start_time = time.time()

        books = []
        try:
            # Use os.scandir for better performance than Path.rglob
            with os.scandir(self.books_dir) as entries:
                for entry in entries:
                    if entry.is_file() and Path(entry.name).suffix.lower() in config.ALLOWED_EXTENSIONS:
                        filepath = Path(entry.path)
                        mtime = entry.stat().st_mtime

                        # Parse metadata from filename
                        parsed = parse_filename(entry.name)

                        books.append(BookMetadata(
                            title=parsed["title"],
                            author=parsed["author"],
                            filepath=filepath,
                            format=Path(entry.name).suffix[1:].upper(),
                            mtime=mtime,
                            has_full_metadata=False,
                        ))

            logger.info(f"Phase 1 complete: {len(books)} books in {time.time() - start_time:.1f}s")

            # Save to cache
            for book in books:
                self._cache[book.filename] = {
                    "title": book.title,
                    "author": book.author,
                    "format": book.format,
                    "mtime": book.mtime,
                    "has_full_metadata": False,
                }
            self.save_cache()

            return books

        except Exception as e:
            logger.error(f"Phase 1 scan failed: {e}")
            return []

    def phase2_enrich_metadata(self) -> None:
        """Phase 2: Background metadata enrichment.

        Extract full metadata from EPUB/PDF files progressively.
        Updates cache as metadata becomes available.
        """
        if self.enriching:
            logger.info("Phase 2: Enrichment already running")
            return

        self.enriching = True
        self.enrich_progress = {"total": len(self._cache), "processed": 0, "errors": 0}

        logger.info("Phase 2: Starting metadata enrichment...")

        try:
            # Get books that need enrichment
            to_enrich = [
                (filename, meta) for filename, meta in self._cache.items()
                if not meta.get("has_full_metadata", False)
            ]

            self.enrich_progress["total"] = len(to_enrich)

            # Process with timeout for each file
            for filename, _ in to_enrich:
                filepath = self.books_dir / filename

                try:
                    # Extract with timeout
                    metadata = self._extract_with_timeout(get_metadata, filepath, timeout=0.3)

                    if metadata:
                        # Update cache with extracted metadata
                        self._cache[filename] = {
                            "title": metadata.title,
                            "author": metadata.author,
                            "format": metadata.format,
                            "mtime": metadata.mtime,
                            "has_full_metadata": True,
                        }
                        self.enrich_progress["processed"] += 1
                    else:
                        # Fallback to filename parsing
                        self.enrich_progress["errors"] += 1

                except Exception as e:
                    logger.debug(f"Failed to enrich {filename}: {e}")
                    self.enrich_progress["errors"] += 1

                # Save progress every 50 books
                if self.enrich_progress["processed"] % 50 == 0:
                    self.save_cache()
                    logger.info(f"Phase 2 progress: {self.enrich_progress['processed']}/{self.enrich_progress['total']} enriched")

            self.save_cache()
            logger.info(f"Phase 2 complete: {self.enrich_progress['processed']} enriched, {self.enrich_progress['errors']} errors")

        finally:
            self.enriching = False

    def _extract_with_timeout(self, func, filepath: Path, timeout: float = 0.5):
        """Extract metadata with timeout."""
        try:
            with ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(func, filepath)
                return future.result(timeout=timeout)
        except (FutureTimeoutError, Exception):
            return None

    def start_background_enrichment(self) -> None:
        """Start Phase 2 enrichment in background thread."""
        if self.enriching or self._enrich_thread:
            return

        def run_enrichment():
            self.phase2_enrich_metadata()

        self._enrich_thread = threading.Thread(target=run_enrichment, daemon=True)
        self._enrich_thread.start()
        logger.info("Phase 2: Background enrichment started")

    def get_enrichment_status(self) -> dict[str, Any]:
        """Get enrichment progress status.

        Returns:
            Dict with enrichment status
        """
        return {
            "enriching": self.enriching,
            "progress": self.enrich_progress,
            "total_books": len(self._cache),
            "enriched_books": sum(1 for m in self._cache.values() if m.get("has_full_metadata", False)),
        }


# Singleton instance
_scanner: TwoPhaseScanner | None = None


def get_scanner() -> TwoPhaseScanner:
    """Get singleton scanner instance."""
    global _scanner
    if _scanner is None:
        cache_dir = Path("/cache")
        _scanner = TwoPhaseScanner(cache_dir, config.BOOKS_DIR)
    return _scanner
