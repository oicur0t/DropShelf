"""Metadata extraction from EPUB, PDF, and MOBI files.

Supports:
- EPUB: Extract from META-INF/container.xml and OPF file
- PDF: Extract from document metadata
- MOBI: Fallback to filename parsing
"""
import os
import re
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from pathlib import Path
from zipfile import ZipFile

import xml.etree.ElementTree as ET


class BookMetadata:
    """Container for book metadata."""

    def __init__(
        self,
        title: str,
        author: str,
        filepath: Path,
        format: str,
        mtime: float,
        has_full_metadata: bool = False,
    ):
        self.title = title
        self.author = author
        self.filepath = filepath
        self.format = format
        self.mtime = mtime
        self.has_full_metadata = has_full_metadata

    @property
    def filename(self) -> str:
        """Original filename for download links."""
        return self.filepath.name

    @property
    def id(self) -> str:
        """Generate unique ID for OPDS entry."""
        return f"urn:uuid:{hash(str(self.filepath)) & 0xFFFFFFFFFFFFFFFF}"


def extract_epub_metadata(filepath: Path) -> dict[str, str | None]:
    """Extract title and author from EPUB file.

    Parses META-INF/container.xml to locate OPF file,
    then extracts metadata from OPF.

    Args:
        filepath: Path to EPUB file

    Returns:
        Dict with 'title' and 'author' keys (None if not found)
    """
    try:
        with ZipFile(filepath) as epub:
            # Read container.xml to find OPF path
            try:
                container_content = epub.read("META-INF/container.xml")
            except KeyError:
                return None

            container_tree = ET.fromstring(container_content)

            # Handle namespace
            ns = {"container": "urn:oasis:names:tc:opendocument:xmlns:container"}
            rootfile = container_tree.find(".//container:rootfile", ns)

            if rootfile is None:
                # Fallback to no namespace
                rootfile = container_tree.find(".//{urn:oasis:names:tc:opendocument:xmlns:container}rootfile")

            if rootfile is None or "full-path" not in rootfile.attrib:
                return None

            opf_path = rootfile.attrib["full-path"]

            # Read OPF file
            try:
                opf_content = epub.read(opf_path)
            except KeyError:
                return None

            opf_tree = ET.fromstring(opf_content)

            # Extract title - try multiple namespaces
            title = None
            for ns_tag in [
                ".//{http://purl.org/dc/elements/1.1/}title",
                ".//{http://www.w3.org/1999/02/22-rdf-syntax-ns#}title",
                ".//title",
                ".//dc:title",
            ]:
                elem = opf_tree.find(ns_tag)
                if elem is not None and elem.text:
                    title = elem.text.strip()
                    break

            # Extract author - try multiple tags
            author = None
            for ns_tag in [
                ".//{http://purl.org/dc/elements/1.1/}creator",
                ".//{http://www.w3.org/1999/02/22-rdf-syntax-ns#}creator",
                ".//creator",
                ".//dc:creator",
                ".//{http://purl.org/dc/elements/1.1/}author",
                ".//author",
                ".//dc:author",
            ]:
                elem = opf_tree.find(ns_tag)
                if elem is not None and elem.text:
                    author = elem.text.strip()
                    break

            return {"title": title, "author": author}

    except Exception:
        return None


def extract_pdf_metadata(filepath: Path) -> dict[str, str | None] | None:
    """Extract title and author from PDF metadata.

    This is a basic implementation. For production use with
    complex PDFs, consider using PyPDF2 or pypdf library.

    Args:
        filepath: Path to PDF file

    Returns:
        Dict with 'title' and 'author' keys (None if not found)
    """
    try:
        # Try importing pypdf if available
        try:
            from pypdf import PdfReader

            with open(filepath, "rb") as f:
                reader = PdfReader(f)
                metadata = reader.metadata

                if metadata:
                    title = metadata.get("/Title", "").strip() or None
                    author = metadata.get("/Author", "").strip() or None

                    if title or author:
                        return {"title": title, "author": author}
        except ImportError:
            pass

        # Fallback: no metadata available without pypdf
        return None

    except Exception:
        return None


def parse_filename(filename: str) -> dict[str, str]:
    """Parse book title and author from filename.

    Handles common patterns:
    - "Title by Author.epub"
    - "Title - Author.pdf"
    - "Author - Title.mobi"
    - "Title (Author).epub"
    - z-library IDs and various formats
    - UUID-only filenames

    Args:
        filename: Filename with extension

    Returns:
        Dict with 'title' and 'author' keys
    """
    # Remove extension
    name = Path(filename).stem

    # Remove common prefixes/suffixes from z-library
    # Pattern: "Author_Title (Z-LibraryID)" or "Title (Z-LibraryID)"
    zlib_pattern = r"\s*(?:\(z-?lib\.?org\))?$"
    name = re.sub(zlib_pattern, "", name, flags=re.IGNORECASE)

    # Try common separators first (before removing z-lib markers)
    patterns = [
        (r"^(.+?)\s+by\s+(.+)$", 1, 2),  # "Title by Author"
        (r"^(.+?)\s*-\s*(.+)$", 1, 2),   # "Title - Author"
        (r"^(.+?)\s*\((.+)\)$", 1, 2),   # "Title (Author)"
    ]

    for pattern, title_idx, author_idx in patterns:
        match = re.match(pattern, name, re.IGNORECASE)
        if match:
            groups = match.groups()
            if groups and len(groups) == 2 and groups[0] and groups[1]:
                return {
                    "title": groups[0].strip(),
                    "author": groups[1].strip(),
                }

    # Handle "Author_Title" format (common in z-library)
    # Split by underscore or dash, treat first as author if it looks like a name
    if "_" in name:
        parts = name.split("_", 1)
        if len(parts) == 2:
            # Check if first part looks like a name (short, or has Caps)
            first_part = parts[0]
            if len(first_part) < 30 or any(c.isupper() for c in first_part):
                return {
                    "title": parts[1].replace("-", " ").replace("_", " "),
                    "author": first_part.replace("-", " ").replace("_", " "),
                }

    # Check if it looks like a UUID/hex string (z-library ID)
    # If name is mostly hex chars, it's probably an ID
    if re.match(r'^[0-9a-f-]{32,}$', name.lower()):
        return {"title": "Unknown Title", "author": "Unknown Author"}

    # Remove common file cleanup patterns
    # Remove things like "z-lib.org", "(1)", etc.
    cleanup_patterns = [
        r'\s*\(z-?lib\.?org\)',
        r'\s*\(1\)',
        r'\s*\(2\)',
        r'\s*_z-lib\.org',
        r'\s*-\s*z-?lib\.?org',
    ]
    for pattern in cleanup_patterns:
        name = re.sub(pattern, '', name, flags=re.IGNORECASE)

    # Clean up extra whitespace
    name = re.sub(r'\s+', ' ', name).strip()

    # Return cleaned filename as title
    return {"title": name, "author": "Unknown"}


def _extract_with_timeout(func, filepath: Path, timeout: float = 0.5):
    """Extract metadata with timeout to avoid hanging on slow NFS.

    Args:
        func: Metadata extraction function
        filepath: Path to book file
        timeout: Timeout in seconds

    Returns:
        Extraction result or None if timeout
    """
    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(func, filepath)
            return future.result(timeout=timeout)
    except (FutureTimeoutError, Exception):
        return None


def get_metadata(filepath: Path) -> BookMetadata:
    """Extract metadata from any supported book format.

    Uses fast timeout to avoid hanging on slow NFS storage.
    Falls back to filename parsing if extraction times out.

    Args:
        filepath: Path to book file

    Returns:
        BookMetadata object with extracted or parsed metadata
    """
    suffix = filepath.suffix.lower()
    mtime = filepath.stat().st_mtime

    title = None
    author = None

    if suffix == ".epub":
        result = _extract_with_timeout(extract_epub_metadata, filepath, timeout=0.5)
        if result:
            title = result.get("title")
            author = result.get("author")

    elif suffix == ".pdf":
        result = _extract_with_timeout(extract_pdf_metadata, filepath, timeout=0.5)
        if result:
            title = result.get("title")
            author = result.get("author")

    # Fallback to filename parsing
    if not title:
        parsed = parse_filename(filepath.name)
        title = parsed["title"]
        author = parsed["author"]

    # Use "Unknown" for missing author
    if not author:
        author = "Unknown"

    return BookMetadata(
        title=title,
        author=author,
        filepath=filepath,
        format=suffix[1:].upper(),  # Remove dot, uppercase
        mtime=mtime,
    )
