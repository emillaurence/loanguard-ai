# Data Directory

This folder holds reference and synthetic data used to seed and test the Neo4j knowledge graph.

## Subdirectories

### `synthetic/`

Synthetic (generated) data safe to commit. Used for local development, unit tests,
and seeding a fresh AuraDB instance.

| File | Description |
|---|---|
| `loans.json` | Synthetic loan accounts and linked transactions |
| `regulations.json` | Sample APRA prudential obligation stubs (CPS 220, APS 110, etc.) |

**Synthetic data format (loans.json):**
```json
{
  "loan_accounts": [
    {
      "account_id": "LA-001",
      "customer_id": "C-001",
      "product_type": "home_loan",
      "balance": 450000,
      "currency": "AUD",
      "status": "active",
      "risk_rating": "medium",
      "transactions": [
        {
          "transaction_id": "TX-001",
          "amount": 15000,
          "type": "credit",
          "counterparty": "employer",
          "timestamp": "2024-11-01T09:00:00Z",
          "suspicious": false
        }
      ]
    }
  ]
}
```

### `raw/` (gitignored)

Real or sensitive data. **Never commit files in this folder.**
The `.gitignore` excludes `data/raw/` automatically.

## TODO

- [ ] Add a `seed_graph.py` script in `scripts/` to load `synthetic/` data into Neo4j
- [ ] Add a `data_loader.ipynb` notebook for exploratory data ingestion
- [ ] Define the full node/relationship schema once AuraDB instance is provisioned
