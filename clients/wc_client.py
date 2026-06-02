import requests
from requests.auth import HTTPBasicAuth
from dotenv import load_dotenv
import os
import json

load_dotenv()

class WooCommerceClient:
    def __init__(self):
        self.base_url = os.getenv("WC_URL")
        self.auth = HTTPBasicAuth(
            os.getenv("WC_USERNAME"),
            os.getenv("WC_APP_PASSWORD")
        )
        self.api_url = f"{self.base_url}/wp-json/wc/v3"

    def _get(self, endpoint: str, params: dict = {}) -> list | dict:
        url = f"{self.api_url}/{endpoint}"
        response = requests.get(url, auth=self.auth, params=params)
        response.raise_for_status()
        return response.json()

    def _post(self, endpoint: str, data: dict) -> dict:
        url = f"{self.api_url}/{endpoint}"
        response = requests.post(url, auth=self.auth, json=data)
        response.raise_for_status()
        return response.json()

    def _put(self, endpoint: str, data: dict) -> dict:
        url = f"{self.api_url}/{endpoint}"
        response = requests.put(url, auth=self.auth, json=data)
        response.raise_for_status()
        return response.json()

    # ── Produkty ──────────────────────────────────────────
    def get_products(self, per_page: int = 20, category: str = None) -> list:
        params = {"per_page": per_page, "status": "publish"}
        if category:
            params["category"] = category
        return self._get("products", params)

    def get_product(self, product_id: int) -> dict:
        return self._get(f"products/{product_id}")

    def update_product(self, product_id: int, data: dict) -> dict:
        return self._put(f"products/{product_id}", data)

    def update_products_batch(self, updates: list[dict]) -> dict:
        return self._post("products/batch", {"update": updates})

    # ── Kategorie ─────────────────────────────────────────
    def get_categories(self, per_page: int = 20) -> list:
        return self._get("products/categories", {"per_page": per_page})

    # ── Zamówienia ────────────────────────────────────────
    def get_orders(self, per_page: int = 20, status: str = None) -> list:
        params = {"per_page": per_page}
        if status:
            params["status"] = status
        return self._get("orders", params)
