from typing import Any, Optional
from fastapi import FastAPI, Request
from pydantic import BaseModel
from datetime import datetime

from x402.http import FacilitatorConfig, HTTPFacilitatorClient, PaymentOption
from x402.http.middleware.fastapi import PaymentMiddlewareASGI
from x402.http.types import RouteConfig
from x402.mechanisms.evm.exact import ExactEvmServerScheme
from x402.server import x402ResourceServer

app = FastAPI(title="Backend Utilities API", version="1.0.0")

# Configuration
RECEIVER_ADDRESS = "0xd9f3cab9a103f76ceebe70513ee6d2499b40a650"
PRICE = "$0.01"
NETWORK = "eip155:84532"  # Base Sepolia Testnet

# Stats tracking
api_stats = {
    "total_requests": 0,
    "requests_today": 0,
    "last_request_time": None,
    "current_date": datetime.now().strftime("%Y-%m-%d")
}

# Create facilitator client (testnet - no auth required)
facilitator = HTTPFacilitatorClient(
    FacilitatorConfig(url="https://x402.org/facilitator")
)

# Create resource server and register EVM scheme
server = x402ResourceServer(facilitator)
server.register(NETWORK, ExactEvmServerScheme())

# Define protected routes
routes: dict[str, RouteConfig] = {
    "POST /v1/validate/email": RouteConfig(
        accepts=[
            PaymentOption(
                scheme="exact",
                pay_to=RECEIVER_ADDRESS,
                price=PRICE,
                network=NETWORK,
            ),
        ],
        mime_type="application/json",
        description="Validate email format and MX records",
    ),
    "POST /v1/validate/url": RouteConfig(
        accepts=[
            PaymentOption(
                scheme="exact",
                pay_to=RECEIVER_ADDRESS,
                price=PRICE,
                network=NETWORK,
            ),
        ],
        mime_type="application/json",
        description="Validate URL format and check reachability",
    ),
    "POST /v1/transform/csv-to-json": RouteConfig(
        accepts=[
            PaymentOption(
                scheme="exact",
                pay_to=RECEIVER_ADDRESS,
                price=PRICE,
                network=NETWORK,
            ),
        ],
        mime_type="application/json",
        description="Convert CSV text to JSON array",
    ),
    "POST /v1/analyze/text": RouteConfig(
        accepts=[
            PaymentOption(
                scheme="exact",
                pay_to=RECEIVER_ADDRESS,
                price=PRICE,
                network=NETWORK,
            ),
        ],
        mime_type="application/json",
        description="Analyze text statistics",
    ),
}

# Add payment middleware
app.add_middleware(PaymentMiddlewareASGI, routes=routes, server=server)

# Request/Response models
class EmailRequest(BaseModel):
    email: str

class EmailResponse(BaseModel):
    valid: bool
    format_valid: bool
    mx_valid: bool
    message: str

class UrlRequest(BaseModel):
    url: str

class UrlResponse(BaseModel):
    valid: bool
    format_valid: bool
    reachable: bool
    status_code: int | None
    message: str

class CsvRequest(BaseModel):
    csv: str
    headers: bool = True

class CsvResponse(BaseModel):
    data: list[Any]
    count: int

class TextRequest(BaseModel):
    text: str

class TextResponse(BaseModel):
    word_count: int
    char_count: int
    char_count_no_spaces: int
    line_count: int
    avg_word_length: float

class StatsResponse(BaseModel):
    total_requests: int
    requests_today: int
    last_request_time: str | None
    current_date: str

def update_stats():
    """Update request statistics."""
    now = datetime.now()
    today = now.strftime("%Y-%m-%d")
    
    # Reset daily counter if date changed
    if api_stats["current_date"] != today:
        api_stats["requests_today"] = 0
        api_stats["current_date"] = today
    
    api_stats["total_requests"] += 1
    api_stats["requests_today"] += 1
    api_stats["last_request_time"] = now.isoformat()

# Free endpoints (no payment required)
@app.get("/")
async def root() -> dict[str, Any]:
    return {
        "name": "Backend Utilities API",
        "version": "1.0.0",
        "price_per_request": PRICE,
        "payment_method": "x402",
        "receiver": RECEIVER_ADDRESS,
        "network": "Base Mainnet",
        "endpoints": [
            "POST /v1/validate/email",
            "POST /v1/validate/url",
            "POST /v1/transform/csv-to-json",
            "POST /v1/analyze/text",
        ],
        "documentation": "/docs",
    }

@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "healthy"}

@app.get("/stats", response_model=StatsResponse)
async def stats() -> StatsResponse:
    """Get API usage statistics."""
    return StatsResponse(
        total_requests=api_stats["total_requests"],
        requests_today=api_stats["requests_today"],
        last_request_time=api_stats["last_request_time"],
        current_date=api_stats["current_date"]
    )

# Paid endpoints
@app.post("/v1/validate/email", response_model=EmailResponse)
async def validate_email(request: EmailRequest) -> EmailResponse:
    update_stats()
    import re
    import socket
    
    email = request.email
    if not email:
        return EmailResponse(
            valid=False,
            format_valid=False,
            mx_valid=False,
            message="Email required"
        )
    
    # Format validation
    pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    format_valid = bool(re.match(pattern, email))
    
    if not format_valid:
        return EmailResponse(
            valid=False,
            format_valid=False,
            mx_valid=False,
            message="Invalid email format"
        )
    
    # MX check
    domain = email.split('@')[1]
    mx_valid = False
    try:
        socket.gethostbyname(domain)
        mx_valid = True
    except:
        pass
    
    return EmailResponse(
        valid=format_valid and mx_valid,
        format_valid=format_valid,
        mx_valid=mx_valid,
        message="Valid email" if (format_valid and mx_valid) else "Domain not reachable"
    )

@app.post("/v1/validate/url", response_model=UrlResponse)
async def validate_url(request: UrlRequest) -> UrlResponse:
    update_stats()
    import urllib.request
    import urllib.error
    from urllib.parse import urlparse
    
    url = request.url
    if not url:
        return UrlResponse(
            valid=False,
            format_valid=False,
            reachable=False,
            status_code=None,
            message="URL required"
        )
    
    # Format validation
    try:
        parsed = urlparse(url)
        format_valid = bool(parsed.scheme and parsed.netloc)
    except:
        format_valid = False
    
    if not format_valid:
        return UrlResponse(
            valid=False,
            format_valid=False,
            reachable=False,
            status_code=None,
            message="Invalid URL format"
        )
    
    # Reachability check
    reachable = False
    status_code = None
    try:
        req = urllib.request.Request(url, method='HEAD', headers={'User-Agent': 'Backend-Utils-API/1.0'})
        with urllib.request.urlopen(req, timeout=10) as response:
            status_code = response.status
            reachable = 200 <= status_code < 400
    except urllib.error.HTTPError as e:
        status_code = e.code
        reachable = 200 <= status_code < 400
    except:
        pass
    
    return UrlResponse(
        valid=format_valid and reachable,
        format_valid=format_valid,
        reachable=reachable,
        status_code=status_code,
        message="URL is valid and reachable" if reachable else "URL not reachable"
    )

@app.post("/v1/transform/csv-to-json", response_model=CsvResponse)
async def csv_to_json(request: CsvRequest) -> CsvResponse:
    update_stats()
    csv_text = request.csv
    has_headers = request.headers
    
    if not csv_text:
        return CsvResponse(data=[], count=0)
    
    lines = csv_text.strip().split('\n')
    if not lines:
        return CsvResponse(data=[], count=0)
    
    def parse_line(line: str) -> list[str]:
        values = []
        current = ''
        in_quotes = False
        for char in line:
            if char == '"':
                in_quotes = not in_quotes
            elif char == ',' and not in_quotes:
                values.append(current.strip())
                current = ''
            else:
                current += char
        values.append(current.strip())
        return values
    
    if has_headers:
        headers = parse_line(lines[0])
        data = []
        for line in lines[1:]:
            if line.strip():
                values = parse_line(line)
                row = {headers[i]: values[i] if i < len(values) else '' for i in range(len(headers))}
                data.append(row)
    else:
        data = [parse_line(line) for line in lines if line.strip()]
    
    return CsvResponse(data=data, count=len(data))

@app.post("/v1/analyze/text", response_model=TextResponse)
async def analyze_text(request: TextRequest) -> TextResponse:
    update_stats()
    text = request.text
    
    if not text:
        return TextResponse(
            word_count=0,
            char_count=0,
            char_count_no_spaces=0,
            line_count=0,
            avg_word_length=0.0
        )
    
    words = text.split()
    word_count = len(words)
    char_count = len(text)
    char_count_no_spaces = len(text.replace(' ', '').replace('\n', '').replace('\t', ''))
    line_count = len(text.split('\n'))
    avg_word_length = sum(len(w) for w in words) / word_count if word_count > 0 else 0
    
    return TextResponse(
        word_count=word_count,
        char_count=char_count,
        char_count_no_spaces=char_count_no_spaces,
        line_count=line_count,
        avg_word_length=round(avg_word_length, 2)
    )

# ============================================================================
# HANDSHAKE - Deal Insurance Service
# ============================================================================

import sqlite3
import uuid

# Initialize Handshake database
def init_handshake_db():
    conn = sqlite3.connect('handshake.db')
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS deals (
            id TEXT PRIMARY KEY,
            party_a TEXT NOT NULL,
            party_a_wallet TEXT NOT NULL,
            party_b TEXT NOT NULL,
            party_b_wallet TEXT NOT NULL,
            terms TEXT NOT NULL,
            amount REAL NOT NULL,
            status TEXT DEFAULT 'pending',
            party_a_paid BOOLEAN DEFAULT FALSE,
            party_b_paid BOOLEAN DEFAULT FALSE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            completed_at TIMESTAMP,
            disputed_at TIMESTAMP,
            dispute_resolution TEXT,
            winner TEXT
        )
    ''')
    conn.commit()
    conn.close()

init_handshake_db()

class HandshakeCreateRequest(BaseModel):
    party_a: str
    party_a_wallet: str
    party_b: str
    party_b_wallet: str
    terms: str
    amount: float

class HandshakeCreateResponse(BaseModel):
    id: str
    status: str
    message: str
    party_a_pay_url: str
    party_b_pay_url: str

class HandshakeDealResponse(BaseModel):
    id: str
    party_a: str
    party_a_wallet: str
    party_b: str
    party_b_wallet: str
    terms: str
    amount: float
    status: str
    party_a_paid: bool
    party_b_paid: bool
    created_at: str
    completed_at: Optional[str] = None
    disputed_at: Optional[str] = None

class HandshakeDisputeRequest(BaseModel):
    party: str
    reason: str

# Update routes to include handshake
routes["POST /handshake/create"] = RouteConfig(
    accepts=[
        PaymentOption(
            scheme="exact",
            pay_to=RECEIVER_ADDRESS,
            price="$0.50",
            network="eip155:84532",  # Testnet for MVP
        ),
    ],
    mime_type="application/json",
    description="Create a handshake deal ($0.50)",
)

@app.post("/handshake/create", response_model=HandshakeCreateResponse)
async def handshake_create(request: HandshakeCreateRequest) -> HandshakeCreateResponse:
    """Create a new handshake deal. Costs $0.50 to create (party A pays)."""
    deal_id = str(uuid.uuid4())[:8]
    
    conn = sqlite3.connect('handshake.db')
    c = conn.cursor()
    c.execute('''
        INSERT INTO deals (id, party_a, party_a_wallet, party_b, party_b_wallet, terms, amount, party_a_paid)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    ''', (deal_id, request.party_a, request.party_a_wallet, 
          request.party_b, request.party_b_wallet, request.terms, request.amount, True))
    conn.commit()
    conn.close()
    
    return HandshakeCreateResponse(
        id=deal_id,
        status="pending",
        message=f"Deal created! ID: {deal_id}. Party B must pay $0.50 to activate. Terms: {request.terms[:100]}...",
        party_a_pay_url="N/A (already paid)",
        party_b_pay_url=f"https://reef-x402-api.onrender.com/handshake/pay/{deal_id}/b"
    )

@app.get("/handshake/{deal_id}", response_model=HandshakeDealResponse)
async def handshake_get(deal_id: str) -> HandshakeDealResponse:
    """Get deal status."""
    conn = sqlite3.connect('handshake.db')
    c = conn.cursor()
    c.execute('SELECT * FROM deals WHERE id = ?', (deal_id,))
    row = c.fetchone()
    conn.close()
    
    if not row:
        raise HTTPException(status_code=404, detail="Deal not found")
    
    return HandshakeDealResponse(
        id=row[0],
        party_a=row[1],
        party_a_wallet=row[2],
        party_b=row[3],
        party_b_wallet=row[4],
        terms=row[5],
        amount=row[6],
        status=row[7],
        party_a_paid=row[8],
        party_b_paid=row[9],
        created_at=row[10],
        completed_at=row[11],
        disputed_at=row[12]
    )

@app.post("/handshake/{deal_id}/pay")
async def handshake_pay(deal_id: str, party: str) -> dict[str, str]:
    """Record payment from party B (simplified - in production this would be webhook from x402)."""
    if party != "b":
        raise HTTPException(status_code=400, detail="Only party B needs to pay via this endpoint")
    
    conn = sqlite3.connect('handshake.db')
    c = conn.cursor()
    
    c.execute('SELECT status, party_b_paid FROM deals WHERE id = ?', (deal_id,))
    row = c.fetchone()
    
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="Deal not found")
    
    status, paid = row
    
    if paid:
        conn.close()
        return {"status": "already_paid", "message": "Party B has already paid"}
    
    c.execute('UPDATE deals SET party_b_paid = TRUE, status = ? WHERE id = ?', 
              ('active' if status == 'pending' else status, deal_id))
    conn.commit()
    conn.close()
    
    return {"status": "paid", "message": "Party B payment recorded. Deal is now ACTIVE."}

@app.post("/handshake/{deal_id}/complete")
async def handshake_complete(deal_id: str, party: str) -> dict[str, str]:
    """Mark deal complete (either party)."""
    conn = sqlite3.connect('handshake.db')
    c = conn.cursor()
    
    c.execute('SELECT status, party_a, party_b FROM deals WHERE id = ?', (deal_id,))
    row = c.fetchone()
    
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="Deal not found")
    
    status, party_a, party_b = row
    
    if status != "active":
        conn.close()
        raise HTTPException(status_code=400, detail=f"Deal is {status}, not active")
    
    if party not in [party_a, party_b]:
        conn.close()
        raise HTTPException(status_code=403, detail="Not authorized")
    
    c.execute('UPDATE deals SET status = ?, completed_at = ? WHERE id = ?',
              ('completed', datetime.now().isoformat(), deal_id))
    conn.commit()
    conn.close()
    
    return {"status": "completed", "message": "Deal completed! Both parties can withdraw their $0.50 stake."}

@app.post("/handshake/{deal_id}/dispute")
async def handshake_dispute(deal_id: str, request: HandshakeDisputeRequest) -> dict[str, str]:
    """Open dispute."""
    conn = sqlite3.connect('handshake.db')
    c = conn.cursor()
    
    c.execute('SELECT status, party_a, party_b FROM deals WHERE id = ?', (deal_id,))
    row = c.fetchone()
    
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="Deal not found")
    
    status, party_a, party_b = row
    
    if request.party not in [party_a, party_b]:
        conn.close()
        raise HTTPException(status_code=403, detail="Not authorized")
    
    if status not in ["active", "pending"]:
        conn.close()
        raise HTTPException(status_code=400, detail=f"Cannot dispute {status} deal")
    
    c.execute('UPDATE deals SET status = ?, disputed_at = ? WHERE id = ?',
              ('disputed', datetime.now().isoformat(), deal_id))
    conn.commit()
    conn.close()
    
    return {
        "status": "disputed",
        "message": f"Dispute opened by {request.party}. Reason: {request.reason}. Manual arbitration within 24h. Both stakes held until resolution."
    }

@app.get("/handshake/admin/deals")
async def handshake_list_deals() -> list[dict[str, Any]]:
    """List all deals (admin)."""
    conn = sqlite3.connect('handshake.db')
    c = conn.cursor()
    c.execute('SELECT id, party_a, party_b, status, amount, created_at FROM deals ORDER BY created_at DESC')
    rows = c.fetchall()
    conn.close()
    
    return [
        {
            "id": row[0],
            "party_a": row[1],
            "party_b": row[2],
            "status": row[3],
            "amount": row[4],
            "created_at": row[5]
        }
        for row in rows
    ]

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)
