from typing import Any
from fastapi import FastAPI
from pydantic import BaseModel

from x402.http import FacilitatorConfig, HTTPFacilitatorClient, PaymentOption
from x402.http.middleware.fastapi import PaymentMiddlewareASGI
from x402.http.types import RouteConfig
from x402.mechanisms.evm.exact import ExactEvmServerScheme
from x402.server import x402ResourceServer

app = FastAPI(title="Backend Utilities API", version="1.0.0")

# Configuration
RECEIVER_ADDRESS = "0xd9f3cab9a103f76ceebe70513ee6d2499b40a650"
PRICE = "$0.01"
NETWORK = "eip155:84532"  # Base Sepolia testnet

# Create facilitator client (testnet)
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

# Free endpoints (no payment required)
@app.get("/")
async def root() -> dict[str, Any]:
    return {
        "name": "Backend Utilities API",
        "version": "1.0.0",
        "price_per_request": PRICE,
        "payment_method": "x402",
        "receiver": RECEIVER_ADDRESS,
        "network": "Base Sepolia Testnet",
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

# Paid endpoints
@app.post("/v1/validate/email", response_model=EmailResponse)
async def validate_email(request: EmailRequest) -> EmailResponse:
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

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)
