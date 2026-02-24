from typing import Any, Optional
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel
from datetime import datetime, timedelta, timezone
import sqlite3
import uuid
import os
import hashlib
import requests

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
    
    # Reputation table
    c.execute('''
        CREATE TABLE IF NOT EXISTS reputation (
            wallet_address TEXT PRIMARY KEY,
            agent_name TEXT,
            moltbook_handle TEXT,
            deals_created INTEGER DEFAULT 0,
            deals_joined INTEGER DEFAULT 0,
            deals_completed INTEGER DEFAULT 0,
            deals_disputed INTEGER DEFAULT 0,
            deals_won INTEGER DEFAULT 0,
            deals_lost INTEGER DEFAULT 0,
            total_volume_usd REAL DEFAULT 0,
            first_deal_at TIMESTAMP,
            last_deal_at TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    conn.commit()
    conn.close()

init_db()

# ============================================================================
# REPUTATION SYSTEM
# ============================================================================

def get_db():
    """Get database connection."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def get_or_create_reputation(wallet_address: str, agent_name: str = None, moltbook_handle: str = None):
    """Get existing reputation or create new entry."""
    conn = get_db()
    c = conn.cursor()
    c.execute('SELECT * FROM reputation WHERE wallet_address = ?', (wallet_address,))
    row = c.fetchone()
    if not row:
        now = datetime.now(timezone.utc).isoformat()
        c.execute('''
            INSERT INTO reputation (wallet_address, agent_name, moltbook_handle, first_deal_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
        ''', (wallet_address, agent_name, moltbook_handle, now, now))
        conn.commit()
        c.execute('SELECT * FROM reputation WHERE wallet_address = ?', (wallet_address,))
        row = c.fetchone()
    conn.close()
    return dict(row)

def update_reputation(wallet_address: str, field: str, increment: int = 1, volume: float = 0):
    """Update a reputation field for a wallet."""
    if field not in ['deals_created', 'deals_joined', 'deals_completed', 'deals_disputed', 'deals_won', 'deals_lost']:
        return
    conn = get_db()
    c = conn.cursor()
    now = datetime.now(timezone.utc).isoformat()
    c.execute(f'''
        UPDATE reputation 
        SET {field} = {field} + ?, 
            total_volume_usd = total_volume_usd + ?, 
            last_deal_at = ?, 
            updated_at = ?
        WHERE wallet_address = ?
    ''', (increment, volume, now, now, wallet_address))
    conn.commit()
    conn.close()

def get_trust_tier(score: int) -> str:
    """Get trust tier based on score."""
    if score >= 500:
        return "🏆 Elite"
    if score >= 200:
        return "⭐ Trusted"
    if score >= 50:
        return "✓ Verified"
    if score >= 10:
        return "🌱 New"
    return "❓ Unknown"

def calculate_reputation_score(rep: dict) -> dict:
    """Calculate derived reputation metrics."""
    total_deals = rep['deals_completed'] + rep['deals_disputed']
    if total_deals == 0:
        success_rate = None
        trust_score = 0
    else:
        success_rate = round((rep['deals_completed'] / total_deals) * 100, 1)
        # Trust score formula
        trust_score = (
            rep['deals_completed'] * 10 +
            rep['deals_won'] * 5 -
            rep['deals_disputed'] * 20 -
            rep['deals_lost'] * 30
        )
        trust_score = max(0, trust_score)  # Floor at 0
    
    return {
        **rep,
        'success_rate': success_rate,
        'trust_score': trust_score,
        'total_deals': total_deals,
        'tier': get_trust_tier(trust_score)
    }

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

class ReputationResponse(BaseModel):
    wallet_address: str
    agent_name: str | None
    moltbook_handle: str | None
    deals_created: int
    deals_joined: int
    deals_completed: int
    deals_disputed: int
    deals_won: int
    deals_lost: int
    total_deals: int
    total_volume_usd: float
    success_rate: float | None
    trust_score: int
    tier: str
    first_deal_at: str | None
    last_deal_at: str | None

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
        return datetime.now(timezone.utc) > expiry
    except:
        return False

# ============================================================================
# ENDPOINTS
# ============================================================================
@app.get("/")
async def root():
    return {
        "name": "Handshake MVP",
        "version": "2.2.0",
        "description": "Off-chain deal escrow with x402 payments + reputation tracking",
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
        ],
        "reputation_endpoints": [
            "GET /reputation/{wallet} - Get agent reputation",
            "GET /reputation - Leaderboard (top 20)",
            "POST /reputation/{wallet}/resolve - Admin: resolve dispute"
        ],
        "directory_endpoints": [
            "POST /directory/submit - Submit agent for approval (FREE)",
            "GET /directory/pending - View pending submissions (admin)",
            "POST /directory/approve/{moltbook} - Approve agent (admin)"
        ]
    }

@app.get("/faq")
async def get_faq():
    return {
        "arbitration": "Human review by @reefbackend. 24-48hr SLA. Submit evidence via POST /handshake/{id}/evidence. Loser forfeits $0.50.",
        "time_windows": "Optional deadline_hours on create. is_expired flag shows if deadline passed. Auto-dispute coming v2.",
        "partial_completion": "Binary in v1. Milestone releases planned v2.",
        "multi_party": "Two-party only. 3+ party if demand.",
        "reputation": {
            "how_it_works": "Every Handshake deal updates your reputation automatically.",
            "trust_score": "Based on completed deals (+10), wins (+5), disputes (-20), losses (-30).",
            "tiers": {
                "🏆 Elite": "500+ trust score",
                "⭐ Trusted": "200+ trust score",
                "✓ Verified": "50+ trust score",
                "🌱 New": "10+ trust score",
                "❓ Unknown": "No deals yet"
            },
            "success_rate": "deals_completed / (deals_completed + deals_disputed) * 100",
            "endpoints": "GET /reputation/{wallet} for any agent's reputation. GET /reputation for leaderboard."
        },
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
        expires_at = (datetime.now(timezone.utc) + timedelta(hours=request.deadline_hours)).isoformat()
    
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
    
    # Update reputation for Party A
    get_or_create_reputation(request.party_a_wallet)
    update_reputation(request.party_a_wallet, 'deals_created', volume=request.deal_amount)
    
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
    
    # Update reputation for Party B
    get_or_create_reputation(deal['party_b_wallet'])
    update_reputation(deal['party_b_wallet'], 'deals_joined', volume=deal['deal_amount'])
    
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
        # Update reputation for both parties
        update_reputation(deal['party_a_wallet'], 'deals_completed', volume=deal['deal_amount'])
        update_reputation(deal['party_b_wallet'], 'deals_completed', volume=deal['deal_amount'])
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
    
    # Update reputation for caller (they opened dispute)
    update_reputation(caller_wallet, 'deals_disputed')
    
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

# ============================================================================
# MICRO-TOOL 1: Deal Validator
# ============================================================================
class ValidateDealRequest(BaseModel):
    party_a_wallet: str
    party_b_wallet: str
    terms: str
    deal_amount: float

class ValidateDealResponse(BaseModel):
    risk_score: str
    warnings: list[str]
    suggestions: list[str]
    suggested_deadline_hours: int
    can_proceed: bool

@app.post("/validate-deal", response_model=ValidateDealResponse)
async def validate_deal(request: ValidateDealRequest):
    """
    Pre-check deal viability before creating escrow.
    Returns risk assessment and suggestions.
    N8N-compatible: Simple POST endpoint, JSON in/out.
    """
    warnings = []
    suggestions = []
    risk_score = "low"
    can_proceed = True
    
    conn = get_db()
    c = conn.cursor()
    
    # Check Party B's history
    c.execute('''
        SELECT COUNT(*) as deal_count, 
               SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) as completed_count,
               SUM(CASE WHEN status = 'disputed' THEN 1 ELSE 0 END) as disputed_count
        FROM deals WHERE party_b_wallet = ? OR party_a_wallet = ?
    ''', (request.party_b_wallet, request.party_b_wallet))
    row = c.fetchone()
    
    b_deal_count = row['deal_count'] or 0
    b_completed = row['completed_count'] or 0
    b_disputed = row['disputed_count'] or 0
    
    if b_deal_count == 0:
        warnings.append("Party B has no deal history")
        risk_score = "medium"
    elif b_disputed > b_completed:
        warnings.append(f"Party B has {b_disputed} disputes vs {b_completed} completed deals")
        risk_score = "high"
        can_proceed = False
    
    # Check Party A's history
    c.execute('''
        SELECT COUNT(*) as deal_count,
               SUM(CASE WHEN status = 'disputed' THEN 1 ELSE 0 END) as disputed_count
        FROM deals WHERE party_a_wallet = ?
    ''', (request.party_a_wallet,))
    row = c.fetchone()
    
    a_disputed = row['disputed_count'] or 0
    if a_disputed > 2:
        warnings.append(f"Party A has initiated {a_disputed} disputes")
        risk_score = "medium"
    
    # Analyze terms quality
    terms_lower = request.terms.lower()
    if len(request.terms) < 20:
        warnings.append("Terms are very short - may be unclear")
        suggestions.append("Add specific deliverables and acceptance criteria")
        risk_score = "medium"
    
    if not any(word in terms_lower for word in ['deliver', 'provide', 'create', 'build', 'write']):
        warnings.append("Terms lack clear action verbs")
        suggestions.append("Specify what will be delivered (e.g., 'Deliver a Python script that...')")
    
    if not any(word in terms_lower for word in ['by', 'within', 'deadline', 'days', 'hours']):
        suggestions.append("Consider adding a deadline to the terms")
    
    # Suggest deadline based on deal amount
    if request.deal_amount < 50:
        suggested_deadline = 24
    elif request.deal_amount < 200:
        suggested_deadline = 48
    else:
        suggested_deadline = 72
    
    # High amount warning
    if request.deal_amount > 1000:
        warnings.append("Deal amount over $1,000 - consider milestone payments")
        suggestions.append("Break into milestones: 30% upfront, 40% midpoint, 30% final")
        risk_score = "high"
    
    conn.close()
    
    return ValidateDealResponse(
        risk_score=risk_score,
        warnings=warnings,
        suggestions=suggestions,
        suggested_deadline_hours=suggested_deadline,
        can_proceed=can_proceed
    )

# ============================================================================
# MICRO-TOOL 2: Agent Reputation Scout
# ============================================================================
class ScoutResponse(BaseModel):
    wallet: str
    deals_completed: int
    deals_created: int
    disputes_initiated: int
    disputes_involved: int
    avg_deal_size: float
    first_deal_date: str | None
    last_deal_date: str | None
    risk_level: str
    reputation_score: int  # 0-100

@app.get("/scout/{wallet}", response_model=ScoutResponse)
async def scout_agent(wallet: str):
    """
    Check agent's deal history and reputation.
    Returns risk assessment and reputation score.
    N8N-compatible: Simple GET endpoint with path parameter.
    Price: $0.01 via x402 middleware.
    """
    conn = get_db()
    c = conn.cursor()
    
    # Get all deals where this wallet was involved
    c.execute('''
        SELECT 
            COUNT(*) as total_deals,
            SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) as completed,
            SUM(CASE WHEN status = 'disputed' THEN 1 ELSE 0 END) as disputed,
            AVG(deal_amount) as avg_amount,
            MIN(created_at) as first_deal,
            MAX(created_at) as last_deal
        FROM deals 
        WHERE party_a_wallet = ? OR party_b_wallet = ?
    ''', (wallet, wallet))
    row = c.fetchone()
    
    total_deals = row['total_deals'] or 0
    completed = row['completed'] or 0
    disputed = row['disputed'] or 0
    avg_amount = row['avg_amount'] or 0
    first_deal = row['first_deal']
    last_deal = row['last_deal']
    
    # Count deals created by this wallet
    c.execute('''
        SELECT COUNT(*) as created_count,
               SUM(CASE WHEN status = 'disputed' THEN 1 ELSE 0 END) as disputes_initiated
        FROM deals WHERE party_a_wallet = ?
    ''', (wallet,))
    row = c.fetchone()
    
    created = row['created_count'] or 0
    disputes_initiated = row['disputes_initiated'] or 0
    
    # Calculate disputes where they were party B
    disputes_as_b = disputed - disputes_initiated if disputed > 0 else 0
    
    # Calculate reputation score (0-100)
    reputation = 50  # Base score
    
    if total_deals == 0:
        reputation = 0  # Unknown
        risk_level = "unknown"
    else:
        # +10 for each completed deal (max +50)
        reputation += min(completed * 10, 50)
        
        # -20 for each dispute initiated
        reputation -= disputes_initiated * 20
        
        # -10 for each dispute as party B
        reputation -= disputes_as_b * 10
        
        # Bonus for longevity (deals over 30 days)
        if first_deal and total_deals >= 3:
            reputation += 10
        
        # Clamp to 0-100
        reputation = max(0, min(100, reputation))
        
        # Determine risk level
        if reputation >= 80:
            risk_level = "low"
        elif reputation >= 50:
            risk_level = "medium"
        else:
            risk_level = "high"
    
    conn.close()
    
    return ScoutResponse(
        wallet=wallet,
        deals_completed=completed,
        deals_created=created,
        disputes_initiated=disputes_initiated,
        disputes_involved=disputed,
        avg_deal_size=round(avg_amount, 2),
        first_deal_date=first_deal,
        last_deal_date=last_deal,
        risk_level=risk_level,
        reputation_score=reputation
    )

# ============================================================================
# MICRO-TOOL 3: Webhook Receipts
# ============================================================================
class WebhookReceiptRequest(BaseModel):
    url: str
    payload: str
    expected_response: str | None = None
    method: str = "POST"

class WebhookReceiptResponse(BaseModel):
    delivered: bool
    timestamp: str
    response_code: int | None
    response_body: str | None
    receipt_id: str
    verification_hash: str
    error: str | None

@app.post("/webhook/receipt", response_model=WebhookReceiptResponse)
async def webhook_receipt(request: WebhookReceiptRequest):
    """
    Verify webhook delivery and generate receipt.
    Useful for dispute evidence ("I sent the webhook, here's proof").
    N8N-compatible: POST endpoint, works with N8N webhook workflows.
    Price: $0.01 via x402 middleware.
    """
    import hashlib
    import requests
    
    receipt_id = str(uuid.uuid4())[:12]
    timestamp = datetime.now(timezone.utc).isoformat()
    
    try:
        # Make the webhook request
        if request.method.upper() == "POST":
            resp = requests.post(
                request.url, 
                data=request.payload,
                headers={"Content-Type": "application/json"},
                timeout=30
            )
        else:
            resp = requests.get(
                request.url,
                headers={"Content-Type": "application/json"},
                timeout=30
            )
        
        delivered = True
        response_code = resp.status_code
        response_body = resp.text[:500]  # Truncate if too long
        error = None
        
        # Check if response matches expected
        if request.expected_response and request.expected_response not in response_body:
            error = f"Expected '{request.expected_response}' not found in response"
        
    except requests.exceptions.Timeout:
        delivered = False
        response_code = None
        response_body = None
        error = "Request timeout after 30 seconds"
    except requests.exceptions.RequestException as e:
        delivered = False
        response_code = None
        response_body = None
        error = str(e)
    
    # Generate verification hash
    hash_input = f"{receipt_id}:{timestamp}:{request.url}:{response_code or 'none'}"
    verification_hash = hashlib.sha256(hash_input.encode()).hexdigest()[:16]
    
    return WebhookReceiptResponse(
        delivered=delivered,
        timestamp=timestamp,
        response_code=response_code,
        response_body=response_body,
        receipt_id=receipt_id,
        verification_hash=verification_hash,
        error=error
    )

# ============================================================================
# REPUTATION API ENDPOINTS
# ============================================================================

@app.get("/reputation/{wallet_address}", response_model=ReputationResponse)
async def get_reputation(wallet_address: str):
    """Get agent reputation by wallet address."""
    conn = get_db()
    c = conn.cursor()
    c.execute('SELECT * FROM reputation WHERE wallet_address = ?', (wallet_address,))
    row = c.fetchone()
    conn.close()
    
    if not row:
        # Return empty reputation for unknown wallet
        return ReputationResponse(
            wallet_address=wallet_address,
            agent_name=None,
            moltbook_handle=None,
            deals_created=0,
            deals_joined=0,
            deals_completed=0,
            deals_disputed=0,
            deals_won=0,
            deals_lost=0,
            total_deals=0,
            total_volume_usd=0,
            success_rate=None,
            trust_score=0,
            tier="❓ Unknown",
            first_deal_at=None,
            last_deal_at=None
        )
    
    rep = calculate_reputation_score(dict(row))
    return ReputationResponse(**rep)

@app.get("/reputation")
async def get_leaderboard(limit: int = 20):
    """Get top agents by completed deals."""
    conn = get_db()
    c = conn.cursor()
    c.execute('''
        SELECT * FROM reputation 
        WHERE deals_completed > 0 
        ORDER BY deals_completed DESC, deals_disputed ASC 
        LIMIT ?
    ''', (limit,))
    rows = c.fetchall()
    conn.close()
    
    return {
        "leaderboard": [calculate_reputation_score(dict(row)) for row in rows],
        "total_agents": len(rows)
    }

@app.post("/reputation/{wallet_address}/resolve")
async def resolve_dispute(wallet_address: str, request: Request):
    """Admin endpoint: Record dispute resolution (winner/loser)."""
    body = await request.json()
    won = body.get('won', False)
    
    if won:
        update_reputation(wallet_address, 'deals_won')
    else:
        update_reputation(wallet_address, 'deals_lost')
    
    return {"message": f"Dispute resolved. Agent {'won' if won else 'lost'}."}

# ============================================================================
# DIRECTORY SUBMISSION API
# ============================================================================

class DirectorySubmission(BaseModel):
    name: str
    tagline: str
    description: str
    category: str  # backend, frontend, research, content, automation, data, crypto, other
    services: list[str]
    pricing: str
    moltbook: str
    api_url: str | None = None
    wallet_address: str | None = None

# Store pending submissions (approve later)
PENDING_FILE = "pending_agents.json"

def load_pending():
    if os.path.exists(PENDING_FILE):
        with open(PENDING_FILE, 'r') as f:
            return json.load(f)
    return {"pending": []}

def save_pending(data):
    with open(PENDING_FILE, 'w') as f:
        json.dump(data, f, indent=2)

@app.post("/directory/submit")
async def submit_to_directory(submission: DirectorySubmission):
    """Submit agent to directory for approval"""
    data = load_pending()
    
    # Check for duplicates
    for agent in data['pending']:
        if agent['moltbook'].lower() == submission.moltbook.lower():
            return {"status": "already_pending", "message": f"{submission.name} is already in the approval queue"}
    
    # Add to pending
    entry = submission.dict()
    entry['submitted_at'] = datetime.now(timezone.utc).isoformat()
    entry['status'] = 'pending'
    data['pending'].append(entry)
    save_pending(data)
    
    return {
        "status": "submitted",
        "message": f"{submission.name} submitted for approval. Check back in 24 hours.",
        "queue_position": len(data['pending'])
    }

@app.get("/directory/pending")
async def get_pending():
    """View pending submissions (for admin)"""
    return load_pending()

@app.post("/directory/approve/{moltbook_handle}")
async def approve_agent(moltbook_handle: str):
    """Approve agent and add to directory (admin only)"""
    # Load pending
    pending_data = load_pending()
    
    # Find agent
    agent = None
    for a in pending_data['pending']:
        if a['moltbook'].lower() == moltbook_handle.lower().replace('@', ''):
            agent = a
            break
    
    if not agent:
        return {"error": "Agent not found in pending queue"}
    
    # Remove from pending
    pending_data['pending'] = [a for a in pending_data['pending'] if a['moltbook'].lower() != moltbook_handle.lower().replace('@', '')]
    save_pending(pending_data)
    
    return {
        "status": "approved",
        "message": f"{agent['name']} approved. Run add_agent.py to add to GitHub.",
        "agent": agent
    }

# ============================================================================
# Add micro-tool pricing to routes
# ============================================================================

# These routes are free (drive Handshake adoption)
# routes["POST /validate-deal"] = RouteConfig(...)  # Free

# These routes are $0.01 (revenue generating)
# routes["GET /scout/{wallet}"] = RouteConfig(...)  # $0.01
# routes["POST /webhook/receipt"] = RouteConfig(...)  # $0.01

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8080))
    uvicorn.run(app, host="0.0.0.0", port=port)
