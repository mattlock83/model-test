"""URL identity and sitemap parsing, separate from browser execution."""

import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from ..config import parse_http_url


def canonical_url(value):
    parsed = parse_http_url(value, "discovery URL")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("Discovery URLs must not contain credentials")
    host = parsed.hostname.lower()
    if ":" in host:
        host = f"[{host}]"
    port = parsed.port
    if port and port != {"http": 80, "https": 443}[parsed.scheme]:
        host += f":{port}"
    # Queries identify different resources. Fragments identify locations within a page.
    return urlunsplit((parsed.scheme, host, parsed.path or "/", parsed.query, ""))


def origin(value):
    parsed = urlsplit(canonical_url(value))
    return parsed.scheme, parsed.netloc


class Scope:
    def __init__(self, seeds, allowed_urls=None):
        self.allowed_urls = None if allowed_urls is None else {canonical_url(u) for u in allowed_urls}
        self.origins = {origin(u) for u in seeds}

    def allows(self, url):
        try:
            normalized = canonical_url(url)
            return (
                normalized in self.allowed_urls
                if self.allowed_urls is not None
                else origin(normalized) in self.origins
            )
        except ValueError:
            return False

    def same_origin(self, url):
        try:
            return origin(url) in self.origins
        except ValueError:
            return False


def sitemap_urls(path):
    """Read a local urlset, accepting standard XML namespaces; never fetch other files."""
    with Path(path).open("rb") as stream:
        data = stream.read(10_000_001)
    if len(data) > 10_000_000:
        raise ValueError("Sitemap exceeds the 10 MB discovery limit")
    # No entity expansion, external resources or sitemap-index network traversal.
    data = data.decode("utf-8-sig")
    if "<!DOCTYPE" in data.upper() or "<!ENTITY" in data.upper():
        raise ValueError("Sitemap DTDs and entities are not supported")
    root = ET.fromstring(data)

    def local(tag):
        return tag.rsplit("}", 1)[-1]

    if local(root.tag) != "urlset":
        raise ValueError("Supply a sitemap urlset XML file; expand sitemap indexes separately")
    urls = []
    for entry in root:
        if local(entry.tag) != "url":
            continue
        locations = [child for child in entry if local(child.tag) == "loc"]
        if len(locations) != 1 or not locations[0].text:
            raise ValueError("Each sitemap URL must have exactly one loc")
        urls.append(canonical_url(locations[0].text.strip()))
    urls = list(dict.fromkeys(urls))
    if not urls:
        raise ValueError("Sitemap contains no page URLs")
    return urls
