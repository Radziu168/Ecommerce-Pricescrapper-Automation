import json
import os
from pathlib import Path
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()

PROMPTS_DIR = Path(__file__).parent.parent / "prompts"

class AIClient:
    def __init__(self, model: str = "gpt-4o-mini"):
        self.client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        self.model = model

    def _load_prompt(self, name: str) -> str:
        path = PROMPTS_DIR / f"{name}.txt"
        return path.read_text(encoding="utf-8")

    def generate_json(self, prompt_name: str, user_message: str, temperature: float = 0.5) -> dict:
        system_prompt = self._load_prompt(prompt_name)

        response = self.client.chat.completions.create(
            model=self.model,
            temperature=temperature,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ]
        )

        raw = response.choices[0].message.content.strip()
        tokens_used = response.usage.total_tokens
        cost = (tokens_used / 1_000_000) * 0.15  # gpt-4o-mini: $0.15/1M tokens

        print(f"  [AI] {tokens_used} tokenów | koszt: ${cost:.5f}")

        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            # Spróbuj wyciągnąć JSON z odpowiedzi
            start = raw.find('{')
            end = raw.rfind('}') + 1
            if start != -1 and end > start:
                return json.loads(raw[start:end])
            raise ValueError(f"AI nie zwróciło poprawnego JSON: {raw[:200]}")


if __name__ == "__main__":
    client = AIClient()

    result = client.generate_json(
        prompt_name="product_description",
        user_message="""
Nazwa: Ścierki z mikrofibry 5 szt.
Kategoria: Czyszczenie
Cena: 20 PLN
Cechy dodatkowe: zestaw 5 sztuk, różne kolory, do użytku domowego
        """.strip()
    )

    print("\n── Wynik ─────────────────────────────────")
    print(f"SEO title:    {result['seo_title']}")
    print(f"Short desc:   {result['short_desc']}")
    print(f"Bullet points:")
    for bp in result['bullet_points']:
        print(f"  • {bp}")
    print(f"Meta desc:    {result['meta_description']}")