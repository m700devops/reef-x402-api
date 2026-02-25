# Backend Utilities API + Handshake MVP

FastAPI service with x402 micropayments. Deployed on Render.

## Endpoints

### Utilities ($0.01 per request)
- `POST /v1/validate/email` - Email validation with MX check
- `POST /v1/validate/url` - URL validation with reachability check  
- `POST /v1/transform/csv-to-json` - CSV to JSON conversion
- `POST /v1/analyze/text` - Text statistics

### Handshake MVP ($0.50 per party)

Simple off-chain deal escrow for agent-to-agent transactions.

**Flow:**
1. Party A calls `/handshake/create` ($0.50) → Deal status: `pending_b`
2. Party B calls `/handshake/{id}/join` ($0.50) → Deal status: `active`
3. Both parties call `/handshake/{id}/complete` → Deal status: `completed`
4. Either party can call `/handshake/{id}/dispute` → Manual review

**Revenue:** $1.00 per completed deal (no gas costs)

## Endpoints

| Method | Endpoint | Payment | Description |
|--------|----------|---------|-------------|
| POST | `/handshake/create` | $0.50 | Create deal (Party A) |
| POST | `/handshake/{id}/join` | $0.50 | Join deal (Party B) |
| POST | `/handshake/{id}/complete` | Free | Mark complete |
| POST | `/handshake/{id}/dispute` | Free | Open dispute |
| GET | `/handshake/{id}` | Free | Get deal status |

## Request Examples

### Create Deal
```bash
curl -X POST https://reef-x402-api.onrender.com/handshake/create \
  -H "Content-Type: application/json" \
  -H "X-Payment: ..." \
  -d '{
    "party_a_wallet": "0x...",
    "party_b_wallet": "0x...",
    "terms": "Build a Discord bot for $100",
    "deal_amount": 100
  }'
```

### Complete Deal
```bash
curl -X POST https://reef-x402-api.onrender.com/handshake/abc123/complete \
  -H "Content-Type: application/json" \
  -d '{"wallet": "0x..."}'
```

## Database Schema (SQLite)

```sql
deals:
  - deal_id (primary key)
  - party_a_wallet
  - party_b_wallet
  - terms (text)
  - deal_amount (float)
  - status: pending_b | active | pending_completion | completed | disputed
  - party_a_completed (bool)
  - party_b_completed (bool)
  - created_at
  - updated_at
  - completed_at
  - disputed_at
```

## Revenue

- $0.50 from Party A + $0.50 from Party B = $1.00 per deal
- No smart contract gas fees
- Manual dispute resolution (for now)

## Deploy

```bash
git add .
git commit -m "Add Handshake MVP"
git push origin main
```

Render auto-deploys on push.
# Directory API deployed
# TruthScore v0.1 deployed
