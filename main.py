from typing import Any, Optional
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel
from datetime import datetime
import sqlite3
import uuid
import os

from x402.http import FacilitatorConfig, HTTPFacilitatorClient, PaymentOption
from x402.http.middleware.fastapi import PaymentMiddlewareASGI
from x402.http.types import RouteConfig
from x402.mechanisms.evm.exact import ExactEvmServerScheme
from x402.server import x402ResourceServer

app = FastAPI(title="Backend Utilities API + Handshake MVP", version="2.0.0")

# Configuration
RECEIVER_ADDRESS = os.getenv("RECEIVER_ADDRESS", "0xd9f3cab9a103f76ceebe70513ee6d2499b40a650")
PRICE = "$0.01"
HANDSHAKE_PRICE = "$0.50"
NETWORK = os.getenv("NETWORK", "eip155:8453")  # Base Mainnet default

# Stats tracking
api_stats = {
    "total_requests": 0,
    "requests_today": 0,
    "last_request_time": None,
    "current_date": datetime.now().strftime("%Y-%m-%d")
}

# Create facilitator client
facilitator = HTTPFacilitatorClient(
    FacilitatorConfig(url="https://api.cdp.coinbase.com/platform/v2/x402")
)

# Create resource server and register EVM scheme
server = x402ResourceServer(facilitator)
server.register(NETWORK, ExactEvmServerScheme())

# ============================================================================
# HANDSHAKE DATABASE
# ============================================================================
DB_PATH = os.getenv("DB_PATH", "handshake.db")

def init_handshake_db():
    """Initialize SQLite database with clean MVP schema."""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS deals (
            deal_id TEXT PRIMARY KEY,
            party_a_wallet TEXT NOT NULL,
            party_b_wallet TEXT,
            terms TEXT NOT NULL,
            deal_amount REAL NOT NULL,
            status TEXT DEFAULT 'pending_b',
            party_a_completed BOOLEAN DEFAULT FALSE,
            party_b_completed BOOLEAN DEFAULT FALSE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            completed_at TIMESTAMP,
            disputed_at TIMESTAMP
        )
    ''')
    conn.commit()
    conn.close()

init_handshake_db()

# ============================================================================
# ROUTE CONFIGS FOR X402 PAYMENTS
# ============================================================================
routes: dict[str, RouteConfig] = {
    # Utility endpoints
    "POST /v1/validate/email": RouteConfig(
        accepts=[PaymentOption(scheme="exact", pay_to=RECEIVER_ADDRESS, price=PRICE, network=NETWORK)],
        mime_type="application/json",
        description="Validate email format and MX records",
    ),
    "POST /v1/validate/url": RouteConfig(
        accepts=[PaymentOption(scheme="exact", pay_to=RECEIVER_ADDRESS, price=PRICE, network=NETWORK)],
        mime_type="application/json",
        description="Validate URL format and check reachability",
    ),
    "POST /v1/transform/csv-to-json": RouteConfig(
        accepts=[PaymentOption(scheme="exact", pay_to=RECEIVER_ADDRESS, price=PRICE, network=NETWORK)],
        mime_type="application/json",
        description="Convert CSV text to JSON array",
    ),
    "POST /v1/analyze/text": RouteConfig(
        accepts=[PaymentOption(scheme="exact", pay_to=RECEIVER_ADDRESS, price=PRICE, network=NETWORK)],
        mime_type="application/json",
        description="Analyze text statistics",
    ),
    # Handshake endpoints
    "POST /handshake/create": RouteConfig(
        accepts=[PaymentOption(scheme="exact", pay_to=RECEIVER_ADDRESS, price=HANDSHAKE_PRICE, network=NETWORK)],
        mime_type="application/json",
        description="Create handshake deal - $0.50 from Party A",
    ),
    "POST /handshake/{deal_id}/join": RouteConfig(
        accepts=[PaymentOption(scheme="exact", pay_to=RECEIVER_ADDRESS, price=HANDSHAKE_PRICE, network=NETWORK)],
        mime_type="application/json",
        description="Join handshake deal - $0.50 from Party B",
    ),
}

app.add_middleware(PaymentMiddlewareASGI, routes=routes, server=server)

# ============================================================================
# REQUEST/RESPONSE MODELS
# ============================================================================

# Utility models
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

# Handshake models
class CreateDealRequest(BaseModel):
    party_a_wallet: str
    party_b_wallet: str
    terms: str
    deal_amount: float

class CreateDealResponse(BaseModel):
    deal_id: str
    status: str
    message: str
    share_url: str

class JoinDealResponse(BaseModel):
    deal_id: str
    status: str
    party_a_wallet: str
    party_b_wallet: str
    terms: str
    deal_amount: float
    message: str

class DealResponse(BaseModel):
    deal_id: str
    party_a_wallet: str
    party_b_wallet: str | None
    terms: str
    deal_amount: float
    status: str
    party_a_completed: bool
    party_b_completed: bool
    created_at: str
    updated_at: str
    completed_at: str | None
    disputed_at: str | None

class CompleteResponse(BaseModel):
    deal_id: str
    status: str
    party_a_completed: bool
    party_b_completed: bool
    message: str

class DisputeResponse(BaseModel):
    deal_id: str
    status: str
    message: str

# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

def update_stats():
    """Update request statistics."""
    now = datetime.now()
    today = now.strftime("%Y-%m-%d")
    
    if api_stats["current_date"] != today:
        api_stats["requests_today"] = 0
        api_stats["current_date"] = today
    
    api_stats["total_requests"] += 1
    api_stats["requests_today"] += 1
    api_stats["last_request_time"] = now.isoformat()

def generate_deal_id() -> str:
    """Generate short unique deal ID."""
    return str(uuid.uuid4())[:10]

def get_db():
    """Get database connection."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

# ============================================================================
# FREE ENDPOINTS
# ============================================================================

@app.get("/")
async def root():
    return {
        "name": "Backend Utilities API + Handshake MVP",
        "version": "2.0.0",
        "endpoints": {
            "utilities": [
                "POST /v1/validate/email - $0.01",
                "POST /v1/validate/url - $0.01", 
                "POST /v1/transform/csv-to-json - $0.01",
                "POST /v1/analyze/text - $0.01",
            ],
            "handshake": [
                "POST /handshake/create - $0.50 (Party A)",
                "POST /handshake/{deal_id}/join - $0.50 (Party B)",
                "POST /handshake/{deal_id}/complete",
                "POST /handshake/{deal_id}/dispute",
                "GET /handshake/{deal_id}",
            ]
        },
        "receiver": RECEIVER_ADDRESS,
        "network": "Base Mainnet" if "8453" in NETWORK else "Base Sepolia",
        "faq": "/faq",
        "docs": "/docs",
    }

@app.get("/faq")
async def get_faq():
    return {
        "arbitration": "Human review by @reefbackend. 24-48hr SLA. Both parties submit evidence. Loser forfeits $0.50.",
        "time_windows": "Not enforced in v1. Configurable deadlines coming v2.",
        "partial_completion": "Binary (complete/dispute) in v1. Milestone releases planned v2.",
        "multi_party": "Two-party only. 3+ party planned if demand exists.",
        "reputation": "Considering MoltID/ERC-8004 integration for v2.",
        "deal_flow": [
            "1. POST /handshake/create (terms + $0.50) → deal_id",
            "2. Share deal_id with counterparty",
            "3. POST /handshake/{id}/join ($0.50) → ACTIVE",
            "4. Work happens off-chain",
            "5. Both POST /handshake/{id}/complete → COMPLETED",
            "6. If dispute: POST /handshake/{id}/dispute → arbitration"
        ],
        "evidence_submission": "DM @reefbackend with deal_id, wallet, description, and proof."
    }

@app.get("/health")
async def health():
    return {"status": "healthy", "version": "2.0.0"}

@app.get("/stats", response_model=StatsResponse)
async def stats():
    return StatsResponse(
        total_requests=api_stats["total_requests"],
        requests_today=api_stats["requests_today"],
        last_request_time=api_stats["last_request_time"],
        current_date=api_stats["current_date"]
    )

# ============================================================================
# UTILITY ENDPOINTS (PAID)
# ============================================================================

@app.post("/v1/validate/email", response_model=EmailResponse)
async def validate_email(request: EmailRequest):
    update_stats()
    import re
    import socket
    
    email = request.email
    if not email:
        return EmailResponse(valid=False, format_valid=False, mx_valid=False, message="Email required")
    
    pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    format_valid = bool(re.match(pattern, email))
    
    if not format_valid:
        return EmailResponse(valid=False, format_valid=False, mx_valid=False, message="Invalid email format")
    
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
async def validate_url(request: UrlRequest):
    update_stats()
    import urllib.request
    import urllib.error
    from urllib.parse import urlparse
    
    url = request.url
    if not url:
        return UrlResponse(valid=False, format_valid=False, reachable=False, status_code=None, message="URL required")
    
    try:
        parsed = urlparse(url)
        format_valid = bool(parsed.scheme and parsed.netloc)
    except:
        format_valid = False
    
    if not format_valid:
        return UrlResponse(valid=False, format_valid=False, reachable=False, status_code=None, message="Invalid URL format")
    
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
async def csv_to_json(request: CsvRequest):
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
async def analyze_text(request: TextRequest):
    update_stats()
    text = request.text
    
    if not text:
        return TextResponse(word_count=0, char_count=0, char_count_no_spaces=0, line_count=0, avg_word_length=0.0)
    
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
# HANDSHAKE MVP ENDPOINTS
# ============================================================================

@app.post("/handshake/create", response_model=CreateDealResponse)
async def handshake_create(request: CreateDealRequest):
    """
    Create a new handshake deal. 
    Requires $0.50 payment from Party A (enforced by middleware).
    Status: pending_b (waiting for Party B to join)
    """
    deal_id = generate_deal_id()
    now = datetime.now().isoformat()
    
    conn = get_db()
    c = conn.cursor()
    c.execute('''
        INSERT INTO deals (deal_id, party_a_wallet, party_b_wallet, terms, deal_amount, status, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    ''', (deal_id, request.party_a_wallet, request.party_b_wallet, request.terms, 
          request.deal_amount, 'pending_b', now, now))
    conn.commit()
    conn.close()
    
    return CreateDealResponse(
        deal_id=deal_id,
        status="pending_b",
        message=f"Deal created! Share this deal ID with Party B: {deal_id}",
        share_url=f"https://reef-x402-api.onrender.com/handshake/{deal_id}"
    )

@app.post("/handshake/{deal_id}/join", response_model=JoinDealResponse)
async def handshake_join(deal_id: str):
    """
    Party B joins the deal.
    Requires $0.50 payment from Party B (enforced by middleware).
    Status: active (both parties paid, deal is live)
    """
    conn = get_db()
    c = conn.cursor()
    
    c.execute('SELECT * FROM deals WHERE deal_id = ?', (deal_id,))
    row = c.fetchone()
    
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="Deal not found")
    
    deal = dict(row)
    
    if deal['status'] != 'pending_b':
        conn.close()
        raise HTTPException(status_code=400, detail=f"Deal status is '{deal['status']}', cannot join")
    
    now = datetime.now().isoformat()
    c.execute('UPDATE deals SET status = ?, updated_at = ? WHERE deal_id = ?',
              ('active', now, deal_id))
    conn.commit()
    conn.close()
    
    return JoinDealResponse(
        deal_id=deal_id,
        status="active",
        party_a_wallet=deal['party_a_wallet'],
        party_b_wallet=deal['party_b_wallet'],
        terms=deal['terms'],
        deal_amount=deal['deal_amount'],
        message="Deal is now ACTIVE! Both parties have paid $0.50. Call /complete when finished."
    )

@app.get("/handshake/{deal_id}", response_model=DealResponse)
async def handshake_get(deal_id: str):
    """Get deal status and details."""
    conn = get_db()
    c = conn.cursor()
    c.execute('SELECT * FROM deals WHERE deal_id = ?', (deal_id,))
    row = c.fetchone()
    conn.close()
    
    if not row:
        raise HTTPException(status_code=404, detail="Deal not found")
    
    deal = dict(row)
    return DealResponse(
        deal_id=deal['deal_id'],
        party_a_wallet=deal['party_a_wallet'],
        party_b_wallet=deal['party_b_wallet'],
        terms=deal['terms'],
        deal_amount=deal['deal_amount'],
        status=deal['status'],
        party_a_completed=bool(deal['party_a_completed']),
        party_b_completed=bool(deal['party_b_completed']),
        created_at=deal['created_at'],
        updated_at=deal['updated_at'],
        completed_at=deal['completed_at'],
        disputed_at=deal['disputed_at']
    )

@app.post("/handshake/{deal_id}/complete", response_model=CompleteResponse)
async def handshake_complete(deal_id: str, request: Request):
    """
    Mark deal as complete. Called by either party.
    Both parties must call complete for status to become "completed".
    """
    body = await request.json()
    caller_wallet = body.get('wallet')
    
    if not caller_wallet:
        raise HTTPException(status_code=400, detail="wallet address required")
    
    conn = get_db()
    c = conn.cursor()
    
    c.execute('SELECT * FROM deals WHERE deal_id = ?', (deal_id,))
    row = c.fetchone()
    
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="Deal not found")
    
    deal = dict(row)
    
    if deal['status'] not in ['active', 'pending_completion']:
        conn.close()
        raise HTTPException(status_code=400, detail=f"Deal status is '{deal['status']}', cannot complete")
    
    # Verify caller is a party
    if caller_wallet not in [deal['party_a_wallet'], deal['party_b_wallet']]:
        conn.close()
        raise HTTPException(status_code=403, detail="Not authorized - caller is not a party to this deal")
    
    # Update completion flags
    is_party_a = caller_wallet == deal['party_a_wallet']
    now = datetime.now().isoformat()
    
    if is_party_a:
        c.execute('UPDATE deals SET party_a_completed = TRUE, updated_at = ? WHERE deal_id = ?', (now, deal_id))
    else:
        c.execute('UPDATE deals SET party_b_completed = TRUE, updated_at = ? WHERE deal_id = ?', (now, deal_id))
    
    # Check if both completed
    c.execute('SELECT party_a_completed, party_b_completed FROM deals WHERE deal_id = ?', (deal_id,))
    comp_row = c.fetchone()
    both_completed = comp_row['party_a_completed'] and comp_row['party_b_completed']
    
    if both_completed:
        c.execute('UPDATE deals SET status = ?, completed_at = ?, updated_at = ? WHERE deal_id = ?',
                  ('completed', now, now, deal_id))
        message = "Deal COMPLETED! Both parties confirmed. $1.00 revenue captured."
        final_status = "completed"
    else:
        c.execute('UPDATE deals SET status = ?, updated_at = ? WHERE deal_id = ?',
                  ('pending_completion', now, deal_id))
        other_party = "Party B" if is_party_a else "Party A"
        message = f"Completion recorded. Waiting for {other_party} to confirm."
        final_status = "pending_completion"
    
    conn.commit()
    conn.close()
    
    return CompleteResponse(
        deal_id=deal_id,
        status=final_status,
        party_a_completed=comp_row['party_a_completed'],
        party_b_completed=comp_row['party_b_completed'],
        message=message
    )

@app.post("/handshake/{deal_id}/dispute", response_model=DisputeResponse)
async def handshake_dispute(deal_id: str, request: Request):
    """
    Open dispute. Called by either party.
    Status becomes "disputed" - manual review required.
    """
    body = await request.json()
    caller_wallet = body.get('wallet')
    reason = body.get('reason', 'No reason provided')
    
    if not caller_wallet:
        raise HTTPException(status_code=400, detail="wallet address required")
    
    conn = get_db()
    c = conn.cursor()
    
    c.execute('SELECT * FROM deals WHERE deal_id = ?', (deal_id,))
    row = c.fetchone()
    
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="Deal not found")
    
    deal = dict(row)
    
    if deal['status'] in ['completed', 'disputed']:
        conn.close()
        raise HTTPException(status_code=400, detail=f"Deal already {deal['status']}")
    
    if caller_wallet not in [deal['party_a_wallet'], deal['party_b_wallet']]:
        conn.close()
        raise HTTPException(status_code=403, detail="Not authorized")
    
    now = datetime.now().isoformat()
    c.execute('UPDATE deals SET status = ?, disputed_at = ?, updated_at = ? WHERE deal_id = ?',
              ('disputed', now, now, deal_id))
    conn.commit()
    conn.close()
    
    # Log for manual review
    print(f"[DISPUTE] Deal {deal_id} disputed by {caller_wallet}")
    print(f"[DISPUTE] Reason: {reason}")
    print(f"[DISPUTE] Parties: A={deal['party_a_wallet']}, B={deal['party_b_wallet']}")
    print(f"[DISPUTE] Terms: {deal['terms'][:200]}...")
    
    return DisputeResponse(
        deal_id=deal_id,
        status="disputed",
        message=f"Dispute opened. Manual review in progress. Evidence logged. Reason: {reason}"
    )

@app.get("/handshake/admin/deals")
async def handshake_list_deals():
    """List all deals (admin/debug)."""
    conn = get_db()
    c = conn.cursor()
    c.execute('''
        SELECT deal_id, party_a_wallet, party_b_wallet, status, deal_amount, created_at 
        FROM deals ORDER BY created_at DESC
    ''')
    rows = c.fetchall()
    conn.close()
    
    return [
        {
            "deal_id": row['deal_id'],
            "party_a_wallet": row['party_a_wallet'],
            "party_b_wallet": row['party_b_wallet'],
            "status": row['status'],
            "deal_amount": row['deal_amount'],
            "created_at": row['created_at']
        }
        for row in rows
    ]

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8080))
    uvicorn.run(app, host="0.0.0.0", port=port)
