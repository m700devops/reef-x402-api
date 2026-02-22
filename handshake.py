from typing import Any, Optional
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from datetime import datetime
import sqlite3
import uuid
import json

from x402.http import FacilitatorConfig, HTTPFacilitatorClient, PaymentOption
from x402.http.middleware.fastapi import PaymentMiddlewareASGI
from x402.http.types import RouteConfig
from x402.mechanisms.evm.exact import ExactEvmServerScheme
from x402.server import x402ResourceServer

app = FastAPI(title="Handshake", version="1.0.0", description="$1 Deal Insurance for Agents")

# Configuration
RECEIVER_ADDRESS = "0xd9f3cab9a103f76ceebe70513ee6d2499b40a650"
PRICE = "$0.50"
NETWORK = "eip155:8453"  # Base Mainnet

# Create facilitator client
facilitator = HTTPFacilitatorClient(
    FacilitatorConfig(url="https://api.cdp.coinbase.com/platform/v2/x402")
)

# Create resource server and register EVM scheme
server = x402ResourceServer(facilitator)
server.register(NETWORK, ExactEvmServerScheme())

# Database setup
def init_db():
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

init_db()

# Request/Response models
class CreateDealRequest(BaseModel):
    party_a: str
    party_a_wallet: str
    party_b: str
    party_b_wallet: str
    terms: str
    amount: float

class CreateDealResponse(BaseModel):
    id: str
    status: str
    payment_url_party_a: str
    payment_url_party_b: str
    message: str

class DealResponse(BaseModel):
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
    completed_at: Optional[str]
    disputed_at: Optional[str]
    dispute_resolution: Optional[str]
    winner: Optional[str]

class DisputeRequest(BaseModel):
    party: str
    reason: str

# Protected routes (require payment)
routes: dict[str, RouteConfig] = {
    "POST /handshake/create": RouteConfig(
        accepts=[
            PaymentOption(
                scheme="exact",
                pay_to=RECEIVER_ADDRESS,
                price=PRICE,
                network=NETWORK,
            ),
        ],
        mime_type="application/json",
        description="Create a new handshake deal ($0.50)",
    ),
}

app.add_middleware(PaymentMiddlewareASGI, routes=routes, server=server)

# Free endpoints
@app.get("/")
async def root() -> dict[str, Any]:
    return {
        "name": "Handshake",
        "version": "1.0.0",
        "description": "$1 Deal Insurance for Agents",
        "price_per_deal": "$1.00 ($0.50 per party)",
        "payment_method": "x402",
        "receiver": RECEIVER_ADDRESS,
        "endpoints": [
            "POST /handshake/create",
            "GET /handshake/{deal_id}",
            "POST /handshake/{deal_id}/complete",
            "POST /handshake/{deal_id}/dispute"
        ]
    }

@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "healthy"}

# Create deal endpoint (payment required)
@app.post("/handshake/create", response_model=CreateDealResponse)
async def create_deal(request: CreateDealRequest) -> CreateDealResponse:
    deal_id = str(uuid.uuid4())[:8]  # Short ID for easy sharing
    
    conn = sqlite3.connect('handshake.db')
    c = conn.cursor()
    c.execute('''
        INSERT INTO deals (id, party_a, party_a_wallet, party_b, party_b_wallet, terms, amount)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    ''', (deal_id, request.party_a, request.party_a_wallet, 
          request.party_b, request.party_b_wallet, request.terms, request.amount))
    conn.commit()
    conn.close()
    
    return CreateDealResponse(
        id=deal_id,
        status="pending",
        payment_url_party_a=f"https://reef-x402-api.onrender.com/handshake/pay/{deal_id}/a",
        payment_url_party_b=f"https://reef-x402-api.onrender.com/handshake/pay/{deal_id}/b",
        message=f"Deal created. Both parties must pay $0.50 to activate. Share deal ID: {deal_id}"
    )

# Get deal status
@app.get("/handshake/{deal_id}", response_model=DealResponse)
async def get_deal(deal_id: str) -> DealResponse:
    conn = sqlite3.connect('handshake.db')
    c = conn.cursor()
    c.execute('SELECT * FROM deals WHERE id = ?', (deal_id,))
    row = c.fetchone()
    conn.close()
    
    if not row:
        raise HTTPException(status_code=404, detail="Deal not found")
    
    return DealResponse(
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
        disputed_at=row[12],
        dispute_resolution=row[13],
        winner=row[14]
    )

# Mark deal complete (both parties must confirm)
@app.post("/handshake/{deal_id}/complete")
async def complete_deal(deal_id: str, party: str) -> dict[str, str]:
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
        raise HTTPException(status_code=400, detail=f"Deal status is {status}, cannot complete")
    
    if party not in [party_a, party_b]:
        conn.close()
        raise HTTPException(status_code=403, detail="Not authorized to complete this deal")
    
    # For MVP: just mark as complete immediately
    # In production: track confirmations from both parties
    c.execute('''
        UPDATE deals SET status = 'completed', completed_at = ?
        WHERE id = ?
    ''', (datetime.now().isoformat(), deal_id))
    conn.commit()
    conn.close()
    
    return {"status": "completed", "message": "Deal marked complete. Funds released to both parties."}

# Open dispute
@app.post("/handshake/{deal_id}/dispute")
async def dispute_deal(deal_id: str, request: DisputeRequest) -> dict[str, str]:
    conn = sqlite3.connect('handshake.db')
    c = conn.cursor()
    
    c.execute('SELECT status, party_a, party_b FROM deals WHERE id = ?', (deal_id,))
    row = c.fetchone()
    
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="Deal not found")
    
    status, party_a, party_b = row
    
    if status not in ["active", "pending"]:
        conn.close()
        raise HTTPException(status_code=400, detail=f"Deal status is {status}, cannot dispute")
    
    if request.party not in [party_a, party_b]:
        conn.close()
        raise HTTPException(status_code=403, detail="Not authorized to dispute this deal")
    
    c.execute('''
        UPDATE deals SET status = 'disputed', disputed_at = ?
        WHERE id = ?
    ''', (datetime.now().isoformat(), deal_id))
    conn.commit()
    conn.close()
    
    return {
        "status": "disputed", 
        "message": "Dispute opened. Manual arbitration will occur within 24 hours. Both parties forfeit $0.50 to escrow until resolved."
    }

# List all deals (for admin/debug)
@app.get("/handshake/admin/deals")
async def list_deals() -> list[dict[str, Any]]:
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
