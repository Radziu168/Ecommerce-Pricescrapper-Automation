import json
import os
from dotenv import load_dotenv
from clients.wc_client import WooCommerceClient
from clients.ai_client import AIClient

load_dotenv()

def build_user_message(product: dict) -> str:
    name = product.get("name", "")
    price = product.get("price", "")
    categories = ", ".join([c["name"] for c in product.get("categories", [])]) or "Inne"
    short_desc = product.get("short_description", "")

    return f"""
Nazwa: {name}
Kategoria: {categories}
Cena: {price} PLN
Cechy dodatkowe: {short_desc or "brak"}
    """.strip()

def run(dry_run: bool = False, limit: int = 3):
    wc = WooCommerceClient()
    ai = AIClient()

    print(f"=== AI Product Description Pipeline ===")
    print(f"Tryb: {'DRY-RUN' if dry_run else 'LIVE'} | Limit: {limit} produktów\n")

    products = wc.get_products(per_page=limit)
    print(f"Pobrano {len(products)} produktów z WooCommerce\n")

    results = []
    total_cost = 0.0

    for product in products:
        print(f"── {product['name']} (ID: {product['id']})")

        try:
            user_message = build_user_message(product)
            description = ai.generate_json(
                prompt_name="product_description",
                user_message=user_message,
                temperature=0.5
            )

            # Walidacja
            assert len(description["meta_description"]) <= 160, "meta_description za długa"
            assert len(description["bullet_points"]) == 5, "bullet_points musi mieć 5 elementów"
            assert len(description["short_desc"]) <= 160, "short_desc za długa"

            results.append({
                "id": product["id"],
                "name": product["name"],
                "description": description,
            })

            print(f"  ✓ Wygenerowano opis")

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
                print(f"  ✓ Zapisano do WooCommerce")
            else:
                print(f"  ○ DRY-RUN — pominięto zapis")

        except AssertionError as e:
            print(f"  ✗ Walidacja nieudana: {e}")
        except Exception as e:
            print(f"  ✗ Błąd: {e}")

    print(f"\n── Podsumowanie ───────────────────────────")
    print(f"  Przetworzono: {len(results)}/{len(products)}")
    print(f"  Tryb: {'DRY-RUN' if dry_run else 'LIVE — zapisano do WooCommerce'}")

if __name__ == "__main__":
    # Najpierw dry-run
    run(dry_run=False, limit=3)