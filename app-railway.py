#!/usr/bin/env python3
"""
Railway-optimized All Ten Nutrition API
Handles Railway's networking requirements
"""

import json
import os
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse
import threading
import time

class RailwayNutritionAPIHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        # Custom logging for Railway
        print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {format % args}")
    
    def do_GET(self):
        parsed_path = urlparse(self.path)
        
        if parsed_path.path == '/health':
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
            self.send_header('Access-Control-Allow-Headers', 'Content-Type')
            self.end_headers()
            response = {
                'status': 'healthy',
                'message': 'All Ten Nutrition API is running on Railway!',
                'timestamp': time.time()
            }
            self.wfile.write(json.dumps(response).encode())
            
        elif parsed_path.path == '/':
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
            self.send_header('Access-Control-Allow-Headers', 'Content-Type')
            self.end_headers()
            response = {
                'message': 'All Ten Nutrition API',
                'deployed_on': 'Railway',
                'endpoints': {
                    'health': '/health',
                    'analyze_food': '/analyze_food'
                }
            }
            self.wfile.write(json.dumps(response).encode())
            
        else:
            self.send_response(404)
            self.send_header('Content-type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            response = {'error': 'Endpoint not found', 'path': parsed_path.path}
            self.wfile.write(json.dumps(response).encode())
    
    def do_POST(self):
        parsed_path = urlparse(self.path)
        
        if parsed_path.path == '/analyze_food':
            try:
                content_length = int(self.headers.get('Content-Length', 0))
                if content_length > 0:
                    post_data = self.rfile.read(content_length)
                    data = json.loads(post_data.decode('utf-8'))
                else:
                    data = {}
                
                # Return simulated nutrition data
                nutrition_data = {
                    'nutrition': {
                        'calories': 250.0,
                        'protein': 15.0,
                        'carbs': 30.0,
                        'fat': 8.0,
                        'fiber': 5.0,
                        'sugar': 12.0,
                        'sodium': 300.0,
                        'micronutrients': {
                            'iron': 2.5,
                            'calcium': 150.0,
                            'vitamin_c': 25.0,
                            'potassium': 400.0,
                            'vitamin_a': 500.0,
                            'vitamin_e': 3.0,
                            'vitamin_k': 15.0,
                            'folate': 50.0,
                            'niacin': 8.0,
                            'riboflavin': 0.5,
                            'thiamin': 0.3,
                            'vitamin_b6': 0.8,
                            'phosphorus': 120.0,
                            'selenium': 15.0,
                            'copper': 0.2,
                            'manganese': 0.5,
                            'chromium': 5.0,
                            'molybdenum': 10.0,
                            'iodine': 15.0,
                            'chloride': 200.0,
                            'biotin': 5.0,
                            'pantothenic_acid': 2.0,
                            'choline': 50.0,
                            'betaine': 10.0,
                            'taurine': 20.0,
                            'creatine': 2.0,
                            'carnitine': 15.0,
                            'inositol': 25.0,
                            'paba': 1.0,
                            'lipoic_acid': 0.5,
                            'coq10': 1.0,
                            'glutathione': 10.0,
                            'melatonin': 0.1,
                            'serotonin': 0.05,
                            'dopamine': 0.02,
                            'norepinephrine': 0.01,
                            'epinephrine': 0.005,
                            'histamine': 0.1,
                            'gaba': 0.5,
                            'glycine': 100.0,
                            'proline': 80.0,
                            'serine': 60.0,
                            'threonine': 50.0,
                            'tryptophan': 20.0,
                            'tyrosine': 40.0,
                            'valine': 70.0,
                            'alanine': 90.0,
                            'arginine': 80.0,
                            'asparagine': 60.0,
                            'aspartic_acid': 70.0,
                            'cysteine': 30.0,
                            'glutamine': 100.0,
                            'glutamic_acid': 120.0,
                            'isoleucine': 60.0,
                            'leucine': 80.0,
                            'lysine': 70.0,
                            'methionine': 25.0,
                            'phenylalanine': 50.0,
                            'histidine': 30.0,
                        }
                    },
                    'detected_foods': ['Sample Food Item'],
                    'processed_at': time.time()
                }
                
                self.send_response(200)
                self.send_header('Content-type', 'application/json')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
                self.send_header('Access-Control-Allow-Headers', 'Content-Type')
                self.end_headers()
                self.wfile.write(json.dumps(nutrition_data).encode())
                
            except Exception as e:
                print(f"Error processing request: {e}")
                self.send_response(500)
                self.send_header('Content-type', 'application/json')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                response = {'error': str(e), 'timestamp': time.time()}
                self.wfile.write(json.dumps(response).encode())
        else:
            self.send_response(404)
            self.send_header('Content-type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            response = {'error': 'Endpoint not found', 'path': parsed_path.path}
            self.wfile.write(json.dumps(response).encode())
    
    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()

def run_server():
    # Get port from Railway environment
    port = int(os.environ.get('PORT', 5000))
    
    # Bind to all interfaces for Railway
    server_address = ('0.0.0.0', port)
    
    try:
        httpd = HTTPServer(server_address, RailwayNutritionAPIHandler)
        print(f'🚀 Starting All Ten Nutrition API on port {port}')
        print(f'📡 Server will be available at: http://0.0.0.0:{port}')
        print(f'🔗 Railway will provide the public URL')
        
        # Start server
        httpd.serve_forever()
    except Exception as e:
        print(f"❌ Error starting server: {e}")
        raise

if __name__ == '__main__':
    run_server() 