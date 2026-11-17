#!/usr/bin/env python3
"""Check the public authoritative links actually emitted in both docs versions."""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sys
import urllib.request
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]

def approved(url):
    parts = urlparse(url)
    return (parts.scheme == "https" and not parts.username and not parts.password and not parts.port and
            (parts.hostname == "docs.rs" or
             (parts.hostname == "github.com" and (parts.path == "/pubky/paykit-rs" or parts.path.startswith("/pubky/paykit-rs/")))))

def check(url):
    if not approved(url):
        return {"url": url, "status": None, "error": "Unapproved public documentation link"}
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "paykit-public-docs-link-check/1"})
        with urllib.request.urlopen(request, timeout=30) as response:
            return {"url": url, "status": response.status, "finalUrl": response.url}
    except Exception as error:
        return {"url": url, "status": getattr(error, "code", None), "error": str(error)}

def main():
    urls = set()
    for version in ("released", "coming-soon"):
        document = json.loads((ROOT / f"content/docs/{version}.json").read_text())
        urls.add(document["version"]["sourceUrl"])
        urls.add(document["version"]["readiness"]["sourceUrl"])
        for page in document["pages"]:
            urls.update(link["href"] for link in page["sourceLinks"])
            for section in page["sections"]:
                urls.update(link["href"] for link in section.get("links", []) if link["href"].startswith("https:"))
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(check, sorted(urls)))
    destination = ROOT / ".automation/external-links.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(results, indent=2) + "\n")
    failures = [result for result in results if result["status"] != 200]
    print(json.dumps({"checked": len(results), "failed": failures}, indent=2))
    return 1 if failures else 0

if __name__ == "__main__":
    sys.exit(main())
