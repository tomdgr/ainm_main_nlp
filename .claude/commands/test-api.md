Test Tripletex API endpoints directly to verify playbook assumptions before deploying.

## When to use

Use this skill when:
- Researching how a Tripletex API endpoint behaves (field names, required fields, response format)
- Verifying that a playbook's recommended API flow actually works
- Debugging why a task scores 0 or low — reproduce the agent's API calls manually
- Testing whether a [BETA] endpoint works on the sandbox
- Confirming batch/inline creation patterns (e.g., POST /department/list, inline costs in POST /travelExpense)

## Setup

The Tripletex sandbox credentials are in `.env`:
```
API_URL=https://kkpqfuj-amager.tripletex.dev/v2
SESSION_TOKEN=<token>
```

The `TripletexClient` at `src/services/tripletex_client.py` is an async HTTP client with Basic Auth.

## How to use

Write a Python script using the TripletexClient:

```python
import asyncio, os, json
from dotenv import load_dotenv
load_dotenv()
from src.services.tripletex_client import TripletexClient

async def test():
    client = TripletexClient(
        base_url=os.getenv('API_URL'),
        session_token=os.getenv('SESSION_TOKEN')
    )

    # Example: GET request
    r = await client.request('GET', '/customer', params={
        'organizationNumber': '999888777',
        'fields': 'id,name,email'
    })
    print(f'Status: {r["status_code"]}')
    print(f'Results: {r["body"]["fullResultSize"]}')

    # Example: POST request
    r = await client.request('POST', '/department/list', json_body=[
        {'name': 'HR'}, {'name': 'Sales'}
    ])

    # Example: PUT request
    r = await client.request('PUT', '/ledger/account/12345', json_body={
        'id': 12345, 'version': 0, 'bankAccountNumber': '86011117947'
    })

    await client.close()

asyncio.run(test())
```

Run it with: `python3 -c "<script>"` or save to a temp file.

## Common test patterns

### Verify a field name works on POST
```python
# Test if freeAccountingDimension1 works in POST /ledger/voucher
r = await client.request('POST', '/ledger/voucher', params={'sendToLedger': True}, json_body={
    'date': '2026-03-21', 'description': 'Test',
    'postings': [
        {'account': {'id': ACCT_ID}, 'amountGross': 1000, 'amountGrossCurrency': 1000,
         'row': 1, 'freeAccountingDimension1': {'id': DIM_VAL_ID}},
        {'account': {'id': BANK_ID}, 'amountGross': -1000, 'amountGrossCurrency': -1000, 'row': 2}
    ]
})
# If 201: field works. If 422 "Feltet eksisterer ikke": wrong field name.
```

### Test batch lookups
```python
# Repeated params for batch (httpx doesn't support natively — use raw httpx)
import httpx
async with httpx.AsyncClient(timeout=30) as hc:
    resp = await hc.get(f'{base_url}/product',
        params=[('productNumber', '1197'), ('productNumber', '7613'), ('fields', 'id,name,number')],
        auth=httpx.BasicAuth('0', token))
    data = resp.json()
    print(f'Found {data["fullResultSize"]} products')
```

### Check if a BETA endpoint works
```python
r = await client.request('POST', '/incomingInvoice', params={'sendTo': 'ledger'}, json_body={...})
# 403 = BETA blocked, 422 = endpoint works but wrong fields, 201 = success
```

### Verify entity appears in search after creation
```python
# Create voucher, then check if it appears as supplier invoice
r = await client.request('POST', '/ledger/voucher', ...)
voucher_id = r['body']['value']['id']
r2 = await client.request('GET', '/supplierInvoice', params={
    'voucherId': voucher_id, 'invoiceDateFrom': '2020-01-01', 'invoiceDateTo': '2030-12-31'
})
print(f'Found as supplier invoice: {r2["body"]["fullResultSize"] > 0}')
```

## What to do with results

After testing, update the relevant files:
- **Playbook** (`src/services/run_history.py`): Add "VERIFIED" to confirmed field names, update golden paths
- **System prompt** (`src/prompts/system_prompt.py`): Add general rules discovered (e.g., deliveryDate required)
- **Validator** (`src/services/api_validator.py`): Add validation for patterns that cause 422 errors

## Important notes

- The sandbox resets periodically — don't rely on specific entity IDs persisting
- The sandbox may have different module configurations than the competition proxy (e.g., BETA endpoints may return 403 on sandbox but work on competition, or vice versa)
- Always clean up test data if possible (DELETE created entities)
- SESSION_TOKEN expires — if you get 403 on everything, the token needs refreshing
- The `client.request()` return format is: `{"status_code": int, "body": dict, "ok": bool}`
