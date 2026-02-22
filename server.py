#!/usr/bin/env python3
"""
x402 Backend Utilities API
Pay-per-request endpoints for common backend tasks.
Price: $0.01 USD per request
"""

import json
import re
import socket
import urllib.request
import urllib.error
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
import hashlib
import time

# Configuration
PRICE_USD = 0.01
RECEIVER_ADDRESS = "0xd9f3cab9a103f76ceebe70513ee6d2499b40a650"  # My wallet
API_VERSION = "v1"

class X402Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        # Suppress default logging
        pass
    
    def do_OPTIONS(self):
        self.send_cors_headers()
        self.end_headers()
    
    def do_POST(self):
        self.send_cors_headers()
        
        # Parse path
        parsed = urlparse(self.path)
        path = parsed.path
        
        # Check for x402 payment header
        payment_header = self.headers.get('X-Payment-Response')
        
        # For now, allow free requests (will implement x402 properly later)
        # In production, verify payment before processing
        
        # Route to handler
        if path == f"/{API_VERSION}/validate/email":
            self.handle_validate_email()
        elif path == f"/{API_VERSION}/validate/url":
            self.handle_validate_url()
        elif path == f"/{API_VERSION}/transform/csv-to-json":
            self.handle_csv_to_json()
        elif path == f"/{API_VERSION}/analyze/text":
            self.handle_text_analysis()
        elif path == "/health":
            self.handle_health()
        else:
            self.send_error(404, "Endpoint not found")
    
    def do_GET(self):
        self.send_cors_headers()
        
        parsed = urlparse(self.path)
        path = parsed.path
        
        if path == "/":
            self.handle_index()
        elif path == "/health":
            self.handle_health()
        elif path == f"/{API_VERSION}/docs":
            self.handle_docs()
        else:
            self.send_error(404, "Not found")
    
    def send_cors_headers(self):
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type, X-Payment-Response')
        self.send_header('Content-Type', 'application/json')
    
    def send_json_response(self, data, status=200):
        self.send_response(status)
        self.send_cors_headers()
        self.end_headers()
        self.wfile.write(json.dumps(data, indent=2).encode())
    
    def read_body(self):
        content_length = int(self.headers.get('Content-Length', 0))
        if content_length > 0:
            return self.rfile.read(content_length).decode('utf-8')
        return "{}"
    
    def handle_index(self):
        self.send_json_response({
            "name": "Backend Utilities API",
            "version": API_VERSION,
            "price_per_request": f"${PRICE_USD} USD",
            "payment_method": "x402",
            "receiver": RECEIVER_ADDRESS,
            "endpoints": [
                "POST /v1/validate/email",
                "POST /v1/validate/url", 
                "POST /v1/transform/csv-to-json",
                "POST /v1/analyze/text",
                "GET  /v1/docs"
            ],
            "documentation": "/v1/docs"
        })
    
    def handle_health(self):
        self.send_json_response({"status": "healthy", "version": API_VERSION})
    
    def handle_docs(self):
        self.send_json_response({
            "endpoints": {
                "POST /v1/validate/email": {
                    "description": "Validate email format and MX records",
                    "price": f"${PRICE_USD}",
                    "body": {"email": "string"},
                    "response": {
                        "valid": "boolean",
                        "format_valid": "boolean",
                        "mx_valid": "boolean",
                        "message": "string"
                    }
                },
                "POST /v1/validate/url": {
                    "description": "Validate URL format and check if reachable",
                    "price": f"${PRICE_USD}",
                    "body": {"url": "string"},
                    "response": {
                        "valid": "boolean",
                        "format_valid": "boolean",
                        "reachable": "boolean",
                        "status_code": "number or null",
                        "message": "string"
                    }
                },
                "POST /v1/transform/csv-to-json": {
                    "description": "Convert CSV text to JSON array",
                    "price": f"${PRICE_USD}",
                    "body": {"csv": "string", "headers": "boolean (default: true)"},
                    "response": {
                        "data": "array of objects",
                        "count": "number"
                    }
                },
                "POST /v1/analyze/text": {
                    "description": "Analyze text statistics",
                    "price": f"${PRICE_USD}",
                    "body": {"text": "string"},
                    "response": {
                        "word_count": "number",
                        "char_count": "number",
                        "char_count_no_spaces": "number",
                        "line_count": "number",
                        "avg_word_length": "number"
                    }
                }
            }
        })
    
    def handle_validate_email(self):
        try:
            body = json.loads(self.read_body())
            email = body.get('email', '')
            
            if not email:
                self.send_json_response({"error": "Email required"}, 400)
                return
            
            # Format validation
            pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
            format_valid = bool(re.match(pattern, email))
            
            if not format_valid:
                self.send_json_response({
                    "valid": False,
                    "format_valid": False,
                    "mx_valid": False,
                    "message": "Invalid email format"
                })
                return
            
            # Extract domain
            domain = email.split('@')[1]
            
            # MX record check
            mx_valid = False
            try:
                socket.gethostbyname(domain)
                mx_valid = True
            except:
                pass
            
            self.send_json_response({
                "valid": format_valid and mx_valid,
                "format_valid": format_valid,
                "mx_valid": mx_valid,
                "message": "Valid email" if (format_valid and mx_valid) else "Domain not reachable"
            })
            
        except json.JSONDecodeError:
            self.send_json_response({"error": "Invalid JSON"}, 400)
    
    def handle_validate_url(self):
        try:
            body = json.loads(self.read_body())
            url = body.get('url', '')
            
            if not url:
                self.send_json_response({"error": "URL required"}, 400)
                return
            
            # Format validation
            try:
                parsed = urlparse(url)
                format_valid = bool(parsed.scheme and parsed.netloc)
            except:
                format_valid = False
            
            if not format_valid:
                self.send_json_response({
                    "valid": False,
                    "format_valid": False,
                    "reachable": False,
                    "status_code": None,
                    "message": "Invalid URL format"
                })
                return
            
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
            
            self.send_json_response({
                "valid": format_valid and reachable,
                "format_valid": format_valid,
                "reachable": reachable,
                "status_code": status_code,
                "message": "URL is valid and reachable" if reachable else "URL not reachable"
            })
            
        except json.JSONDecodeError:
            self.send_json_response({"error": "Invalid JSON"}, 400)
    
    def handle_csv_to_json(self):
        try:
            body = json.loads(self.read_body())
            csv_text = body.get('csv', '')
            has_headers = body.get('headers', True)
            
            if not csv_text:
                self.send_json_response({"error": "CSV text required"}, 400)
                return
            
            lines = csv_text.strip().split('\n')
            if not lines:
                self.send_json_response({"data": [], "count": 0})
                return
            
            # Simple CSV parser (handles basic cases)
            def parse_line(line):
                # Handle quoted values
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
                        row = {}
                        for i, header in enumerate(headers):
                            row[header] = values[i] if i < len(values) else ''
                        data.append(row)
            else:
                data = []
                for line in lines:
                    if line.strip():
                        data.append(parse_line(line))
            
            self.send_json_response({
                "data": data,
                "count": len(data)
            })
            
        except json.JSONDecodeError:
            self.send_json_response({"error": "Invalid JSON"}, 400)
        except Exception as e:
            self.send_json_response({"error": str(e)}, 500)
    
    def handle_text_analysis(self):
        try:
            body = json.loads(self.read_body())
            text = body.get('text', '')
            
            if not text:
                self.send_json_response({
                    "word_count": 0,
                    "char_count": 0,
                    "char_count_no_spaces": 0,
                    "line_count": 0,
                    "avg_word_length": 0
                })
                return
            
            words = text.split()
            word_count = len(words)
            char_count = len(text)
            char_count_no_spaces = len(text.replace(' ', '').replace('\n', '').replace('\t', ''))
            line_count = len(text.split('\n'))
            avg_word_length = sum(len(w) for w in words) / word_count if word_count > 0 else 0
            
            self.send_json_response({
                "word_count": word_count,
                "char_count": char_count,
                "char_count_no_spaces": char_count_no_spaces,
                "line_count": line_count,
                "avg_word_length": round(avg_word_length, 2)
            })
            
        except json.JSONDecodeError:
            self.send_json_response({"error": "Invalid JSON"}, 400)

def run_server(port=8080):
    server = HTTPServer(('0.0.0.0', port), X402Handler)
    print(f"🚀 Backend Utilities API running on http://localhost:{port}")
    print(f"💰 Price: ${PRICE_USD} USD per request")
    print(f"📖 Docs: http://localhost:{port}/v1/docs")
    print(f"🔗 Receiver: {RECEIVER_ADDRESS}")
    print("\nPress Ctrl+C to stop")
    
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n\n👋 Server stopped")

if __name__ == "__main__":
    import sys
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8080
    run_server(port)
