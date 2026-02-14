"""OPDS 1.2 feed generation.

Generates OPDS (Open Publication Distribution System) 1.2 compliant XML feeds.
Supports pagination, search, and acquisition links.
"""
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import xml.etree.ElementTree as ET

from app.config import config
from app.metadata import BookMetadata


# OPDS namespace
OPDS_NS = "http://opds-spec.org/2010/catalog"
ATOM_NS = "http://www.w3.org/2005/Atom"


def register_namespaces() -> None:
    """Register XML namespaces for pretty printing."""
    ET.register_namespace("", ATOM_NS)
    ET.register_namespace("opds", OPDS_NS)


def get_mime_type(format: str) -> str:
    """Get MIME type for book format.

    Args:
        format: Book format (EPUB, PDF, MOBI)

    Returns:
        MIME type string
    """
    mime_types = {
        "EPUB": "application/epub+zip",
        "PDF": "application/pdf",
        "MOBI": "application/x-mobipocket-ebook",
    }
    return mime_types.get(format.upper(), "application/octet-stream")


def generate_feed_id() -> str:
    """Generate unique ID for the feed."""
    return f"urn:uuid:{uuid.uuid4()}"


def format_timestamp(timestamp: float) -> str:
    """Format Unix timestamp as RFC 3339 datetime.

    Args:
        timestamp: Unix timestamp

    Returns:
        RFC 3339 formatted datetime string
    """
    dt = datetime.fromtimestamp(timestamp, tz=timezone.utc)
    return dt.isoformat()


def create_element(tag: str, text: str | None = None, **attribs: Any) -> ET.Element:
    """Create XML element with optional text and attributes.

    Args:
        tag: Element tag name
        text: Optional text content
        **attribs: Optional XML attributes

    Returns:
        ElementTree Element
    """
    el = ET.Element(tag, attribs)
    if text:
        el.text = text
    return el


def create_link(
    rel: str,
    href: str,
    type_: str | None = None,
    title: str | None = None,
) -> ET.Element:
    """Create an OPDS/Atom link element.

    Args:
        rel: Link relationship
        href: Link URL
        type_: Optional MIME type
        title: Optional link title

    Returns:
        Link Element
    """
    attribs = {"rel": rel, "href": href}
    if type_:
        attribs["type"] = type_
    if title:
        attribs["title"] = title
    return create_element("link", **attribs)


def create_entry(book: BookMetadata, base_url: str = "") -> ET.Element:
    """Create OPDS entry for a book.

    Args:
        book: BookMetadata object
        base_url: Base URL for link generation

    Returns:
        OPDS entry Element
    """
    entry = ET.Element("entry")

    # Title
    title = create_element("title", book.title)
    entry.append(title)

    # ID
    id_elem = create_element("id", book.id)
    entry.append(id_elem)

    # Author
    author = ET.Element("author")
    name = create_element("name", book.author)
    author.append(name)
    entry.append(author)

    # Updated
    updated = create_element("updated", format_timestamp(book.mtime))
    entry.append(updated)

    # Acquisition link (download)
    download_url = f"{base_url}/download/{book.filename}"
    acquisition = create_link(
        rel="http://opds-spec.org/acquisition",
        href=download_url,
        type_=get_mime_type(book.format),
    )
    entry.append(acquisition)

    # Thumbnail link (placeholder - could be enhanced with cover extraction)
    thumbnail = create_link(
        rel="http://opds-spec.org/image/thumbnail",
        href=f"{base_url}/cover/{book.filename}",
        type_="image/png",
    )
    entry.append(thumbnail)

    return entry


def create_feed(
    books: list[BookMetadata],
    feed_title: str,
    feed_id: str,
    base_url: str = "",
    page: int = 1,
    total_results: int = 0,
    search_query: str | None = None,
) -> ET.Element:
    """Create OPDS feed element.

    Args:
        books: List of BookMetadata objects
        feed_title: Title for this feed
        feed_id: Unique ID for this feed
        base_url: Base URL for link generation
        page: Current page number
        total_results: Total number of results (for pagination)
        search_query: Optional search query that generated this feed

    Returns:
        OPDS feed ElementTree Element
    """
    # Root feed element with namespaces
    feed = ET.Element(
        "feed",
        {
            "xmlns": ATOM_NS,
            "xmlns:opds": OPDS_NS,
        },
    )

    # Feed ID
    feed_id_elem = create_element("id", feed_id)
    feed.append(feed_id_elem)

    # Feed title
    title = create_element("title", feed_title)
    feed.append(title)

    # Feed updated
    updated = create_element("updated", format_timestamp(time.time()))
    feed.append(updated)

    # Feed author
    author = ET.Element("author")
    name = create_element("name", config.FEED_AUTHOR)
    author.append(name)
    feed.append(author)

    # Pagination links
    max_per_page = config.MAX_RESULTS
    total_pages = (total_results + max_per_page - 1) // max_per_page if total_results else 1

    if page > 1:
        # Previous page link
        prev_url = f"{base_url}/opds/all?page={page - 1}"
        if search_query:
            prev_url = f"{base_url}/opds/search?q={search_query}&page={page - 1}"
        feed.append(create_link("previous", prev_url, "application/atom+xml;profile=opds-catalog;kind=acquisition"))

    if page < total_pages:
        # Next page link
        next_url = f"{base_url}/opds/all?page={page + 1}"
        if search_query:
            next_url = f"{base_url}/opds/search?q={search_query}&page={page + 1}"
        feed.append(create_link("next", next_url, "application/atom+xml;profile=opds-catalog;kind=acquisition"))

    # Self link
    self_url = f"{base_url}/opds/all?page={page}"
    if search_query:
        self_url = f"{base_url}/opds/search?q={search_query}&page={page}"
    feed.append(create_link("self", self_url, "application/atom+xml;profile=opds-catalog;kind=acquisition"))

    # Start link (root catalog)
    feed.append(create_link(
        "start",
        f"{base_url}/opds",
        "application/atom+xml;profile=opds-catalog;kind=navigation"
    ))

    # Add entries for each book
    for book in books:
        entry = create_entry(book, base_url)
        feed.append(entry)

    return feed


def create_root_catalog(base_url: str = "") -> ET.Element:
    """Create root OPDS navigation catalog.

    Args:
        base_url: Base URL for link generation

    Returns:
        OPDS navigation feed Element
    """
    feed = ET.Element(
        "feed",
        {
            "xmlns": ATOM_NS,
            "xmlns:opds": OPDS_NS,
        },
    )

    feed_id_elem = create_element("id", generate_feed_id())
    feed.append(feed_id_elem)

    title = create_element("title", config.FEED_TITLE)
    feed.append(title)

    updated = create_element("updated", format_timestamp(time.time()))
    feed.append(updated)

    # Author
    author = ET.Element("author")
    name = create_element("name", config.FEED_AUTHOR)
    author.append(name)
    feed.append(author)

    # Self link
    feed.append(create_link(
        "self",
        f"{base_url}/opds",
        "application/atom+xml;profile=opds-catalog;kind=navigation"
    ))

    # Navigation entries

    # All books
    all_entry = ET.Element("entry")
    all_entry.append(create_element("title", "All Books"))
    all_entry.append(create_element("id", f"{base_url}/opds/all"))
    all_entry.append(create_element("content", "Browse all books in the library"))
    all_entry.append(create_link(
        "subsection",
        f"{base_url}/opds/all",
        "application/atom+xml;profile=opds-catalog;kind=acquisition"
    ))
    feed.append(all_entry)

    # Recent books
    recent_entry = ET.Element("entry")
    recent_entry.append(create_element("title", "Recently Added"))
    recent_entry.append(create_element("id", f"{base_url}/opds/recent"))
    recent_entry.append(create_element("content", "Books sorted by most recently added"))
    recent_entry.append(create_link(
        "subsection",
        f"{base_url}/opds/recent",
        "application/atom+xml;profile=opds-catalog;kind=acquisition"
    ))
    feed.append(recent_entry)

    # Search
    search_entry = ET.Element("entry")
    search_entry.append(create_element("title", "Search"))
    search_entry.append(create_element("id", f"{base_url}/opds/search"))
    search_entry.append(create_element("content", "Search books by title or author"))
    search_link = create_link(
        "search",
        f"{base_url}/opds/search",
        "application/opensearchdescription+xml"
    )
    search_link.set("opds:facet", "title,author")
    search_entry.append(search_link)
    feed.append(search_entry)

    return feed


def feed_to_xml(feed: ET.Element) -> str:
    """Convert Element feed to XML string.

    Args:
        feed: ElementTree feed element

    Returns:
        XML string with declaration
    """
    # Register namespaces first
    register_namespaces()

    # Convert to string
    xml_str = ET.tostring(feed, encoding="unicode", xml_declaration=True)

    return xml_str
