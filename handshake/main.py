from typing import Any, Optional
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from datetime import datetime
import sqlite3
import uuid

app = FastAPI(title="Handshake", version="1.0.0", description="$1 Deal Insurance for Agents")

# Database
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

# Models
class CreateDealRequest(BaseModel):
    party_a: str
    party_a_wallet: str
    party_b: str
    party_b_wallet: str
    terms: str
    amount: float

class PaymentRequest(BaseModel):
    party: str  # 'a' or 'b'
    tx_hash: str  # USDC transaction hash

class DisputeRequest(BaseModel):
    party: str
    reason: str

@app.get("/")
async def root():
    return {
        "name": "Handshake",
        "version": "1.0.0",
        "description": "$1 Deal Insurance for Agents",
        "price": "$0.50 per party ($1.00 total)",
        "payment": "USDC on Base to 0xd9f3cab9a103f76ceebe70513ee6d2499b40a650",
        "how_it_works": {
            "1": "Create deal (free)",
            "2": "Both parties send $0.50 USDC with deal ID in memo",
            "3": "Work happens",
            "4": "Mark complete = both stakes returned",
            "5": "Dispute = manual arbitration, loser forfeits"
        }
    }

@app.get("/health")
async def health():
    return {"status": "healthy"}

@app.post("/deal")
async def create_deal(request: CreateDealRequest):
    deal_id = str(uuid.uuid4())[:8].upper()
    
    conn = sqlite3.connect('handshake.db')
    c = conn.cursor()
    c.execute('''
        INSERT INTO deals (id, party_a, party_a_wallet, party_b, party_b_wallet, terms, amount)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    ''', (deal_id, request.party_a, request.party_a_wallet,
          request.party_b, request.party_b_wallet, request.terms, request.amount))
    conn.commit()
    conn.close()
    
    return {
        "deal_id": deal_id,
        "status": "created",
        "message": f"Deal {deal_id} created!",
        "next_steps": {
            "party_a": f"{request.party_a}: Send $0.50 USDC to 0xd9f3...a650 with memo '{deal_id}-A'",
            "party_b": f"{request.party_b}: Send $0.50 USDC to 0xd9f3...a650 with memo '{deal_id}-B'",
            "check_status": f"GET /deal/{deal_id}"
        }
    }

@app.get("/deal/{deal_id}")
async def get_deal(deal_id: str):
    conn = sqlite3.connect('handshake.db')
    c = conn.cursor()
    c.execute('SELECT * FROM deals WHERE id = ?', (deal_id.upper(),))
    row = c.fetchone()
    conn.close()
    
    if not row:
        raise HTTPException(status_code=404, detail="Deal not found")
    
    return {
        "id": row[0],
        "party_a": row[1],
        "party_a_wallet": row[2],
        "party_b": row[3],
        "party_b_wallet": row[4],
        "terms": row[5],
        "amount": row[6],
        "status": row[7],
        "party_a_paid": row[8],
        "party_b_paid": row[9],
        "created_at": row[10],
        "completed_at": row[11],
        "disputed_at": row[12]
    }

@app.post("/deal/{deal_id}/pay")
async def record_payment(deal_id: str, request: PaymentRequest):
    conn = sqlite3.connect('handshake.db')
    c = conn.cursor()
    
    c.execute('SELECT status, party_a_paid, party_b_paid FROM deals WHERE id = ?', (deal_id.upper(),))
    row = c.fetchone()
    
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="Deal not found")
    
    status, a_paid, b_paid = row
    
    # In production: verify tx_hash on-chain
    # For MVP: manual verification
    
    if request.party == "a":
        c.execute('UPDATE deals SET party_a_paid = TRUE WHERE id = ?', (deal_id.upper(),))
        msg = f"Party A payment recorded (tx: {request.tx_hash[:10]}...)."
    elif request.party == "b":
        c.execute('UPDATE deals SET party_b_paid = TRUE WHERE id = ?', (deal_id.upper(),))
        if a_paid:
            c.execute("UPDATE deals SET status = 'active' WHERE id = ?", (deal_id.upper(),))
        msg = f"Party B payment recorded (tx: {request.tx_hash[:10]}...)."
    else:
        conn.close()
        raise HTTPException(status_code=400, detail="Party must be 'a' or 'b'")
    
    conn.commit()
    conn.close()
    
    return {"status": "recorded", "message": msg}

@app.post("/deal/{deal_id}/complete")
async def complete_deal(deal_id: str, party: str):
    conn = sqlite3.connect('handshake.db')
    c = conn.cursor()
    
    c.execute('SELECT status, party_a, party_b FROM deals WHERE id = ?', (deal_id.upper(),))
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
              ('completed', datetime.now().isoformat(), deal_id.upper()))
    conn.commit()
    conn.close()
    
    return {
        "status": "completed",
        "message": "Deal complete! Both $0.50 stakes returned to respective parties."
    }

@app.post("/deal/{deal_id}/dispute")
async def dispute_deal(deal_id: str, request: DisputeRequest):
    conn = sqlite3.connect('handshake.db')
    c = conn.cursor()
    
    c.execute('SELECT status, party_a, party_b FROM deals WHERE id = ?', (deal_id.upper(),))
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
              ('disputed', datetime.now().isoformat(), deal_id.upper()))
    conn.commit()
    conn.close()
    
    return {
        "status": "disputed",
        "message": f"Dispute opened by {request.party}. Manual arbitration within 24h. Loser forfeits $0.50."
    }

@app.get("/admin/deals")
async def list_deals():
    conn = sqlite3.connect('handshake.db')
    c = conn.cursor()
    c.execute('SELECT id, party_a, party_b, status, amount, created_at FROM deals ORDER BY created_at DESC')
    rows = c.fetchall()
    conn.close()
    
    return [{"id": r[0], "party_a": r[1], "party_b": r[2], "status": r[3], "amount": r[4], "created_at": r[5]} for r in rows]

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)
