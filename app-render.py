#!/usr/bin/env python3
"""
Simple All Ten Nutrition API for Render
Ultra-minimal version with zero dependencies
"""

import json
import os
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse

class SimpleNutritionAPI(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        print(f"[API] {format % args}")
    
    def do_GET(self):
        path = urlparse(self.path).path
        
        if path == '/health':
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            response = {"status": "healthy", "message": "All Ten API running on Render!"}
            self.wfile.write(json.dumps(response).encode())
            
        elif path == '/':
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            response = {
                "message": "All Ten Nutrition API",
                "status": "live",
                "endpoints": ["/health", "/analyze_food"]
            }
            self.wfile.write(json.dumps(response).encode())
            
        else:
            self.send_response(404)
            self.send_header('Content-type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(b'{"error": "Not found"}')
    
    def do_POST(self):
        path = urlparse(self.path).path
        
        if path == '/analyze_food':
            try:
                # Read request data
                content_length = int(self.headers.get('Content-Length', 0))
                if content_length > 0:
                    self.rfile.read(content_length)
                
                # Return nutrition data
                nutrition = {
                    "nutrition": {
                        "calories": 250, "protein": 15, "carbs": 30, "fat": 8,
                        "fiber": 5, "sugar": 12, "sodium": 300,
                        "micronutrients": {
                            "iron": 2.5, "calcium": 150, "vitamin_c": 25, "potassium": 400,
                            "vitamin_a": 500, "vitamin_e": 3, "vitamin_k": 15, "folate": 50,
                            "niacin": 8, "riboflavin": 0.5, "thiamin": 0.3, "vitamin_b6": 0.8,
                            "phosphorus": 120, "selenium": 15, "copper": 0.2, "manganese": 0.5,
                            "chromium": 5, "molybdenum": 10, "iodine": 15, "chloride": 200,
                            "biotin": 5, "pantothenic_acid": 2, "choline": 50, "betaine": 10,
                            "taurine": 20, "creatine": 2, "carnitine": 15, "inositol": 25,
                            "paba": 1, "lipoic_acid": 0.5, "coq10": 1, "glutathione": 10,
                            "melatonin": 0.1, "serotonin": 0.05, "dopamine": 0.02, "norepinephrine": 0.01,
                            "epinephrine": 0.005, "histamine": 0.1, "gaba": 0.5, "glycine": 100,
                            "proline": 80, "serine": 60, "threonine": 50, "tryptophan": 20,
                            "tyrosine": 40, "valine": 70, "alanine": 90, "arginine": 80,
                            "asparagine": 60, "aspartic_acid": 70, "cysteine": 30, "glutamine": 100,
                            "glutamic_acid": 120, "isoleucine": 60, "leucine": 80, "lysine": 70,
                            "methionine": 25, "phenylalanine": 50, "histidine": 30
                        }
                    },
                    "detected_foods": ["Sample Food"]
                }
                
                self.send_response(200)
                self.send_header('Content-type', 'application/json')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(json.dumps(nutrition).encode())
                
            except Exception as e:
                self.send_response(500)
                self.send_header('Content-type', 'application/json')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(json.dumps({"error": str(e)}).encode())
        else:
            self.send_response(404)
            self.send_header('Content-type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(b'{"error": "Not found"}')
    
    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 10000))
    print(f"🚀 Starting All Ten API on port {port}")
    server = HTTPServer(('0.0.0.0', port), SimpleNutritionAPI)
    server.serve_forever() 