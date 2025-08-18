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
                
                # Generate varied nutrition data based on timestamp to simulate different meals
                import time
                import random
                random.seed(int(time.time() * 1000) % 1000000)  # Use timestamp for variation
                
                # Define different meal types with realistic ranges
                meal_types = [
                    {
                        "name": "Grilled Chicken Salad",
                        "calories": (200, 350), "protein": (25, 35), "carbs": (10, 20), "fat": (5, 15),
                        "fiber": (5, 8), "sugar": (5, 10), "sodium": (300, 600)
                    },
                    {
                        "name": "Pasta with Marinara Sauce",
                        "calories": (350, 500), "protein": (12, 18), "carbs": (60, 80), "fat": (8, 15),
                        "fiber": (4, 7), "sugar": (8, 15), "sodium": (400, 800)
                    },
                    {
                        "name": "Avocado Toast",
                        "calories": (250, 400), "protein": (8, 15), "carbs": (25, 40), "fat": (15, 25),
                        "fiber": (8, 12), "sugar": (3, 8), "sodium": (200, 500)
                    },
                    {
                        "name": "Greek Yogurt with Berries",
                        "calories": (150, 250), "protein": (15, 20), "carbs": (20, 30), "fat": (2, 8),
                        "fiber": (3, 6), "sugar": (15, 25), "sodium": (50, 150)
                    },
                    {
                        "name": "Burger and Fries",
                        "calories": (600, 900), "protein": (25, 35), "carbs": (45, 70), "fat": (25, 45),
                        "fiber": (3, 6), "sugar": (5, 12), "sodium": (800, 1500)
                    },
                    {
                        "name": "Salmon with Vegetables",
                        "calories": (300, 450), "protein": (30, 40), "carbs": (15, 25), "fat": (15, 25),
                        "fiber": (6, 10), "sugar": (8, 15), "sodium": (250, 600)
                    }
                ]
                
                # Select a random meal type
                meal = random.choice(meal_types)
                
                # Generate values within the realistic ranges
                calories = random.randint(meal["calories"][0], meal["calories"][1])
                protein = round(random.uniform(meal["protein"][0], meal["protein"][1]), 1)
                carbs = round(random.uniform(meal["carbs"][0], meal["carbs"][1]), 1)
                fat = round(random.uniform(meal["fat"][0], meal["fat"][1]), 1)
                fiber = round(random.uniform(meal["fiber"][0], meal["fiber"][1]), 1)
                sugar = round(random.uniform(meal["sugar"][0], meal["sugar"][1]), 1)
                sodium = random.randint(meal["sodium"][0], meal["sodium"][1])
                
                # Generate varied micronutrients based on meal type
                base_multiplier = calories / 300  # Scale micronutrients based on calories
                
                nutrition = {
                    "nutrition": {
                        "calories": calories, "protein": protein, "carbs": carbs, "fat": fat,
                        "fiber": fiber, "sugar": sugar, "sodium": sodium,
                        "micronutrients": {
                            "iron": round(random.uniform(1, 5) * base_multiplier, 1),
                            "calcium": round(random.uniform(50, 200) * base_multiplier, 1),
                            "vitamin_c": round(random.uniform(10, 50) * base_multiplier, 1),
                            "potassium": round(random.uniform(200, 600) * base_multiplier, 1),
                            "vitamin_a": round(random.uniform(100, 800) * base_multiplier, 1),
                            "vitamin_e": round(random.uniform(1, 5) * base_multiplier, 1),
                            "vitamin_k": round(random.uniform(5, 25) * base_multiplier, 1),
                            "folate": round(random.uniform(20, 80) * base_multiplier, 1),
                            "niacin": round(random.uniform(3, 12) * base_multiplier, 1),
                            "riboflavin": round(random.uniform(0.2, 0.8) * base_multiplier, 2),
                            "thiamin": round(random.uniform(0.1, 0.5) * base_multiplier, 2),
                            "vitamin_b6": round(random.uniform(0.3, 1.2) * base_multiplier, 2),
                            "phosphorus": round(random.uniform(80, 180) * base_multiplier, 1),
                            "selenium": round(random.uniform(5, 25) * base_multiplier, 1),
                            "copper": round(random.uniform(0.1, 0.5) * base_multiplier, 2),
                            "manganese": round(random.uniform(0.2, 0.8) * base_multiplier, 2),
                            "chromium": round(random.uniform(2, 8) * base_multiplier, 1),
                            "molybdenum": round(random.uniform(5, 15) * base_multiplier, 1),
                            "iodine": round(random.uniform(5, 25) * base_multiplier, 1),
                            "chloride": round(random.uniform(100, 400) * base_multiplier, 1),
                            "biotin": round(random.uniform(2, 8) * base_multiplier, 1),
                            "pantothenic_acid": round(random.uniform(1, 4) * base_multiplier, 1),
                            "choline": round(random.uniform(20, 80) * base_multiplier, 1),
                            "betaine": round(random.uniform(5, 20) * base_multiplier, 1),
                            "taurine": round(random.uniform(10, 40) * base_multiplier, 1),
                            "creatine": round(random.uniform(1, 5) * base_multiplier, 1),
                            "carnitine": round(random.uniform(5, 25) * base_multiplier, 1),
                            "inositol": round(random.uniform(10, 40) * base_multiplier, 1),
                            "paba": round(random.uniform(0.5, 2) * base_multiplier, 1),
                            "lipoic_acid": round(random.uniform(0.2, 1) * base_multiplier, 2),
                            "coq10": round(random.uniform(0.5, 2) * base_multiplier, 1),
                            "glutathione": round(random.uniform(5, 20) * base_multiplier, 1),
                            "melatonin": round(random.uniform(0.05, 0.2) * base_multiplier, 2),
                            "serotonin": round(random.uniform(0.02, 0.1) * base_multiplier, 2),
                            "dopamine": round(random.uniform(0.01, 0.05) * base_multiplier, 2),
                            "norepinephrine": round(random.uniform(0.005, 0.02) * base_multiplier, 3),
                            "epinephrine": round(random.uniform(0.002, 0.01) * base_multiplier, 3),
                            "histamine": round(random.uniform(0.05, 0.2) * base_multiplier, 2),
                            "gaba": round(random.uniform(0.2, 1) * base_multiplier, 2),
                            "glycine": round(random.uniform(50, 150) * base_multiplier, 1),
                            "proline": round(random.uniform(40, 120) * base_multiplier, 1),
                            "serine": round(random.uniform(30, 90) * base_multiplier, 1),
                            "threonine": round(random.uniform(25, 75) * base_multiplier, 1),
                            "tryptophan": round(random.uniform(10, 30) * base_multiplier, 1),
                            "tyrosine": round(random.uniform(20, 60) * base_multiplier, 1),
                            "valine": round(random.uniform(35, 105) * base_multiplier, 1),
                            "alanine": round(random.uniform(45, 135) * base_multiplier, 1),
                            "arginine": round(random.uniform(40, 120) * base_multiplier, 1),
                            "asparagine": round(random.uniform(30, 90) * base_multiplier, 1),
                            "aspartic_acid": round(random.uniform(35, 105) * base_multiplier, 1),
                            "cysteine": round(random.uniform(15, 45) * base_multiplier, 1),
                            "glutamine": round(random.uniform(50, 150) * base_multiplier, 1),
                            "glutamic_acid": round(random.uniform(60, 180) * base_multiplier, 1),
                            "isoleucine": round(random.uniform(30, 90) * base_multiplier, 1),
                            "leucine": round(random.uniform(40, 120) * base_multiplier, 1),
                            "lysine": round(random.uniform(35, 105) * base_multiplier, 1),
                            "methionine": round(random.uniform(12, 38) * base_multiplier, 1),
                            "phenylalanine": round(random.uniform(25, 75) * base_multiplier, 1),
                            "histidine": round(random.uniform(15, 45) * base_multiplier, 1)
                        }
                    },
                    "detected_foods": [meal["name"]]
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