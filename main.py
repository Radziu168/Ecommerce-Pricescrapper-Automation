import typer
import sqlite3
import logging
from pathlib import Path
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

app = typer.Typer(help="E-commerce Automation Pipeline")

LOG_DIR = Path("logs")
DB_PATH = Path("price_history.db")

def setup_logging(verbose: bool = False):
    LOG_DIR.mkdir(exist_ok=True)
    log_file = LOG_DIR / f"pipeline_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"

    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.FileHandler(log_file, encoding="utf-8"),
            logging.StreamHandler(),
        ]
    )
    return logging.getLogger("pipeline")

@app.command()
def run(
    limit: int = typer.Option(3, "--limit", "-l", help="Liczba produktów do przetworzenia"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Tryb testowy — bez zapisu do WooCommerce"),
    skip_scraper: bool = typer.Option(False, "--skip-scraper", help="Pomiń scraping cen"),
    skip_ai_desc: bool = typer.Option(False, "--skip-ai-desc", help="Pomiń generowanie opisów"),
    skip_analysis: bool = typer.Option(False, "--skip-analysis", help="Pomiń analizę cenową"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Szczegółowe logi"),
):
    """Uruchom pełny pipeline e-commerce automation."""
    logger = setup_logging(verbose)
    start_time = datetime.now()

    logger.info("=" * 50)
    logger.info(f"START PIPELINE — {start_time.strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info(f"Limit: {limit} | Dry-run: {dry_run}")
    logger.info("=" * 50)

    stats = {
        "products_fetched": 0,
        "descriptions_generated": 0,
        "descriptions_saved": 0,
        "prices_scraped": 0,
        "recommendations_created": 0,
        "errors": 0,
    }

    # ── Krok 1: Scraping cen konkurencji ─────────────────
    if not skip_scraper:
        logger.info("\n[1/3] Scrapowanie cen konkurencji...")
        try:
            import asyncio
            from scrapers.price_scraper import scrape_all, init_db, upsert_product, save_price

            conn = init_db()
            products = asyncio.run(scrape_all(max_pages=1))

            for product in products:
                product_id = upsert_product(conn, product)
                save_price(conn, product_id, product)

            conn.close()
            stats["prices_scraped"] = len(products)
            logger.info(f"  ✓ Zescrapowano {len(products)} cen")
        except Exception as e:
            logger.error(f"  ✗ Błąd scrapera: {e}")
            stats["errors"] += 1
    else:
        logger.info("[1/3] Scraping pominięty (--skip-scraper)")

    # ── Krok 2: AI opisy produktów ────────────────────────
    if not skip_ai_desc:
        logger.info("\n[2/3] Generowanie AI opisów produktów...")
        try:
            import json
            from clients.wc_client import WooCommerceClient
            from clients.ai_client import AIClient
            from clients.product_description_pipeline import build_user_message

            wc = WooCommerceClient()
            ai = AIClient()
            wc_products = wc.get_products(per_page=limit)
            stats["products_fetched"] = len(wc_products)

            for product in wc_products:
                logger.info(f"  → {product['name']}")
                try:
                    description = ai.generate_json(
                        prompt_name="product_description",
                        user_message=build_user_message(product),
                        temperature=0.5,
                    )

                    assert len(description["meta_description"]) <= 160
                    assert len(description["bullet_points"]) == 5
                    stats["descriptions_generated"] += 1

                    if not dry_run:
                        wc.update_product(product["id"], {
                            "meta_data": [
                                {"key": "ai_short_desc", "value": description["short_desc"]},
                                {"key": "ai_long_desc", "value": description["long_desc"]},
                                {"key": "ai_bullet_points", "value": json.dumps(description["bullet_points"], ensure_ascii=False)},
                                {"key": "ai_seo_title", "value": description["seo_title"]},
                                {"key": "ai_meta_description", "value": description["meta_description"]},
                            ]
                        })
                        stats["descriptions_saved"] += 1
                        logger.info(f"    ✓ Zapisano do WooCommerce")
                    else:
                        logger.info(f"    ○ DRY-RUN — pominięto zapis")

                except Exception as e:
                    logger.error(f"    ✗ Błąd dla {product['name']}: {e}")
                    stats["errors"] += 1

        except Exception as e:
            logger.error(f"  ✗ Błąd modułu opisów: {e}")
            stats["errors"] += 1
    else:
        logger.info("[2/3] Generowanie opisów pominięte (--skip-ai-desc)")

    # ── Krok 3: Analiza cenowa ────────────────────────────
    if not skip_analysis:
        logger.info("\n[3/3] Analiza cenowa AI...")
        try:
            from clients.price_analyzer import (
                get_competitor_prices, build_analysis_message, save_recommendation
            )
            from clients.wc_client import WooCommerceClient
            from clients.ai_client import AIClient

            wc = WooCommerceClient()
            ai = AIClient()
            conn = sqlite3.connect(DB_PATH)

            wc_products = wc.get_products(per_page=limit)
            competitor_prices = get_competitor_prices(conn, "")

            for product in wc_products:
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
                    stats["recommendations_created"] += 1
                    action_icon = {"lower": "📉", "maintain": "⚪", "raise": "📈"}.get(recommendation["action"], "?")
                    logger.info(f"  {action_icon} {product['name']}: {recommendation['action'].upper()} → {recommendation['suggested_price']} PLN")
                except Exception as e:
                    logger.error(f"  ✗ Błąd analizy {product['name']}: {e}")
                    stats["errors"] += 1

            conn.close()
        except Exception as e:
            logger.error(f"  ✗ Błąd modułu analizy: {e}")
            stats["errors"] += 1
    else:
        logger.info("[3/3] Analiza cenowa pominięta (--skip-analysis)")

    # ── Podsumowanie ──────────────────────────────────────
    duration = (datetime.now() - start_time).total_seconds()
    logger.info("\n" + "=" * 50)
    logger.info("PODSUMOWANIE PIPELINE")
    logger.info("=" * 50)
    logger.info(f"  Produkty pobrane:        {stats['products_fetched']}")
    logger.info(f"  Opisy wygenerowane:      {stats['descriptions_generated']}")
    logger.info(f"  Opisy zapisane do WC:    {stats['descriptions_saved']}")
    logger.info(f"  Ceny zescrapowane:       {stats['prices_scraped']}")
    logger.info(f"  Rekomendacje cenowe:     {stats['recommendations_created']}")
    logger.info(f"  Błędy:                   {stats['errors']}")
    logger.info(f"  Czas wykonania:          {duration:.1f}s")
    logger.info("=" * 50)

def main():
    app()

if __name__ == "__main__":
    main()