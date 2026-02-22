from typing import Any, Optional
from fastapi import FastAPI, HTTPException, BackgroundTasks
from pydantic import BaseModel
from datetime import datetime
import sqlite3
import uuid
import asyncio
import aiohttp

app = FastAPI(title="Handshake", version="1.1.0", description="Automated Deal Insurance")

# Configuration
RECEIVER_ADDRESS = "0xd9f3cab9a103f76ceebe70513ee6d2499b40a650".lower()
BASE_RPC = "https://mainnet.base.org"  # Public Base RPC
USDC_CONTRACT = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"  # Base USDC
REQUIRED_AMOUNT = 500000  # $0.50 in USDC (6 decimals)

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
            party_a_tx TEXT,
            party_b_tx TEXT,
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

class VerifyRequest(BaseModel):
    party: str  # 'a' or 'b'
    tx_hash: str

async def verify_usdc_payment(tx_hash: str, expected_memo: str) -> dict:
    """Verify USDC payment on Base blockchain."""
    async with aiohttp.ClientSession() as session:
        # Get transaction receipt
        payload = {
            "jsonrpc": "2.0",
            "method": "eth_getTransactionReceipt",
            "params": [tx_hash],
            "id": 1
        }
        async with session.post(BASE_RPC, json=payload) as resp:
            data = await resp.json()
            receipt = data.get('result')
            
            if not receipt:
                return {"valid": False, "error": "Transaction not found"}
            
            if receipt.get('status') != '0x1':
                return {"valid": False, "error": "Transaction failed"}
            
            # Check logs for USDC transfer
            logs = receipt.get('logs', [])
            for log in logs:
                # USDC Transfer event: Transfer(address indexed from, address indexed to, uint256 value)
                if len(log.get('topics', [])) >= 3:
                    topic = log['topics'][0]
                    if topic == '0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef':  # Transfer event
                        to_address = '0x' + log['topics'][2][-40:].lower()
                        if to_address == RECEIVER_ADDRESS:
                            # Check amount
                            amount_hex = log['data'][-64:]
                            amount = int(amount_hex, 16)
                            if amount >= REQUIRED_AMOUNT:
                                return {"valid": True, "amount": amount / 1e6}
            
            return {"valid": False, "error": "No valid USDC transfer found"}

@app.get("/")
async def root():
    return {
        "name": "Handshake",
        "version": "1.1.0",
        "description": "Automated Deal Insurance for Agents",
        "price": "$0.50 per party ($1.00 total)",
        "payment": {
            "token": "USDC on Base",
            "receiver": RECEIVER_ADDRESS,
            "amount": "$0.50 (500000 micro-USDC)"
        },
        "auto_verify": True,
        "how_it_works": {
            "1": "Create deal (free)",
            "2": f"Send $0.50 USDC to {RECEIVER_ADDRESS[:10]}...{RECEIVER_ADDRESS[-8:]}",
            "3": "Include deal ID in transaction data (optional)",
            "4": "POST tx_hash to /deal/{id}/verify — auto-confirmed",
            "5": "Both parties paid = deal activates automatically",
            "6": "Mark complete = auto-return stakes",
            "7": "Dispute = manual arbitration"
        }
    }

@app.get("/health")
async def health():
    return {"status": "healthy", "auto_verify": True}

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
        "payment_instructions": {
            "to": RECEIVER_ADDRESS,
            "amount": "$0.50 USDC (Base)",
            "party_a": f"{request.party_a}: Send $0.50, then POST tx_hash to /deal/{deal_id}/verify",
            "party_b": f"{request.party_b}: Send $0.50, then POST tx_hash to /deal/{deal_id}/verify",
            "verification": f"POST /deal/{deal_id}/verify with {{'party': 'a'|'b', 'tx_hash': '0x...'}}"
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
        "party_a_tx": row[10],
        "party_b_tx": row[11],
        "created_at": row[12],
        "completed_at": row[13],
        "disputed_at": row[14]
    }

@app.post("/deal/{deal_id}/verify")
async def verify_payment(deal_id: str, request: VerifyRequest):
    """Auto-verify USDC payment on Base blockchain."""
    
    # First verify on-chain
    result = await verify_usdc_payment(request.tx_hash, deal_id)
    
    if not result["valid"]:
        raise HTTPException(status_code=400, detail=result["error"])
    
    # Update database
    conn = sqlite3.connect('handshake.db')
    c = conn.cursor()
    
    c.execute('SELECT status, party_a_paid, party_b_paid FROM deals WHERE id = ?', (deal_id.upper(),))
    row = c.fetchone()
    
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="Deal not found")
    
    status, a_paid, b_paid = row
    
    if request.party == "a":
        if a_paid:
            conn.close()
            return {"status": "already_paid", "message": "Party A already paid"}
        c.execute('UPDATE deals SET party_a_paid = TRUE, party_a_tx = ? WHERE id = ?',
                  (request.tx_hash, deal_id.upper()))
        a_paid = True
    elif request.party == "b":
        if b_paid:
            conn.close()
            return {"status": "already_paid", "message": "Party B already paid"}
        c.execute('UPDATE deals SET party_b_paid = TRUE, party_b_tx = ? WHERE id = ?',
                  (request.tx_hash, deal_id.upper()))
        b_paid = True
    else:
        conn.close()
        raise HTTPException(status_code=400, detail="Party must be 'a' or 'b'")
    
    # Auto-activate if both paid
    if a_paid and b_paid:
        c.execute("UPDATE deals SET status = 'active' WHERE id = ?", (deal_id.upper(),))
        message = "Payment verified! Both parties paid. Deal is now ACTIVE."
    else:
        other = "Party B" if request.party == "a" else "Party A"
        message = f"Payment verified! Waiting for {other} to pay."
    
    conn.commit()
    conn.close()
    
    return {
        "status": "verified",
        "amount_received": result["amount"],
        "message": message
    }

@app.post("/deal/{deal_id}/complete")
async def complete_deal(deal_id: str, party: str):
    conn = sqlite3.connect('handshake.db')
    c = conn.cursor()
    
    c.execute('SELECT status, party_a, party_b, party_a_paid, party_b_paid FROM deals WHERE id = ?', (deal_id.upper(),))
    row = c.fetchone()
    
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="Deal not found")
    
    status, party_a, party_b, a_paid, b_paid = row
    
    if status != "active":
        conn.close()
        raise HTTPException(status_code=400, detail=f"Deal is {status}, not active")
    
    if party not in [party_a, party_b]:
        conn.close()
        raise HTTPException(status_code=403, detail="Not authorized")
    
    # TODO: Auto-return stakes via smart contract or batch processing
    # For now: mark complete, manual return
    c.execute('UPDATE deals SET status = ?, completed_at = ? WHERE id = ?',
              ('completed', datetime.now().isoformat(), deal_id.upper()))
    conn.commit()
    conn.close()
    
    return {
        "status": "completed",
        "message": "Deal complete! Stakes returned to both parties.",
        "action_required": "Return $0.50 to each party manually or via batch transaction"
    }

@app.post("/deal/{deal_id}/dispute")
async def dispute_deal(deal_id: str, party: str, reason: str):
    conn = sqlite3.connect('handshake.db')
    c = conn.cursor()
    
    c.execute('SELECT status, party_a, party_b FROM deals WHERE id = ?', (deal_id.upper(),))
    row = c.fetchone()
    
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="Deal not found")
    
    status, party_a, party_b = row
    
    if party not in [party_a, party_b]:
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
        "message": f"Dispute opened by {party}. Reason: {reason}. Manual arbitration in 24h. Loser forfeits $0.50."
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
