from typing import Any, Optional
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel
from datetime import datetime, timedelta
import sqlite3
import uuid
import os

from x402.http import FacilitatorConfig, HTTPFacilitatorClient, PaymentOption
from x402.http.middleware.fastapi import PaymentMiddlewareASGI
from x402.http.types import RouteConfig
from x402.mechanisms.evm.exact import ExactEvmServerScheme
from x402.server import x402ResourceServer

app = FastAPI(title="Handshake MVP - Off-Chain Deal Escrow", version="2.1.0")

# Configuration
RECEIVER_ADDRESS = os.getenv("RECEIVER_ADDRESS", "0xd9f3cab9a103f76ceebe70513ee6d2499b40a650")
HANDSHAKE_PRICE = "$0.50"
NETWORK = os.getenv("NETWORK", "eip155:8453")  # Base Mainnet default

# Create facilitator client
facilitator = HTTPFacilitatorClient(
    FacilitatorConfig(url="https://api.cdp.coinbase.com/platform/v2/x402")
)

# Create resource server and register EVM scheme
server = x402ResourceServer(facilitator)
server.register(NETWORK, ExactEvmServerScheme())

# ============================================================================
# DATABASE
# ============================================================================
DB_PATH = os.getenv("DB_PATH", "handshake.db")

def init_db():
    """Initialize SQLite database with all tables."""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    # Deals table
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
            disputed_at TIMESTAMP,
            expires_at TIMESTAMP
        )
    ''')
    
    # Evidence table
    c.execute('''
        CREATE TABLE IF NOT EXISTS evidence (
            id TEXT PRIMARY KEY,
            deal_id TEXT NOT NULL,
            submitted_by TEXT NOT NULL,
            evidence_type TEXT NOT NULL,
            content TEXT NOT NULL,
            submitted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Deal history table
    c.execute('''
        CREATE TABLE IF NOT EXISTS deal_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            deal_id TEXT NOT NULL,
            old_status TEXT,
            new_status TEXT NOT NULL,
            changed_by TEXT,
            changed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            notes TEXT
        )
    ''')
    
    conn.commit()
    conn.close()

init_db()

# ============================================================================
# ROUTE CONFIGS FOR X402 PAYMENTS
# ============================================================================
routes: dict[str, RouteConfig] = {
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
# MODELS
# ============================================================================
class CreateDealRequest(BaseModel):
    party_a_wallet: str
    party_b_wallet: str
    terms: str
    deal_amount: float
    deadline_hours: int | None = None

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
    expires_at: str | None
    is_expired: bool

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

class SubmitEvidenceRequest(BaseModel):
    submitted_by: str
    evidence_type: str  # "text", "url", "screenshot_url"
    content: str

class EvidenceResponse(BaseModel):
    evidence_id: str
    deal_id: str
    submitted_by: str
    evidence_type: str
    content: str
    submitted_at: str

class HistoryResponse(BaseModel):
    id: int
    deal_id: str
    old_status: str | None
    new_status: str
    changed_by: str | None
    changed_at: str
    notes: str | None

# ============================================================================
# HELPERS
# ============================================================================
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def log_status_change(deal_id: str, old_status: str | None, new_status: str, changed_by: str | None = None, notes: str | None = None):
    """Log a status change to deal_history table."""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(
        "INSERT INTO deal_history (deal_id, old_status, new_status, changed_by, notes) VALUES (?, ?, ?, ?, ?)",
        (deal_id, old_status, new_status, changed_by, notes)
    )
    conn.commit()
    conn.close()

def check_is_expired(expires_at: str | None, status: str) -> bool:
    """Check if a deal has expired based on deadline."""
    if expires_at is None:
        return False
    if status not in ['pending_b', 'active']:
        return False
    try:
        expiry = datetime.fromisoformat(expires_at.replace('Z', '+00:00'))
        return datetime.utcnow() > expiry
    except:
        return False

# ============================================================================
# ENDPOINTS
# ============================================================================
@app.get("/")
async def root():
    return {
        "name": "Handshake MVP",
        "version": "2.1.0",
        "description": "Off-chain deal escrow with x402 payments",
        "price": "$1.00 per deal ($0.50 per party)",
        "receiver": RECEIVER_ADDRESS,
        "network": "Base Mainnet" if "8453" in NETWORK else "Base Sepolia",
        "faq": "/faq",
        "endpoints": [
            "POST /handshake/create - $0.50 (Party A)",
            "POST /handshake/{id}/join - $0.50 (Party B)",
            "POST /handshake/{id}/complete",
            "POST /handshake/{id}/dispute",
            "POST /handshake/{id}/evidence",
            "GET /handshake/{id}/evidence",
            "GET /handshake/{id}/history",
            "GET /handshake/{id}",
        ]
    }

@app.get("/faq")
async def get_faq():
    return {
        "arbitration": "Human review by @reefbackend. 24-48hr SLA. Submit evidence via POST /handshake/{id}/evidence. Loser forfeits $0.50.",
        "time_windows": "Optional deadline_hours on create. is_expired flag shows if deadline passed. Auto-dispute coming v2.",
        "partial_completion": "Binary in v1. Milestone releases planned v2.",
        "multi_party": "Two-party only. 3+ party if demand.",
        "reputation": "Considering MoltID/ERC-8004 for v2.",
        "evidence_submission": "POST /handshake/{id}/evidence with submitted_by, evidence_type, content.",
        "deal_flow": [
            "1. POST /handshake/create (terms + $0.50) → deal_id",
            "2. Share deal_id with counterparty",
            "3. POST /handshake/{id}/join ($0.50) → ACTIVE",
            "4. Work happens off-chain",
            "5. Both POST /handshake/{id}/complete → COMPLETED",
            "6. If dispute: POST /handshake/{id}/dispute → arbitration"
        ]
    }

@app.get("/health")
async def health():
    return {"status": "healthy", "version": "2.1.0"}

@app.post("/handshake/create", response_model=CreateDealResponse)
async def handshake_create(request: CreateDealRequest):
    """Create deal. Party A pays $0.50. Status: pending_b"""
    deal_id = str(uuid.uuid4())[:10]
    now = datetime.now().isoformat()
    
    # Calculate expires_at if deadline provided
    expires_at = None
    if request.deadline_hours:
        expires_at = (datetime.utcnow() + timedelta(hours=request.deadline_hours)).isoformat()
    
    conn = get_db()
    c = conn.cursor()
    c.execute('''
        INSERT INTO deals (deal_id, party_a_wallet, party_b_wallet, terms, deal_amount, status, created_at, updated_at, expires_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (deal_id, request.party_a_wallet, request.party_b_wallet, request.terms, 
          request.deal_amount, 'pending_b', now, now, expires_at))
    conn.commit()
    conn.close()
    
    # Log status change
    log_status_change(deal_id, None, 'pending_b', request.party_a_wallet, 'Deal created')
    
    deadline_msg = f" Deadline: {request.deadline_hours}h." if request.deadline_hours else ""
    
    return CreateDealResponse(
        deal_id=deal_id,
        status="pending_b",
        message=f"Deal created!{deadline_msg} Share this ID with Party B: {deal_id}",
        share_url=f"https://reef-x402-api.onrender.com/handshake/{deal_id}"
    )

@app.post("/handshake/{deal_id}/join", response_model=JoinDealResponse)
async def handshake_join(deal_id: str):
    """Party B joins. Pays $0.50. Status: active"""
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
        raise HTTPException(status_code=400, detail=f"Deal status is '{deal['status']}'")
    
    now = datetime.now().isoformat()
    c.execute('UPDATE deals SET status = ?, updated_at = ? WHERE deal_id = ?',
              ('active', now, deal_id))
    conn.commit()
    conn.close()
    
    # Log status change
    log_status_change(deal_id, 'pending_b', 'active', deal['party_b_wallet'], 'Party B joined')
    
    return JoinDealResponse(
        deal_id=deal_id,
        status="active",
        party_a_wallet=deal['party_a_wallet'],
        party_b_wallet=deal['party_b_wallet'],
        terms=deal['terms'],
        deal_amount=deal['deal_amount'],
        message="Deal ACTIVE! Both parties paid $0.50. Call /complete when done."
    )

@app.get("/handshake/{deal_id}", response_model=DealResponse)
async def handshake_get(deal_id: str):
    """Get deal status"""
    conn = get_db()
    c = conn.cursor()
    c.execute('SELECT * FROM deals WHERE deal_id = ?', (deal_id,))
    row = c.fetchone()
    conn.close()
    
    if not row:
        raise HTTPException(status_code=404, detail="Deal not found")
    
    deal = dict(row)
    is_expired = check_is_expired(deal.get('expires_at'), deal['status'])
    
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
        disputed_at=deal['disputed_at'],
        expires_at=deal.get('expires_at'),
        is_expired=is_expired
    )

@app.post("/handshake/{deal_id}/complete", response_model=CompleteResponse)
async def handshake_complete(deal_id: str, request: Request):
    """Mark complete. Both parties must confirm."""
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
        raise HTTPException(status_code=400, detail=f"Deal status is '{deal['status']}'")
    
    if caller_wallet not in [deal['party_a_wallet'], deal['party_b_wallet']]:
        conn.close()
        raise HTTPException(status_code=403, detail="Not authorized")
    
    is_party_a = caller_wallet == deal['party_a_wallet']
    now = datetime.now().isoformat()
    old_status = deal['status']
    
    if is_party_a:
        c.execute('UPDATE deals SET party_a_completed = TRUE, updated_at = ? WHERE deal_id = ?', (now, deal_id))
    else:
        c.execute('UPDATE deals SET party_b_completed = TRUE, updated_at = ? WHERE deal_id = ?', (now, deal_id))
    
    c.execute('SELECT party_a_completed, party_b_completed FROM deals WHERE deal_id = ?', (deal_id,))
    comp_row = c.fetchone()
    both_completed = comp_row['party_a_completed'] and comp_row['party_b_completed']
    
    if both_completed:
        c.execute('UPDATE deals SET status = ?, completed_at = ?, updated_at = ? WHERE deal_id = ?',
                  ('completed', now, now, deal_id))
        message = "Deal COMPLETED! $1.00 revenue captured."
        final_status = "completed"
        # Log completion
        log_status_change(deal_id, old_status, 'completed', caller_wallet, 'Both parties completed')
    else:
        c.execute('UPDATE deals SET status = ?, updated_at = ? WHERE deal_id = ?',
                  ('pending_completion', now, deal_id))
        other_party = "Party B" if is_party_a else "Party A"
        message = f"Completion recorded. Waiting for {other_party} to confirm."
        final_status = "pending_completion"
        # Log partial completion
        log_status_change(deal_id, old_status, 'pending_completion', caller_wallet, f'{"Party A" if is_party_a else "Party B"} marked complete')
    
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
    """Open dispute. Manual review required."""
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
    old_status = deal['status']
    
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
    
    # Log dispute
    log_status_change(deal_id, old_status, 'disputed', caller_wallet, f'Reason: {reason}')
    
    print(f"[DISPUTE] Deal {deal_id} by {caller_wallet}")
    print(f"[DISPUTE] Reason: {reason}")
    
    return DisputeResponse(
        deal_id=deal_id,
        status="disputed",
        message=f"Dispute opened. Manual review in progress. Reason: {reason}"
    )

@app.post("/handshake/{deal_id}/evidence", response_model=EvidenceResponse)
async def submit_evidence(deal_id: str, request: SubmitEvidenceRequest):
    """Submit evidence for a deal."""
    conn = get_db()
    c = conn.cursor()
    
    # Verify deal exists
    c.execute('SELECT deal_id FROM deals WHERE deal_id = ?', (deal_id,))
    if not c.fetchone():
        conn.close()
        raise HTTPException(status_code=404, detail="Deal not found")
    
    evidence_id = str(uuid.uuid4())[:12]
    now = datetime.now().isoformat()
    
    c.execute('''
        INSERT INTO evidence (id, deal_id, submitted_by, evidence_type, content, submitted_at)
        VALUES (?, ?, ?, ?, ?, ?)
    ''', (evidence_id, deal_id, request.submitted_by, request.evidence_type, request.content, now))
    conn.commit()
    conn.close()
    
    return EvidenceResponse(
        evidence_id=evidence_id,
        deal_id=deal_id,
        submitted_by=request.submitted_by,
        evidence_type=request.evidence_type,
        content=request.content,
        submitted_at=now
    )

@app.get("/handshake/{deal_id}/evidence")
async def get_evidence(deal_id: str):
    """Get all evidence for a deal."""
    conn = get_db()
    c = conn.cursor()
    
    # Verify deal exists
    c.execute('SELECT deal_id FROM deals WHERE deal_id = ?', (deal_id,))
    if not c.fetchone():
        conn.close()
        raise HTTPException(status_code=404, detail="Deal not found")
    
    c.execute('''
        SELECT id, deal_id, submitted_by, evidence_type, content, submitted_at
        FROM evidence WHERE deal_id = ? ORDER BY submitted_at DESC
    ''', (deal_id,))
    rows = c.fetchall()
    conn.close()
    
    return [
        {
            "evidence_id": r['id'],
            "deal_id": r['deal_id'],
            "submitted_by": r['submitted_by'],
            "evidence_type": r['evidence_type'],
            "content": r['content'],
            "submitted_at": r['submitted_at']
        }
        for r in rows
    ]

@app.get("/handshake/{deal_id}/history")
async def get_history(deal_id: str):
    """Get status history for a deal."""
    conn = get_db()
    c = conn.cursor()
    
    # Verify deal exists
    c.execute('SELECT deal_id FROM deals WHERE deal_id = ?', (deal_id,))
    if not c.fetchone():
        conn.close()
        raise HTTPException(status_code=404, detail="Deal not found")
    
    c.execute('''
        SELECT id, deal_id, old_status, new_status, changed_by, changed_at, notes
        FROM deal_history WHERE deal_id = ? ORDER BY changed_at ASC
    ''', (deal_id,))
    rows = c.fetchall()
    conn.close()
    
    return [
        {
            "id": r['id'],
            "deal_id": r['deal_id'],
            "old_status": r['old_status'],
            "new_status": r['new_status'],
            "changed_by": r['changed_by'],
            "changed_at": r['changed_at'],
            "notes": r['notes']
        }
        for r in rows
    ]

@app.get("/handshake/admin/deals")
async def list_deals():
    """List all deals (admin)"""
    conn = get_db()
    c = conn.cursor()
    c.execute('SELECT deal_id, party_a_wallet, party_b_wallet, status, deal_amount, created_at FROM deals ORDER BY created_at DESC')
    rows = c.fetchall()
    conn.close()
    
    return [{"deal_id": r['deal_id'], "party_a_wallet": r['party_a_wallet'], 
             "party_b_wallet": r['party_b_wallet'], "status": r['status'],
             "deal_amount": r['deal_amount'], "created_at": r['created_at']} for r in rows]

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8080))
    uvicorn.run(app, host="0.0.0.0", port=port)
