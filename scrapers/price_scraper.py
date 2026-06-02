import asyncio
import sqlite3
import random
from datetime import datetime
from pathlib import Path
from playwright.async_api import async_playwright, TimeoutError as PWTimeout
from pydantic import BaseModel, field_validator
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

# ── Model danych ──────────────────────────────────────────
class CompetitorProduct(BaseModel):
    name: str
    price: float
    url: str
    in_stock: bool
    scraped_at: datetime = None
    source: str = "books.toscrape.com"

    @field_validator('price')
    @classmethod
    def price_must_be_positive(cls, v):
        if v <= 0:
            raise ValueError(f"Cena musi być dodatnia, otrzymano: {v}")
        return round(v, 2)

    @field_validator('name')
    @classmethod
    def name_must_not_be_empty(cls, v):
        if not v.strip():
            raise ValueError("Nazwa produktu nie może być pusta")
        return v.strip()

    def model_post_init(self, __context):
        if self.scraped_at is None:
            self.scraped_at = datetime.now()

# ── Baza danych ───────────────────────────────────────────
DB_PATH = Path(__file__).parent.parent / "price_history.db"

def init_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS competitor_products (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            name        TEXT NOT NULL,
            url         TEXT NOT NULL UNIQUE,
            source      TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS price_history (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            product_id  INTEGER NOT NULL,
            price       REAL NOT NULL,
            in_stock    INTEGER NOT NULL,
            scraped_at  TEXT NOT NULL,
            FOREIGN KEY (product_id) REFERENCES competitor_products(id)
        )
    """)
    conn.commit()
    print(f"Baza: {DB_PATH}")
    return conn

def upsert_product(conn: sqlite3.Connection, product: CompetitorProduct) -> int:
    existing = conn.execute(
        "SELECT id FROM competitor_products WHERE url = ?", (product.url,)
    ).fetchone()

    if existing:
        conn.execute(
            "UPDATE competitor_products SET name = ? WHERE url = ?",
            (product.name, product.url)
        )
        conn.commit()
        return existing[0]

    cursor = conn.execute(
        "INSERT INTO competitor_products (name, url, source) VALUES (?, ?, ?)",
        (product.name, product.url, product.source)
    )
    conn.commit()
    return cursor.lastrowid

def save_price(conn: sqlite3.Connection, product_id: int, product: CompetitorProduct):
    conn.execute(
        "INSERT INTO price_history (product_id, price, in_stock, scraped_at) VALUES (?, ?, ?, ?)",
        (product_id, product.price, int(product.in_stock), product.scraped_at.isoformat())
    )
    conn.commit()

def get_last_price(conn: sqlite3.Connection, product_id: int) -> float | None:
    row = conn.execute(
        "SELECT price FROM price_history WHERE product_id = ? ORDER BY scraped_at DESC LIMIT 1",
        (product_id,)
    ).fetchone()
    return row[0] if row else None

# ── Scraper z retry ───────────────────────────────────────
@retry(
    retry=retry_if_exception_type((PWTimeout, Exception)),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    reraise=True
)
async def scrape_page(page, url: str) -> list[CompetitorProduct]:
    """Scrapuje jedną stronę katalogu. Retry 3x z exponential backoff."""
    print(f"  Scrapuję: {url}")
    await page.goto(url, wait_until="domcontentloaded", timeout=30000)
    await page.wait_for_selector("article.product_pod", timeout=10000)
    await asyncio.sleep(random.uniform(0.5, 1.5))

    products = []
    books = await page.locator("article.product_pod").all()

    for book in books:
        try:
            name = await book.locator("h3 a").get_attribute("title")
            price_text = await book.locator(".price_color").inner_text()
            price = float(price_text.replace("£", "").replace("Â", "").strip())
            availability = await book.locator(".availability").inner_text()
            in_stock = "In stock" in availability
            href = await book.locator("h3 a").get_attribute("href")
            full_url = f"https://books.toscrape.com/catalogue/{href.replace('../', '')}"

            product = CompetitorProduct(
                name=name,
                price=price,
                url=full_url,
                in_stock=in_stock,
            )
            products.append(product)

        except Exception as e:
            print(f"  ⚠ Pominięto produkt: {e}")
            continue

    return products

async def scrape_all(max_pages: int = 2) -> list[CompetitorProduct]:
    all_products = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        )
        page = await context.new_page()

        for page_num in range(1, max_pages + 1):
            url = f"https://books.toscrape.com/catalogue/page-{page_num}.html"
            try:
                products = await scrape_page(page, url)
                all_products.extend(products)
                print(f"  ✓ Strona {page_num}: {len(products)} produktów")
            except Exception as e:
                print(f"  ✗ Strona {page_num} nieudana po 3 próbach: {e}")
                continue

        await browser.close()

    return all_products

# ── Main ──────────────────────────────────────────────────
async def main():
    print("=== Playwright Scraper v2 — Pydantic + tenacity ===\n")

    conn = init_db()
    products = await scrape_all(max_pages=2)

    print(f"\nPrzetwarzam {len(products)} produktów...\n")

    new_count = 0
    price_changes = 0

    for product in products:
        product_id = upsert_product(conn, product)
        last_price = get_last_price(conn, product_id)
        save_price(conn, product_id, product)

        if last_price is None:
            new_count += 1
        elif abs(product.price - last_price) > 0.01:
            price_changes += 1
            direction = "📉" if product.price < last_price else "📈"
            print(f"  {direction} {product.name[:40]:<40} {last_price:.2f} → {product.price:.2f}")

    conn.close()

    print(f"\n── Podsumowanie ──────────────────────────")
    print(f"  Nowe produkty:    {new_count}")
    print(f"  Zmiany cen:       {price_changes}")
    print(f"  Łącznie:          {len(products)}")
    print(f"  Baza:             {DB_PATH}")

if __name__ == "__main__":
    asyncio.run(main())