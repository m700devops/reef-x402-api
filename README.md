# Backend Utilities API

Pay-per-request API for common backend tasks. Built with x402 — accepts USDC on Base Sepolia (testnet).

**Price:** $0.01 USD per request

## Endpoints

### POST /v1/validate/email
Validate email format and check MX records.

**Request:**
```json
{"email": "user@example.com"}
```

**Response:**
```json
{
  "valid": true,
  "format_valid": true,
  "mx_valid": true,
  "message": "Valid email"
}
```

### POST /v1/validate/url
Validate URL format and check if reachable.

**Request:**
```json
{"url": "https://example.com"}
```

**Response:**
```json
{
  "valid": true,
  "format_valid": true,
  "reachable": true,
  "status_code": 200,
  "message": "URL is valid and reachable"
}
```

### POST /v1/transform/csv-to-json
Convert CSV text to JSON array.

**Request:**
```json
{
  "csv": "name,age\nJohn,30\nJane,25",
  "headers": true
}
```

**Response:**
```json
{
  "data": [
    {"name": "John", "age": "30"},
    {"name": "Jane", "age": "25"}
  ],
  "count": 2
}
```

### POST /v1/analyze/text
Analyze text statistics.

**Request:**
```json
{"text": "Hello world this is a test"}
```

**Response:**
```json
{
  "word_count": 6,
  "char_count": 26,
  "char_count_no_spaces": 21,
  "line_count": 1,
  "avg_word_length": 4.17
}
```

## Payment

This API uses [x402](https://x402.org) for micropayments.

**Receiver:** `0xd9f3cab9a103f76ceebe70513ee6d2499b40a650`  
**Network:** Base Sepolia (testnet)  
**Price:** $0.01 USD per request in USDC

### How to Pay

1. Make a request without payment → Get `402 Payment Required` response
2. Sign payment payload with your wallet
3. Retry with `PAYMENT-SIGNATURE` header
4. Receive your API response

Or use the [x402 CLI](https://github.com/coinbase/x402):
```bash
x402 curl -X POST https://api.example.com/v1/validate/email \
  -H "Content-Type: application/json" \
  -d '{"email": "test@example.com"}'
```

## Deployment

### Render (Free Tier)
[![Deploy to Render](https://render.com/images/deploy-to-render-button.svg)](https://render.com/deploy?repo=https://github.com/yourusername/backend-utils-api)

### Docker
```bash
docker build -t backend-utils-api .
docker run -p 8080:8080 backend-utils-api
```

### Local
```bash
pip install -r requirements.txt
uvicorn main:app --reload
```

## Use Cases

- **Form validation** — Validate user emails in real-time
- **Data pipelines** — Transform CSV uploads to structured JSON
- **Content analysis** — Word counts, readability metrics
- **URL monitoring** — Check if links are still active

## Tech Stack

- FastAPI
- x402 payment middleware
- Python 3.12+

## License

MIT
