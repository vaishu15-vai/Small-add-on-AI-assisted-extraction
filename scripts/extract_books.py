"""Politely scrape Books to Scrape and extract structured fields from descriptions."""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import time
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from bs4 import BeautifulSoup


BASE_URL = "https://books.toscrape.com/"
USER_AGENT = "books-structure-demo/1.0 (educational; contact: example@example.com)"
FIELDS = [
    "title",
    "price_gbp",
    "rating",
    "availability",
    "description",
    "category",
    "audience",
    "themes",
    "suspicious_flags",
    "extraction_method",
]


def fetch(url: str, delay: float) -> str:
    """Fetch one page and wait before the next request."""
    request = Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urlopen(request, timeout=30) as response:
            body = response.read().decode("utf-8", errors="replace")
    except (HTTPError, URLError) as exc:
        raise RuntimeError(f"Could not fetch {url}: {exc}") from exc
    time.sleep(delay)
    return body


def rating_value(element: Any) -> int:
    classes = element.get("class", [])
    for value in classes:
        if value in {"One", "Two", "Three", "Four", "Five"}:
            return int({"One": 1, "Two": 2, "Three": 3, "Four": 4, "Five": 5}[value])
    return 0


def parse_listing(html: str) -> list[dict[str, Any]]:
    soup = BeautifulSoup(html, "html.parser")
    books: list[dict[str, Any]] = []
    for article in soup.select("article.product_pod"):
        link = article.select_one("h3 a")
        price = article.select_one(".price_color")
        availability = article.select_one(".availability")
        if not link or not price or not availability:
            continue
        stock_match = re.search(r"\d+", availability.get_text())
        books.append(
            {
                "url": BASE_URL + link["href"].replace("../", ""),
                "title": link.get("title", link.get_text(strip=True)),
                "price_gbp": float(re.sub(r"[^0-9.]", "", price.get_text())),
                "rating": rating_value(article.select_one(".star-rating")),
                "availability": int(stock_match.group()) if stock_match else 0,
            }
        )
    return books


def parse_detail(html: str) -> tuple[str, str, int]:
    soup = BeautifulSoup(html, "html.parser")
    description = soup.select_one("#product_description + p")
    breadcrumbs = soup.select("ul.breadcrumb li")
    category = breadcrumbs[-2].get_text(" ", strip=True) if len(breadcrumbs) > 1 else ""
    availability = soup.select_one(".availability")
    stock_match = re.search(r"\d+", availability.get_text()) if availability else None
    return (
        description.get_text(" ", strip=True) if description else "",
        category,
        int(stock_match.group()) if stock_match else 0,
    )


def heuristic_extract(description: str) -> dict[str, Any]:
    text = description.lower()
    audience = "adult" if any(word in text for word in ("woman", "man", "family", "girl", "boy")) else "general"
    keywords = [
        "love", "friendship", "family", "mystery", "adventure", "history",
        "war", "magic", "crime", "life", "death", "romance", "nature",
    ]
    themes = [word for word in keywords if word in text][:4] or ["general fiction"]
    return {"audience": audience, "themes": themes}


def llm_extract(description: str) -> dict[str, Any]:
    """Call an OpenAI-compatible Responses endpoint without adding an SDK dependency."""
    import urllib.request

    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not set")
    endpoint = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1/responses").rstrip("/")
    payload = {
        "model": os.environ.get("OPENAI_MODEL", "gpt-4o-mini"),
        "temperature": 0,
        "input": (
            "Extract JSON with exactly these keys: audience (short string) and themes "
            "(array of up to 5 short strings). Do not invent facts. Description:\n" + description
        ),
    }
    request = urllib.request.Request(
        endpoint,
        data=json.dumps(payload).encode(),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        result = json.loads(response.read().decode())
    text = result.get("output_text", "")
    if not text:
        for item in result.get("output", []):
            for content in item.get("content", []):
                if content.get("type") in {"output_text", "text"}:
                    text += content.get("text", "")
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", cleaned, flags=re.IGNORECASE)
    extracted = json.loads(cleaned)
    if not isinstance(extracted.get("audience"), str) or not isinstance(extracted.get("themes"), list):
        raise ValueError("LLM returned invalid field types")
    return extracted


def validate(row: dict[str, Any]) -> list[str]:
    flags: list[str] = []
    if not row["description"]:
        flags.append("missing_description")
    if row["price_gbp"] < 0:
        flags.append("negative_price")
    if row["rating"] not in range(1, 6):
        flags.append("rating_out_of_range")
    if row["availability"] < 0:
        flags.append("negative_availability")
    if not row["category"]:
        flags.append("missing_category")
    if not isinstance(row["audience"], str) or not isinstance(row["themes"], list):
        flags.append("invalid_extraction_types")
    return flags


def collect(pages: int, delay: float, use_llm: bool) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for page in range(1, pages + 1):
        listing_url = BASE_URL if page == 1 else f"{BASE_URL}catalogue/page-{page}.html"
        for book in parse_listing(fetch(listing_url, delay)):
            description, category, availability = parse_detail(fetch(book["url"], delay))
            method = "llm"
            try:
                extracted = llm_extract(description) if use_llm else heuristic_extract(description)
            except (RuntimeError, ValueError, json.JSONDecodeError, HTTPError, URLError) as exc:
                extracted = heuristic_extract(description)
                method = "heuristic_fallback"
                book["_extraction_error"] = type(exc).__name__
            book.update(
                {
                    "description": description,
                    "category": category,
                    "availability": availability,
                    "audience": extracted["audience"],
                    "themes": extracted["themes"],
                    "extraction_method": method if use_llm else "heuristic",
                }
            )
            flags = validate(book)
            if "_extraction_error" in book:
                flags.append("llm_error_fallback")
                del book["_extraction_error"]
            book["suspicious_flags"] = "|".join(flags)
            book["themes"] = "|".join(book["themes"])
            rows.append({field: book[field] for field in FIELDS})
    return rows


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pages", type=int, default=1)
    parser.add_argument("--delay", type=float, default=1.0)
    parser.add_argument("--use-llm", action="store_true")
    parser.add_argument("--output", default="data/books_structured.csv")
    args = parser.parse_args()
    if args.pages < 1:
        raise SystemExit("--pages must be at least 1")
    rows = collect(args.pages, args.delay, args.use_llm)
    if not rows:
        raise RuntimeError("No rows were collected")
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    print(f"Wrote {len(rows)} rows to {output}")


if __name__ == "__main__":
    main()
