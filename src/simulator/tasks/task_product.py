"""Task 03: Create product with number, price, and VAT type."""

import re

from src.simulator.models import Check
from src.simulator.tasks.base import BaseTask


class ProductTask(BaseTask):
    name = "Create Product"
    tier = 1
    optimal_calls = 2  # GET vatType + POST product

    prompts = [
        'Opprett produktet "Havregryn" med produktnummer 3113. Prisen er 29250 kr eksklusiv MVA, og MVA-sats for næringsmiddel på 15 % skal nyttast.',
        'Create the product "Web Hosting" with product number 5501. The price is 8500 NOK excluding VAT, with 25% standard VAT.',
        'Registre o produto "Consultoria" com número de produto 7220. Preço é 12000 NOK sem IVA, com IVA padrão de 25%.',
    ]

    def extract_expected(self, prompt: str) -> dict:
        result = {}

        # Extract product name (quoted)
        name_match = re.search(r'["\u201c]([^"\u201d]+)["\u201d]', prompt)
        if name_match:
            result["name"] = name_match.group(1)

        # Extract product number
        num_match = re.search(r'(?:produktnummer|product number|número de produto)\s+(\d+)', prompt, re.IGNORECASE)
        if num_match:
            result["number"] = num_match.group(1)

        # Extract price
        price_match = re.search(r'(\d[\d\s]*\d)\s*(?:kr|NOK)', prompt)
        if price_match:
            result["price"] = float(price_match.group(1).replace(" ", ""))

        # Extract VAT rate
        vat_match = re.search(r'(\d+)\s*%', prompt)
        if vat_match:
            result["vat_percent"] = int(vat_match.group(1))

        return result

    def check(self, verifier, expected: dict) -> list[Check]:
        checks = []
        product_number = expected.get("number", "")
        product_name = expected.get("name", "")
        price = expected.get("price", 0)

        # Find product by number
        resp = verifier.get("/product", {
            "number": product_number,
            "fields": "id,name,number,priceExcludingVatCurrency,vatType(*)",
            "count": 5,
        })
        products = resp.get("values", [])
        product = next(
            (p for p in products if str(p.get("number", "")) == product_number),
            None,
        )

        checks.append(Check(
            name="Product found",
            passed=product is not None,
            expected=f"number={product_number}",
            actual="FOUND" if product else "NOT FOUND",
            points=2,
        ))

        if not product:
            return checks

        # Check name
        checks.append(Check(
            name="Name matches",
            passed=product.get("name", "").lower() == product_name.lower(),
            expected=product_name,
            actual=product.get("name", ""),
        ))

        # Check price
        actual_price = product.get("priceExcludingVatCurrency", 0) or 0
        checks.append(Check(
            name="Price correct",
            passed=abs(actual_price - price) < 1,
            expected=str(price),
            actual=str(actual_price),
            points=2,
        ))

        # Check VAT type is set
        vat_type = product.get("vatType", {}) or {}
        checks.append(Check(
            name="VAT type set",
            passed=vat_type.get("id", 0) > 0,
            expected="VAT type assigned",
            actual=f"vatType id={vat_type.get('id', 0)}",
        ))

        return checks
