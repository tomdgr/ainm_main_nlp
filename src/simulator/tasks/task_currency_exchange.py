"""Task 26: Register payment in foreign currency and book exchange rate difference."""

import datetime
import re

from src.simulator.models import Check
from src.simulator.tasks.base import BaseTask


class CurrencyExchangeTask(BaseTask):
    """Tier 3 task: Register payment on a foreign currency invoice and
    book the exchange rate difference (agio/disagio).

    Setup creates a customer and an invoice in EUR.
    The agent must:
    1. Find the invoice
    2. Register payment at the new exchange rate
    3. Book the currency difference to account 8060 (agio) or 8160 (disagio)
    """

    name = "Currency Exchange (Agio/Disagio)"
    tier = 3
    optimal_calls = 6  # find invoice + accounts + paymentType + payment + voucher

    _CUSTOMER_ORG = "865432109"
    _CUSTOMER_NAME = "EuroTest GmbH"
    _EUR_AMOUNT = 5000
    _ORIG_RATE = 11.50  # NOK/EUR at invoice time
    _NEW_RATE = 11.80   # NOK/EUR at payment time (agio — customer pays more NOK)

    prompts = [
        (
            f"Vi sendte en faktura på {_EUR_AMOUNT} EUR til {_CUSTOMER_NAME} "
            f"(org.nr {_CUSTOMER_ORG}) da kursen var {_ORIG_RATE} NOK/EUR. "
            f"Kunden har nå betalt, men kursen er {_NEW_RATE} NOK/EUR. "
            f"Registrer betalingen og bokfør valutadifferansen (agio) på rett konto."
        ),
    ]

    def extract_expected(self, prompt: str) -> dict:
        result = {
            "customer_org": self._CUSTOMER_ORG,
            "customer_name": self._CUSTOMER_NAME,
            "eur_amount": self._EUR_AMOUNT,
            "orig_rate": self._ORIG_RATE,
            "new_rate": self._NEW_RATE,
        }

        org_match = re.search(r'\b(\d{9})\b', prompt)
        if org_match:
            result["customer_org"] = org_match.group(1)

        eur_match = re.search(r'(\d+)\s*EUR', prompt)
        if eur_match:
            result["eur_amount"] = int(eur_match.group(1))

        rates = re.findall(r'(\d+[.,]\d+)\s*NOK/EUR', prompt)
        if len(rates) >= 2:
            result["orig_rate"] = float(rates[0].replace(",", "."))
            result["new_rate"] = float(rates[1].replace(",", "."))

        # Calculate expected values
        result["orig_nok"] = result["eur_amount"] * result["orig_rate"]
        result["new_nok"] = result["eur_amount"] * result["new_rate"]
        result["difference"] = result["new_nok"] - result["orig_nok"]
        result["is_agio"] = result["difference"] > 0  # Gain if new_rate > orig_rate

        return result

    def setup(self, base_url: str, session_token: str, expected: dict):
        """Create customer with an invoice in EUR."""
        org_nr = expected["customer_org"]
        name = expected["customer_name"]
        eur_amount = expected["eur_amount"]
        orig_nok = expected["orig_nok"]

        print(f"  Setting up currency exchange task: {name}, {eur_amount} EUR @ {expected['orig_rate']}")

        # Ensure bank account
        resp = self._api(base_url, session_token, "GET", "/ledger/account", {
            "number": "1920", "fields": "id,version,bankAccountNumber",
        })
        accounts = resp.get("values", [])
        if accounts and not accounts[0].get("bankAccountNumber"):
            self._api(base_url, session_token, "PUT", f"/ledger/account/{accounts[0]['id']}", json_body={
                "id": accounts[0]["id"], "version": accounts[0]["version"],
                "bankAccountNumber": "12345678903",
            })

        # Create customer
        resp = self._api(base_url, session_token, "GET", "/customer", {
            "organizationNumber": org_nr, "fields": "id,name", "count": 1,
        })
        customers = resp.get("values", [])
        if customers:
            customer_id = customers[0]["id"]
        else:
            resp = self._api(base_url, session_token, "POST", "/customer", json_body={
                "name": name, "organizationNumber": org_nr, "isCustomer": True,
                "email": "test@eurotest.de", "invoiceEmail": "test@eurotest.de",
            })
            customer_id = resp.get("value", {}).get("id")
        print(f"  Customer: id={customer_id}")

        if not customer_id:
            print("  ERROR: Failed to create customer")
            return

        # Create order + invoice in NOK (we simulate the EUR invoice as NOK at the original rate)
        today = datetime.date.today().isoformat()
        resp = self._api(base_url, session_token, "POST", "/order", json_body={
            "customer": {"id": customer_id},
            "orderDate": today,
            "deliveryDate": today,
        })
        order_id = resp.get("value", {}).get("id")

        if order_id:
            self._api(base_url, session_token, "POST", "/order/orderline", json_body={
                "order": {"id": order_id},
                "description": f"Service {eur_amount} EUR @ {expected['orig_rate']}",
                "count": 1,
                "unitPriceExcludingVatCurrency": orig_nok,
                "vatType": {"id": 3},
            })

            resp = self._api(base_url, session_token, "PUT", f"/order/{order_id}/:invoice", params={
                "invoiceDate": today, "sendToCustomer": False,
            })
            invoice_id = resp.get("value", {}).get("id")
            expected["_invoice_id"] = invoice_id
            print(f"  Created invoice: id={invoice_id} ({orig_nok:.0f} NOK)")

    def check(self, verifier, expected: dict) -> list[Check]:
        checks = []
        difference = expected.get("difference", 0)
        is_agio = expected.get("is_agio", True)

        # Check 1: invoice payment registered
        invoice_id = expected.get("_invoice_id")
        if invoice_id:
            inv_resp = verifier.get(f"/invoice/{invoice_id}", {
                "fields": "id,amount,amountOutstanding",
            })
            inv = inv_resp.get("value", {})
            outstanding = inv.get("amountOutstanding", -1)
            checks.append(Check(
                name="Payment registered on invoice",
                passed=outstanding is not None and abs(outstanding) < 100,
                expected="outstanding ~0",
                actual=f"outstanding: {outstanding:.0f}" if outstanding else "N/A",
                points=3,
            ))
        else:
            checks.append(Check(
                name="Payment registered on invoice",
                passed=False,
                expected="invoice exists",
                actual="setup failed — no invoice ID",
                points=3,
            ))

        # Check 2: agio/disagio voucher exists
        # Look for posting on account 8060 (agio) or 8160 (disagio)
        target_acct = "8060" if is_agio else "8160"
        acct_resp = verifier.get("/ledger/account", {
            "number": target_acct, "fields": "id", "count": 1,
        })
        target_acct_id = acct_resp.get("values", [{}])[0].get("id") if acct_resp.get("values") else None

        has_exchange_voucher = False
        if target_acct_id:
            voucher_resp = verifier.get("/ledger/voucher", {
                "dateFrom": "2026-01-01", "dateTo": "2099-12-31", "count": 50,
            })
            for v in voucher_resp.get("values", [])[-10:]:
                v_detail = verifier.get(f"/ledger/voucher/{v['id']}", {"fields": "id,postings(*)"})
                for p in v_detail.get("value", {}).get("postings", []):
                    if p.get("account", {}).get("id") == target_acct_id:
                        has_exchange_voucher = True
                        break
                if has_exchange_voucher:
                    break

        agio_label = "agio (8060)" if is_agio else "disagio (8160)"
        checks.append(Check(
            name=f"Exchange rate voucher ({agio_label})",
            passed=has_exchange_voucher,
            expected=f"voucher with posting on {target_acct} for ~{abs(difference):.0f} NOK",
            actual="FOUND" if has_exchange_voucher else "NOT FOUND",
            points=3,
        ))

        return checks
