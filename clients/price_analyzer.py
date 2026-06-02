import sqlite3
import json
from pathlib import Path
from datetime import datetime
from clients.ai_client import AIClient
from clients.wc_client import WooCommerceClient

DB_PATH = Path(__file__).parent.parent / "price_history.db"

def get_competitor_prices(conn: sqlite3.Connection, product_name: str) -> list[dict]:
    """Pobierz ostatnie ceny konkurencji dla podobnych produktów."""
    rows = conn.execute("""
        SELECT cp.name, ph.price, ph.scraped_at
        FROM competitor_products cp
        JOIN price_history ph ON cp.id = ph.product_id
        WHERE ph.scraped_at = (
            SELECT MAX(ph2.scraped_at)
            FROM price_history ph2
            WHERE ph2.product_id = cp.id
        )
        ORDER BY ph.price ASC
        LIMIT 10
    """).fetchall()

    return [{"name": row[0], "price": row[1], "scraped_at": row[2]} for row in rows]

def save_recommendation(conn: sqlite3.Connection, product_id: int, product_name: str,
                         current_price: float, recommendation: dict):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS price_recommendations (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            product_id      INTEGER NOT NULL,
            product_name    TEXT NOT NULL,
            current_price   REAL NOT NULL,
            action          TEXT NOT NULL,
            suggested_price REAL NOT NULL,
            confidence      TEXT NOT NULL,
            reasoning       TEXT NOT NULL,
            market_position TEXT NOT NULL,
            created_at      TEXT NOT NULL,
            status          TEXT DEFAULT 'pending'
        )
    """)
    conn.execute("""
        INSERT INTO price_recommendations
        (product_id, product_name, current_price, action, suggested_price,
         confidence, reasoning, market_position, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        product_id,
        product_name,
        current_price,
        recommendation["action"],
        recommendation["suggested_price"],
        recommendation["confidence"],
        recommendation["reasoning"],
        recommendation["market_position"],
        datetime.now().isoformat(),
    ))
    conn.commit()

def build_analysis_message(product: dict, competitor_prices: list[dict]) -> str:
    prices = [c["price"] for c in competitor_prices]
    median = sorted(prices)[len(prices) // 2] if prices else 0
    min_price = min(prices) if prices else 0
    max_price = max(prices) if prices else 0

    competitors_text = "\n".join([
        f"  - {c['name'][:50]}: £{c['price']:.2f}"
        for c in competitor_prices[:8]
    ])

    return f"""
Produkt: {product['name']}
Nasza cena: {product['price']} PLN
Kategoria: {', '.join([c['name'] for c in product.get('categories', [])])}

Ceny konkurencji (ostatni scrape):
{competitors_text}

Statystyki konkurencji:
  Mediana: £{median:.2f}
  Min: £{min_price:.2f}
  Max: £{max_price:.2f}
  Liczba obserwacji: {len(prices)}
    """.strip()

def run():
    print("=== AI Price Analyzer ===\n")

    ai = AIClient()
    wc = WooCommerceClient()
    conn = sqlite3.connect(DB_PATH)

    products = wc.get_products(per_page=3)
    competitor_prices = get_competitor_prices(conn, "")

    print(f"Produkty WooCommerce: {len(products)}")
    print(f"Ceny konkurencji w bazie: {len(competitor_prices)}\n")

    for product in products:
        print(f"── {product['name']} (cena: {product['price']} PLN)")

        try:
            message = build_analysis_message(product, competitor_prices)
            recommendation = ai.generate_json(
                prompt_name="price_analysis",
                user_message=message,
                temperature=0.2,
            )

            save_recommendation(
                conn,
                product_id=product["id"],
                product_name=product["name"],
                current_price=float(product["price"]),
                recommendation=recommendation,
            )

            action_icon = {"lower": "📉", "maintain": "⚪", "raise": "📈"}.get(recommendation["action"], "?")
            print(f"  {action_icon} Akcja: {recommendation['action'].upper()}")
            print(f"  💰 Sugerowana cena: {recommendation['suggested_price']} PLN")
            print(f"  📊 Pewność: {recommendation['confidence']}")
            print(f"  💬 {recommendation['reasoning'][:100]}")

        except Exception as e:
            print(f"  ✗ Błąd: {e}")

    conn.close()
    print(f"\n✓ Rekomendacje zapisane do bazy")

if __name__ == "__main__":
    run()