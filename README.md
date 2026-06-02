# E-commerce Automation

Python pipeline: scraping cen konkurencji → analiza AI → opisy produktów → zapis do WooCommerce.

## Architektura

```
ecommerce-automation/
├── clients/
│   ├── wc_client.py                  ← WooCommerce REST API (GET/PUT produkty, zamówienia)
│   ├── ai_client.py                  ← OpenAI GPT client (generate_json z liczeniem tokenów)
│   └── product_description_pipeline.py  ← Generowanie AI opisów → zapis do WC meta_data
├── scrapers/
│   └── price_scraper.py              ← Playwright scraper + Pydantic + tenacity retry
├── prompts/
│   ├── product_description.txt       ← System prompt dla opisów produktów
│   └── price_analysis.txt            ← System prompt dla analizy cenowej
├── logs/                             ← Logi pipeline (auto-tworzone)
├── main.py                           ← Orchestrator CLI (typer)
├── price_history.db                  ← SQLite: competitor_products, price_history, price_recommendations
└── .env                              ← Klucze API
```

### Przepływ danych

```
CSV / harmonogram (APScheduler)
        │
        ▼
[1] Playwright scraper
    books.toscrape.com → competitor_products + price_history (SQLite)
        │
        ▼
[2] AI opisy produktów (OpenAI GPT-4o-mini)
    WooCommerce GET produkty → generate_json() → WooCommerce PUT meta_data
        │
        ▼
[3] Analiza cenowa AI
    WC produkty + ceny konkurencji → rekomendacja JSON → price_recommendations (SQLite)
        │
        ▼
POST /api/notify → Next.js SSE → dashboard odświeża rekomendacje
```

---

## Wymagania

- Python 3.11+
- Playwright Chromium (instalacja systemowa)
- WooCommerce z włączonym REST API
- Klucz OpenAI API

---

## Setup od zera

### 1. Klonuj repo i utwórz środowisko

```bash
git clone <repo-url> ecommerce-automation
cd ecommerce-automation

python -m venv venv
venv\Scripts\activate          # Windows
# source venv/bin/activate     # Linux/macOS
```

### 2. Zainstaluj zależności

```bash
pip install requests python-dotenv openai pydantic playwright tenacity typer
```

### 3. Zainstaluj przeglądarkę Playwright

```bash
playwright install chromium
```

> Jeśli Playwright był już zainstalowany na tym komputerze, przeglądarka jest współdzielona — ten krok można pominąć. Sprawdź: `playwright --version`

### 4. Zmienne środowiskowe

```bash
cp .env.example .env
```

Uzupełnij `.env`:

```env
# WooCommerce — Application Password
# WP Admin → Użytkownicy → Profil → Application Passwords → Dodaj nowe
WC_URL="http://twoj-sklep.local"
WC_USERNAME="twoja_nazwa_uzytkownika_wp"
WC_APP_PASSWORD="xxxx xxxx xxxx xxxx xxxx xxxx"

# OpenAI API
# platform.openai.com → API Keys → Create new key
# Ustaw alert na $5 w Billing → Usage limits
OPENAI_API_KEY="sk-..."

# Sekret webhooka — musi być identyczny jak NOTIFY_SECRET w Next.js
NOTIFY_SECRET="losowy_string_min_16_znakow"
```

### 5. Weryfikacja połączenia z WooCommerce

```bash
python clients/wc_client.py
```

Powinno wypisać produkty, kategorie i zamówienia z Twojego sklepu.

---

## Uruchamianie pipeline

### Pełny pipeline (wszystkie kroki)

```bash
python main.py --limit 10
```

### Dry-run (bez zapisu do WooCommerce)

```bash
python main.py --dry-run --limit 3
```

### Wybiórcze kroki

```bash
# Tylko scraping cen
python main.py --skip-ai-desc --skip-analysis

# Tylko opisy AI
python main.py --skip-scraper --skip-analysis

# Tylko analiza cenowa
python main.py --skip-scraper --skip-ai-desc
```

### Wszystkie opcje

```bash
python main.py --help
```

| Opcja | Domyślnie | Opis |
|-------|-----------|------|
| `--limit` | 3 | Liczba produktów WooCommerce do przetworzenia |
| `--dry-run` | False | Tryb testowy — bez zapisu do WooCommerce |
| `--skip-scraper` | False | Pomiń scraping cen konkurencji |
| `--skip-ai-desc` | False | Pomiń generowanie opisów AI |
| `--skip-analysis` | False | Pomiń analizę cenową AI |
| `--verbose` | False | Szczegółowe logi (DEBUG level) |

---

## Moduły

### `clients/wc_client.py` — WooCommerce REST API

```python
from clients.wc_client import WooCommerceClient

wc = WooCommerceClient()

# Pobierz produkty
products = wc.get_products(per_page=20)

# Zaktualizuj produkt
wc.update_product(product_id=15, data={"regular_price": "199.99"})

# Batch update
wc.update_products_batch([
    {"id": 15, "regular_price": "199.99"},
    {"id": 14, "regular_price": "18.99"},
])
```

> **Uwaga:** WooCommerce na LocalWP z nginx wymaga dodatkowej konfiguracji nginx do przekazywania nagłówka `Authorization`. Szczegóły w sekcji Znane problemy.

### `clients/ai_client.py` — OpenAI GPT

```python
from clients.ai_client import AIClient

ai = AIClient(model="gpt-4o-mini")  # domyślny model

result = ai.generate_json(
    prompt_name="product_description",  # ← plik prompts/product_description.txt
    user_message="Nazwa: Kubek termiczny\nCena: 89 PLN",
    temperature=0.5,
)
# result = {"short_desc": "...", "long_desc": "...", "bullet_points": [...], ...}
```

**Koszt:** GPT-4o-mini ~$0.00013 per opis produktu (≈900 tokenów).

### `scrapers/price_scraper.py` — Playwright

```python
import asyncio
from scrapers.price_scraper import scrape_all, init_db, upsert_product, save_price

conn = init_db()
products = asyncio.run(scrape_all(max_pages=2))  # 40 produktów

for product in products:
    product_id = upsert_product(conn, product)
    save_price(conn, product_id, product)
```

### Prompt versioning

Prompty są przechowywane jako pliki `.txt` w `prompts/`. Każda zmiana = commit z opisem efektu:

```bash
git add prompts/product_description.txt
git commit -m "prompt: dodano few-shot example dla kategorii Elektronika — +15% jakości"
```

---

## Baza danych (SQLite)

Plik: `price_history.db`

```sql
-- Produkty konkurencji
SELECT * FROM competitor_products;

-- Historia cen (każdy scrape = nowy rekord)
SELECT cp.name, ph.price, ph.scraped_at
FROM price_history ph
JOIN competitor_products cp ON cp.id = ph.product_id
ORDER BY ph.scraped_at DESC;

-- Rekomendacje cenowe AI
SELECT product_name, current_price, action, suggested_price, confidence, status
FROM price_recommendations
WHERE status = 'pending';
```

---

## Stack technologiczny

| Technologia | Wersja | Zastosowanie |
|-------------|--------|--------------|
| Python | 3.11+ | Runtime |
| OpenAI SDK | 1.x | GPT API |
| Playwright | 1.60+ | Headless scraping |
| Pydantic | 2.x | Walidacja danych |
| tenacity | 8.x | Retry z backoff |
| typer | 0.x | CLI |
| requests | 2.x | WooCommerce REST API |
| python-dotenv | 1.x | Zmienne środowiskowe |

---

## Znane problemy

### WooCommerce 401 Unauthorized na LocalWP (nginx)

Nginx domyślnie nie przekazuje nagłówka `Authorization` do PHP. Rozwiązanie — dodaj do bloku `location ~ \.php$` w `site.conf`:

```nginx
fastcgi_param   HTTP_AUTHORIZATION      $http_authorization;
fastcgi_pass_header Authorization;
```

Lokalizacja pliku: `C:\Users\<user>\AppData\Local\Local\sites\<site>\conf\nginx\site.conf`

Po zmianie zrestartuj stronę w LocalWP.

### Playwright na Windows — ścieżki

Playwright instaluje Chromium systemowo w `C:\Users\<user>\AppData\Local\ms-playwright\`. Jeśli `playwright install chromium` zostało już wcześniej uruchomione na tym komputerze — nie trzeba instalować ponownie.

### Koszty AI

Monitoruj zużycie na `platform.openai.com/usage`. Ustaw twardy limit w `Settings → Limits`. Orientacyjne koszty przy GPT-4o-mini:

| Operacja | Tokeny | Koszt |
|----------|--------|-------|
| Opis produktu | ~900 | $0.00013 |
| Analiza cenowa | ~600 | $0.00009 |
| Pełny pipeline (3 produkty) | ~4500 | $0.00075 |

---

## Deployment (produkcja)

### VPS (Hetzner CX22 ~4 EUR/mies)

```bash
# Zainstaluj Python i zależności
sudo apt update && sudo apt install python3.11 python3-pip -y
pip install -r requirements.txt
playwright install chromium --with-deps

# Utwórz systemd service
sudo nano /etc/systemd/system/ecommerce-pipeline.service
```

```ini
[Unit]
Description=E-commerce Automation Pipeline
After=network.target

[Service]
Type=oneshot
User=ubuntu
WorkingDirectory=/home/ubuntu/ecommerce-automation
ExecStart=/home/ubuntu/ecommerce-automation/venv/bin/python main.py --limit 20
EnvironmentFile=/home/ubuntu/ecommerce-automation/.env

[Install]
WantedBy=multi-user.target
```

```bash
# Cron co 6h
sudo systemctl enable ecommerce-pipeline.timer
journalctl -u ecommerce-pipeline -f  # podgląd logów
```
