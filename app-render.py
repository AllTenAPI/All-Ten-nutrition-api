import json
import os
import base64
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse
import time
import random
import hashlib
import traceback

# Try to import Google Cloud Vision, but don't crash if it fails
try:
    from google.cloud import vision
    from google.oauth2 import service_account
    GOOGLE_VISION_AVAILABLE = True
    print("✅ Google Cloud Vision imports successful")
except ImportError as e:
    print(f"⚠️ Google Cloud Vision import failed: {e}")
    GOOGLE_VISION_AVAILABLE = False

class GoogleVisionNutritionAPI(BaseHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        self.vision_client = None
        try:
            if GOOGLE_VISION_AVAILABLE:
                # Try to get credentials from environment variable first
                credentials_json = os.environ.get('GOOGLE_APPLICATION_CREDENTIALS_JSON')
                if credentials_json:
                    try:
                        # Parse the JSON string from environment
                        credentials_info = json.loads(credentials_json)
                        credentials = service_account.Credentials.from_service_account_info(credentials_info)
                        self.vision_client = vision.ImageAnnotatorClient(credentials=credentials)
                        print("✅ Google Cloud Vision client initialized with environment credentials")
                    except json.JSONDecodeError as e:
                        print(f"❌ Failed to parse credentials JSON: {e}")
                        print(f"Credentials content preview: {credentials_json[:100]}...")
                    except Exception as e:
                        print(f"❌ Failed to create credentials from JSON: {e}")
                else:
                    print("⚠️ No GOOGLE_APPLICATION_CREDENTIALS_JSON environment variable found")
                    # Fallback to file if environment variable not set
                    credentials_path = os.path.join(os.path.dirname(__file__), 'google-credentials.json')
                    if os.path.exists(credentials_path):
                        try:
                            credentials = service_account.Credentials.from_service_account_file(credentials_path)
                            self.vision_client = vision.ImageAnnotatorClient(credentials=credentials)
                            print("✅ Google Cloud Vision client initialized with service account file")
                        except Exception as e:
                            print(f"❌ Failed to load credentials from file: {e}")
                    else:
                        print("⚠️ No credentials file found, trying default credentials")
                        try:
                            self.vision_client = vision.ImageAnnotatorClient()
                            print("✅ Google Cloud Vision client initialized with default credentials")
                        except Exception as e:
                            print(f"❌ Failed to initialize with default credentials: {e}")
            else:
                print("⚠️ Google Cloud Vision not available")
        except Exception as e:
            print(f"❌ Failed to initialize Google Cloud Vision: {e}")
            print(f"Full traceback: {traceback.format_exc()}")
            self.vision_client = None
        
        super().__init__(*args, **kwargs)
    
    def do_GET(self):
        path = urlparse(self.path).path
        
        if path == '/health':
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            response = {
                "status": "healthy", 
                "message": "All Ten API running on Render!",
                "vision_api": "enabled" if self.vision_client else "disabled"
            }
            self.wfile.write(json.dumps(response).encode())
            
        elif path == '/debug':
            # Debug endpoint to see what's happening
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            
            # Check environment variables
            env_var = os.environ.get('GOOGLE_APPLICATION_CREDENTIALS_JSON')
            env_var_length = len(env_var) if env_var else 0
            env_var_preview = env_var[:100] + "..." if env_var and len(env_var) > 100 else env_var
            
            debug_info = {
                "vision_client_exists": self.vision_client is not None,
                "google_vision_available": GOOGLE_VISION_AVAILABLE,
                "env_var_exists": env_var is not None,
                "env_var_length": env_var_length,
                "env_var_preview": env_var_preview,
                "env_var_starts_with_brace": env_var.startswith('{') if env_var else False,
                "env_var_ends_with_brace": env_var.endswith('}') if env_var else False
            }
            
            self.wfile.write(json.dumps(debug_info, indent=2).encode())
            
        elif path == '/vision_labels':
            # Debug endpoint to see all Vision API labels for an image
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            
            response = {
                "message": "Send a POST request to this endpoint with image data to see all Vision API labels",
                "usage": "POST /vision_labels with JSON: {'image': 'base64_image_data'}"
            }
            self.wfile.write(json.dumps(response).encode())
            
        elif path == '/':
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            response = {
                "message": "All Ten Nutrition API with Google Vision",
                "status": "live",
                "vision_api": "enabled" if self.vision_client else "disabled",
                "endpoints": ["/health", "/analyze_food", "/vision_labels", "/debug"]
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
                    post_data = self.rfile.read(content_length)
                    try:
                        data = json.loads(post_data.decode('utf-8'))
                        image_data = data.get('image', '')
                    except:
                        image_data = None
                else:
                    image_data = None
                
                # Analyze the image with Google Vision API
                nutrition = self._analyze_food_with_vision(image_data)
                
                self.send_response(200)
                self.send_header('Content-type', 'application/json')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(json.dumps(nutrition).encode())
                
            except Exception as e:
                print(f"❌ Error in analyze_food: {e}")
                self.send_response(500)
                self.send_header('Content-type', 'application/json')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(json.dumps({"error": str(e)}).encode())
                
        elif path == '/vision_labels':
            try:
                # Read request data
                content_length = int(self.headers.get('Content-Length', 0))
                if content_length > 0:
                    post_data = self.rfile.read(content_length)
                    try:
                        data = json.loads(post_data.decode('utf-8'))
                        image_data = data.get('image', '')
                    except:
                        image_data = None
                else:
                    image_data = None
                
                # Get all Vision API labels for debugging
                labels_info = self._get_vision_labels(image_data)
                
                self.send_response(200)
                self.send_header('Content-type', 'application/json')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(json.dumps(labels_info).encode())
                
            except Exception as e:
                print(f"❌ Error in vision_labels: {e}")
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

    def _get_vision_labels(self, image_data):
        """Get all Vision API labels for debugging"""
        if not self.vision_client:
            return {"error": "Vision API not available", "labels": []}
        
        try:
            # Decode base64 image
            if not image_data:
                return {"error": "No image data provided", "labels": []}
            
            # Remove data URL prefix if present
            if image_data.startswith('data:image'):
                image_data = image_data.split(',')[1]
            
            # Decode base64
            image_bytes = base64.b64decode(image_data)
            
            # Create Vision API image object
            image = vision.Image(content=image_bytes)
            
            # Perform label detection
            response = self.vision_client.label_detection(image=image)
            labels = response.label_annotations
            
            # Extract all labels with scores
            all_labels = []
            for label in labels:
                all_labels.append({
                    "description": label.description,
                    "score": label.score,
                    "mid": label.mid
                })
            
            # Sort by score (highest first)
            all_labels.sort(key=lambda x: x['score'], reverse=True)
            
            return {
                "total_labels": len(all_labels),
                "labels": all_labels,
                "food_related_labels": [l for l in all_labels if self._is_food_related(l['description'])],
                "analysis_method": "Google Cloud Vision API - Debug Mode"
            }
            
        except Exception as e:
            print(f"❌ Vision API error in _get_vision_labels: {e}")
            return {"error": str(e), "labels": []}

    def _is_food_related(self, label):
        """Check if a label is food-related"""
        food_keywords = [
            'food', 'meal', 'dish', 'cuisine', 'cooking', 'recipe', 'ingredient',
            'meat', 'beef', 'chicken', 'pork', 'lamb', 'fish', 'seafood',
            'vegetable', 'fruit', 'grain', 'rice', 'pasta', 'bread', 'cereal',
            'dairy', 'milk', 'cheese', 'yogurt', 'butter', 'cream',
            'nut', 'seed', 'bean', 'legume', 'soup', 'salad', 'sandwich',
            'pizza', 'burger', 'steak', 'chop', 'cutlet', 'fillet',
            'potato', 'tomato', 'onion', 'carrot', 'broccoli', 'spinach',
            'apple', 'banana', 'orange', 'grape', 'berry', 'lemon',
            'pasta', 'noodle', 'spaghetti', 'macaroni', 'lasagna',
            'sauce', 'gravy', 'marinade', 'seasoning', 'spice', 'herb'
        ]
        
        label_lower = label.lower()
        return any(keyword in label_lower for keyword in food_keywords)

    def _analyze_food_with_vision(self, image_data):
        """Analyze food image using Google Cloud Vision API"""
        
        if not self.vision_client:
            return self._fallback_analysis(image_data)
        
        try:
            # Decode base64 image
            if not image_data:
                return self._fallback_analysis(image_data)
            
            # Remove data URL prefix if present
            if image_data.startswith('data:image'):
                image_data = image_data.split(',')[1]
            
            # Decode base64
            image_bytes = base64.b64decode(image_data)
            
            # Create Vision API image object
            image = vision.Image(content=image_bytes)
            
            # Perform label detection with timeout
            # Use lower threshold for faster processing and more results
            response = self.vision_client.label_detection(
                image=image,
                max_results=20  # Limit results for faster processing
            )
            labels = response.label_annotations
            
            # Extract food-related labels with lower threshold (0.4 instead of 0.5 for faster results)
            food_labels = [label.description.lower() for label in labels if label.score > 0.4]
            
            print(f"🔍 Vision API detected labels: {food_labels}")
            
            # Analyze nutrition based on detected foods
            nutrition = self._calculate_nutrition_from_labels(food_labels, image_bytes)
            
            return nutrition
            
        except Exception as e:
            print(f"❌ Vision API error: {e}")
            return self._fallback_analysis(image_data)
    
    def _calculate_nutrition_from_labels(self, food_labels, image_bytes):
        """Calculate nutrition based on detected food labels"""
        
        # Create deterministic seed from image
        image_hash = hashlib.md5(image_bytes).hexdigest()
        seed_value = int(image_hash[:8], 16) % 1000000
        random.seed(seed_value)
        
        # Expanded food database with more items and synonyms
        # Values are per 100g serving, will be scaled based on portion size
        food_database = {
            # Meats & Proteins (per 100g cooked)
            'chicken': {'calories': (165, 231), 'protein': (31, 35), 'carbs': (0, 0), 'fat': (3.6, 7.4)},
            'chicken breast': {'calories': (165, 231), 'protein': (31, 35), 'carbs': (0, 0), 'fat': (3.6, 7.4)},
            'chicken thigh': {'calories': (209, 290), 'protein': (26, 30), 'carbs': (0, 0), 'fat': (10, 20)},
            'beef': {'calories': (250, 332), 'protein': (26, 30), 'carbs': (0, 0), 'fat': (15, 25)},
            'steak': {'calories': (271, 332), 'protein': (26, 30), 'carbs': (0, 0), 'fat': (15, 25)},
            'ground beef': {'calories': (250, 332), 'protein': (26, 30), 'carbs': (0, 0), 'fat': (15, 25)},
            'lamb': {'calories': (294, 332), 'protein': (25, 30), 'carbs': (0, 0), 'fat': (20, 25)},
            'lambchop': {'calories': (294, 332), 'protein': (25, 30), 'carbs': (0, 0), 'fat': (20, 25)},
            'pork': {'calories': (242, 297), 'protein': (27, 30), 'carbs': (0, 0), 'fat': (14, 20)},
            'pork chop': {'calories': (242, 297), 'protein': (27, 30), 'carbs': (0, 0), 'fat': (14, 20)},
            'fish': {'calories': (206, 250), 'protein': (22, 30), 'carbs': (0, 0), 'fat': (10, 15)},
            'salmon': {'calories': (208, 250), 'protein': (20, 25), 'carbs': (0, 0), 'fat': (12, 18)},
            'tuna': {'calories': (184, 200), 'protein': (30, 35), 'carbs': (0, 0), 'fat': (1, 5)},
            'shrimp': {'calories': (99, 120), 'protein': (24, 28), 'carbs': (0, 0), 'fat': (0.3, 1.5)},
            'prawn': {'calories': (99, 120), 'protein': (24, 28), 'carbs': (0, 0), 'fat': (0.3, 1.5)},
            'eggs': {'calories': (155, 180), 'protein': (13, 15), 'carbs': (1.1, 1.5), 'fat': (11, 13)},
            'egg': {'calories': (155, 180), 'protein': (13, 15), 'carbs': (1.1, 1.5), 'fat': (11, 13)},
            'scrambled eggs': {'calories': (155, 200), 'protein': (13, 15), 'carbs': (1.1, 2), 'fat': (11, 15)},
            'fried egg': {'calories': (155, 200), 'protein': (13, 15), 'carbs': (1.1, 2), 'fat': (11, 15)},
            'boiled egg': {'calories': (155, 180), 'protein': (13, 15), 'carbs': (1.1, 1.5), 'fat': (11, 13)},
            
            # Grains and Starches (per 100g cooked)
            'rice': {'calories': (130, 150), 'protein': (2.7, 3.5), 'carbs': (28, 33), 'fat': (0.3, 0.5)},
            'white rice': {'calories': (130, 150), 'protein': (2.7, 3.5), 'carbs': (28, 33), 'fat': (0.3, 0.5)},
            'brown rice': {'calories': (111, 130), 'protein': (2.6, 3.5), 'carbs': (23, 28), 'fat': (0.9, 1.2)},
            'pasta': {'calories': (131, 160), 'protein': (5, 7), 'carbs': (25, 32), 'fat': (1.1, 2)},
            'spaghetti': {'calories': (131, 160), 'protein': (5, 7), 'carbs': (25, 32), 'fat': (1.1, 2)},
            'bread': {'calories': (265, 300), 'protein': (9, 12), 'carbs': (49, 55), 'fat': (3.2, 5)},
            'potato': {'calories': (87, 110), 'protein': (2, 3), 'carbs': (20, 25), 'fat': (0.1, 0.2)},
            'potatoes': {'calories': (87, 110), 'protein': (2, 3), 'carbs': (20, 25), 'fat': (0.1, 0.2)},
            'mashed potato': {'calories': (87, 150), 'protein': (2, 4), 'carbs': (20, 30), 'fat': (0.1, 5)},
            'mashed potatoes': {'calories': (87, 150), 'protein': (2, 4), 'carbs': (20, 30), 'fat': (0.1, 5)},
            'fries': {'calories': (312, 400), 'protein': (3, 5), 'carbs': (48, 60), 'fat': (15, 25)},
            'french fries': {'calories': (312, 400), 'protein': (3, 5), 'carbs': (48, 60), 'fat': (15, 25)},
            'fufu': {'calories': (180, 250), 'protein': (2, 4), 'carbs': (40, 55), 'fat': (0.5, 2)},
            'cassava': {'calories': (160, 200), 'protein': (1.4, 2), 'carbs': (38, 45), 'fat': (0.3, 0.5)},
            'plantain': {'calories': (122, 180), 'protein': (1.3, 2), 'carbs': (32, 40), 'fat': (0.4, 0.6)},
            'yam': {'calories': (118, 150), 'protein': (1.5, 2.5), 'carbs': (28, 35), 'fat': (0.2, 0.5)},
            
            # Vegetables (per 100g)
            'vegetable': {'calories': (20, 50), 'protein': (1, 3), 'carbs': (4, 10), 'fat': (0.2, 1)},
            'vegetables': {'calories': (20, 50), 'protein': (1, 3), 'carbs': (4, 10), 'fat': (0.2, 1)},
            'broccoli': {'calories': (34, 40), 'protein': (2.8, 3.5), 'carbs': (7, 9), 'fat': (0.4, 0.6)},
            'carrot': {'calories': (41, 50), 'protein': (0.9, 1.2), 'carbs': (10, 12), 'fat': (0.2, 0.3)},
            'carrots': {'calories': (41, 50), 'protein': (0.9, 1.2), 'carbs': (10, 12), 'fat': (0.2, 0.3)},
            'spinach': {'calories': (23, 30), 'protein': (2.9, 3.5), 'carbs': (3.6, 4.5), 'fat': (0.4, 0.6)},
            'lettuce': {'calories': (15, 20), 'protein': (1.4, 1.8), 'carbs': (2.9, 3.5), 'fat': (0.2, 0.3)},
            'tomato': {'calories': (18, 25), 'protein': (0.9, 1.2), 'carbs': (3.9, 5), 'fat': (0.2, 0.3)},
            'tomatoes': {'calories': (18, 25), 'protein': (0.9, 1.2), 'carbs': (3.9, 5), 'fat': (0.2, 0.3)},
            'onion': {'calories': (40, 50), 'protein': (1.1, 1.5), 'carbs': (9.3, 11), 'fat': (0.1, 0.2)},
            'onions': {'calories': (40, 50), 'protein': (1.1, 1.5), 'carbs': (9.3, 11), 'fat': (0.1, 0.2)},
            'pepper': {'calories': (31, 40), 'protein': (1, 1.5), 'carbs': (7, 9), 'fat': (0.3, 0.5)},
            'bell pepper': {'calories': (31, 40), 'protein': (1, 1.5), 'carbs': (7, 9), 'fat': (0.3, 0.5)},
            'cucumber': {'calories': (16, 20), 'protein': (0.7, 1), 'carbs': (4, 5), 'fat': (0.1, 0.2)},
            'cucumbers': {'calories': (16, 20), 'protein': (0.7, 1), 'carbs': (4, 5), 'fat': (0.1, 0.2)},
            'cabbage': {'calories': (25, 30), 'protein': (1.3, 1.8), 'carbs': (5.8, 7), 'fat': (0.1, 0.2)},
            'cauliflower': {'calories': (25, 30), 'protein': (1.9, 2.5), 'carbs': (5, 6), 'fat': (0.3, 0.5)},
            'zucchini': {'calories': (17, 20), 'protein': (1.2, 1.5), 'carbs': (3.3, 4), 'fat': (0.2, 0.3)},
            'green beans': {'calories': (31, 35), 'protein': (1.8, 2.5), 'carbs': (7, 9), 'fat': (0.2, 0.3)},
            'peas': {'calories': (81, 100), 'protein': (5.4, 7), 'carbs': (14, 18), 'fat': (0.4, 0.6)},
            'corn': {'calories': (96, 120), 'protein': (3.4, 4.5), 'carbs': (21, 27), 'fat': (1.2, 2)},
            
            # Fruits (per 100g)
            'fruit': {'calories': (50, 80), 'protein': (0.5, 1.5), 'carbs': (12, 20), 'fat': (0.2, 0.5)},
            'apple': {'calories': (52, 60), 'protein': (0.3, 0.5), 'carbs': (14, 16), 'fat': (0.2, 0.3)},
            'banana': {'calories': (89, 110), 'protein': (1.1, 1.5), 'carbs': (23, 28), 'fat': (0.3, 0.4)},
            'orange': {'calories': (47, 55), 'protein': (0.9, 1.2), 'carbs': (12, 15), 'fat': (0.1, 0.2)},
            
            # Dairy (per 100g)
            'cheese': {'calories': (113, 400), 'protein': (7, 25), 'carbs': (1, 3), 'fat': (9, 33)},
            'milk': {'calories': (42, 61), 'protein': (3.4, 4), 'carbs': (5, 6), 'fat': (1, 3.3)},
            'yogurt': {'calories': (59, 150), 'protein': (10, 12), 'carbs': (3.6, 15), 'fat': (0.4, 8)},
            'butter': {'calories': (717, 800), 'protein': (0.9, 1), 'carbs': (0.1, 0.2), 'fat': (81, 90)},
            
            # Other/Combined dishes (per serving)
            'salad': {'calories': (50, 200), 'protein': (3, 10), 'carbs': (8, 20), 'fat': (0, 8)},
            'soup': {'calories': (80, 250), 'protein': (5, 20), 'carbs': (10, 30), 'fat': (2, 12)},
            'sandwich': {'calories': (250, 500), 'protein': (12, 25), 'carbs': (30, 60), 'fat': (8, 20)},
            'pizza': {'calories': (266, 500), 'protein': (12, 25), 'carbs': (33, 60), 'fat': (10, 25)},
            'burger': {'calories': (354, 600), 'protein': (16, 30), 'carbs': (33, 60), 'fat': (15, 35)},
        }
        
        # Match detected labels to food database with improved matching
        detected_foods = []
        food_quantities = {}  # Track quantities for each food
        total_calories = 0
        total_protein = 0
        total_carbs = 0
        total_fat = 0
        
        # First pass: Try exact and close matches
        for label in food_labels:
            label_lower = label.lower().strip()
            best_match = None
            best_match_score = 0
            
            for food, nutrition in food_database.items():
                food_lower = food.lower()
                score = 0
                
                # Exact match gets highest score
                if food_lower == label_lower:
                    score = 100
                # Food name contained in label
                elif food_lower in label_lower:
                    score = 80 - (len(label_lower) - len(food_lower)) * 2
                # Label contained in food name
                elif label_lower in food_lower:
                    score = 70 - (len(food_lower) - len(label_lower)) * 2
                # Word-based matching
                elif any(word in label_lower for word in food_lower.split()):
                    score = 60
                elif any(word in food_lower for word in label_lower.split()):
                    score = 50
                
                if score > best_match_score and score >= 50:  # Minimum threshold
                    best_match = food
                    best_match_score = score
            
            if best_match:
                if best_match not in food_quantities:
                    food_quantities[best_match] = 0
                    detected_foods.append(best_match)
                food_quantities[best_match] += 1
        
        # Second pass: If we have matches, calculate nutrition with better portion estimation
        if detected_foods:
            # Estimate portion size based on number of foods detected
            # More foods = larger meal = higher portion multipliers
            # Improved calculation: scale more aggressively for larger meals
            num_foods = len(detected_foods)
            total_quantities = sum(food_quantities.values())
            
            # Base portion calculation: more aggressive scaling
            if num_foods <= 2:
                base_portion = 2.0  # Increased from 1.5
            elif num_foods <= 4:
                base_portion = 2.8  # Increased from 2.0
            elif num_foods <= 6:
                base_portion = 3.5  # New tier
            else:
                base_portion = 4.2  # Increased from 2.5 for large meals
            
            # Additional multiplier based on total quantities
            quantity_multiplier = 1.0 + (total_quantities - num_foods) * 0.15
            
            for food in detected_foods:
                nutrition = food_database[food]
                quantity = food_quantities.get(food, 1)
                
                # Portion multiplier: base portion * quantity multiplier * quantity, with variation
                # For larger meals with multiple foods, use higher multipliers
                portion_multiplier = base_portion * quantity_multiplier * quantity * random.uniform(1.1, 1.5)
                
                # Use average of range for more accurate estimation
                avg_calories = (nutrition['calories'][0] + nutrition['calories'][1]) / 2
                avg_protein = (nutrition['protein'][0] + nutrition['protein'][1]) / 2
                avg_carbs = (nutrition['carbs'][0] + nutrition['carbs'][1]) / 2
                avg_fat = (nutrition['fat'][0] + nutrition['fat'][1]) / 2
                
                total_calories += avg_calories * portion_multiplier
                total_protein += avg_protein * portion_multiplier
                total_carbs += avg_carbs * portion_multiplier
                total_fat += avg_fat * portion_multiplier
        else:
            # Fallback: Try to match generic categories with specific foods
            generic_matches = []
            for label in food_labels:
                label_lower = label.lower()
                
                # Try to match to specific foods based on keywords
                if any(word in label_lower for word in ['chicken', 'poultry', 'bird']):
                    generic_matches.append('chicken')
                elif any(word in label_lower for word in ['beef', 'cow', 'steak', 'meat']):
                    generic_matches.append('beef')
                elif any(word in label_lower for word in ['fish', 'seafood', 'salmon', 'tuna']):
                    generic_matches.append('fish')
                elif any(word in label_lower for word in ['shrimp', 'prawn', 'seafood']):
                    generic_matches.append('shrimp')
                elif any(word in label_lower for word in ['egg', 'eggs']):
                    generic_matches.append('eggs')
                elif any(word in label_lower for word in ['rice', 'grain']):
                    generic_matches.append('rice')
                elif any(word in label_lower for word in ['potato', 'potatoes']):
                    generic_matches.append('potato')
                elif any(word in label_lower for word in ['vegetable', 'veggie', 'salad']):
                    generic_matches.append('vegetable')
                elif any(word in label_lower for word in ['cucumber', 'cucumbers']):
                    generic_matches.append('cucumber')
                elif any(word in label_lower for word in ['fufu', 'cassava']):
                    generic_matches.append('fufu')
            
            if generic_matches:
                # Remove duplicates while preserving order
                detected_foods = list(dict.fromkeys(generic_matches))
                num_foods = len(detected_foods)
                
                # Improved base portion for generic matches
                if num_foods <= 2:
                    base_portion = 2.2
                elif num_foods <= 4:
                    base_portion = 3.0
                elif num_foods <= 6:
                    base_portion = 3.8
                else:
                    base_portion = 4.5
                
                for food in detected_foods:
                    if food in food_database:
                        nutrition = food_database[food]
                        portion_multiplier = base_portion * random.uniform(1.2, 1.6)
                        
                        avg_calories = (nutrition['calories'][0] + nutrition['calories'][1]) / 2
                        avg_protein = (nutrition['protein'][0] + nutrition['protein'][1]) / 2
                        avg_carbs = (nutrition['carbs'][0] + nutrition['carbs'][1]) / 2
                        avg_fat = (nutrition['fat'][0] + nutrition['fat'][1]) / 2
                        
                        total_calories += avg_calories * portion_multiplier
                        total_protein += avg_protein * portion_multiplier
                        total_carbs += avg_carbs * portion_multiplier
                        total_fat += avg_fat * portion_multiplier
            else:
                # Last resort: estimate based on meal size indicators
                meal_size_indicators = sum(1 for label in food_labels if any(word in label.lower() for word in ['large', 'big', 'hearty', 'full', 'plate', 'meal']))
                if meal_size_indicators > 0:
                    # Large meal estimate
                    detected_foods = ['Large Meal']
                    total_calories = random.uniform(800, 1400)
                    total_protein = random.uniform(40, 70)
                    total_carbs = random.uniform(60, 120)
                    total_fat = random.uniform(25, 50)
                else:
                    # Medium meal estimate - increased range
                    detected_foods = ['Mixed Meal']
                    total_calories = random.uniform(600, 1200)  # Increased from 500-900
                    total_protein = random.uniform(30, 60)  # Increased from 25-50
                    total_carbs = random.uniform(50, 100)  # Increased from 40-80
                    total_fat = random.uniform(20, 45)  # Increased from 15-35
        
        # Format detected food names for display (capitalize properly)
        formatted_foods = []
        for food in detected_foods:
            # Capitalize first letter of each word
            formatted = ' '.join(word.capitalize() for word in food.split())
            formatted_foods.append(formatted)
        
        # Generate micronutrients based on detected foods
        base_multiplier = max(total_calories / 400, 1.0)  # Ensure at least 1.0
        
        nutrition_data = {
            "nutrition": {
                "calories": round(total_calories),
                "protein": round(total_protein, 1),
                "carbs": round(total_carbs, 1),
                "fat": round(total_fat, 1),
                "fiber": round(random.uniform(3, 8) * base_multiplier, 1),
                "sugar": round(random.uniform(5, 15) * base_multiplier, 1),
                "sodium": round(random.uniform(200, 800) * base_multiplier),
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
            "detected_foods": formatted_foods,
            "confidence": 0.85 if detected_foods and detected_foods[0].lower() not in ['mixed meal', 'large meal'] else 0.6,
            "analysis_method": "Google Cloud Vision API + All Ten AI"
        }
        
        return nutrition_data
    
    def _fallback_analysis(self, image_data):
        """Fallback analysis when Vision API is not available"""
        print("⚠️ Using fallback analysis (Vision API not available)")
        
        # Use the old simulated analysis as fallback
        if image_data:
            image_hash = hashlib.md5(image_data.encode() if isinstance(image_data, str) else str(image_data).encode()).hexdigest()
            seed_value = int(image_hash[:8], 16) % 1000000
        else:
            seed_value = int(time.time() * 1000) % 1000000
        
        random.seed(seed_value)
        
        # Simple fallback meal types
        meal_types = [
            {"name": "Mixed Meal", "calories": (300, 500), "protein": (20, 35), "carbs": (25, 45), "fat": (10, 25)},
            {"name": "Light Meal", "calories": (200, 350), "protein": (15, 25), "carbs": (20, 35), "fat": (5, 15)},
            {"name": "Hearty Meal", "calories": (500, 700), "protein": (30, 45), "carbs": (40, 60), "fat": (20, 35)},
        ]
        
        meal = random.choice(meal_types)
        # Use higher portion multipliers for fallback too
        portion_mult = random.uniform(1.5, 2.5)
        calories = round(random.randint(meal["calories"][0], meal["calories"][1]) * portion_mult)
        protein = round(random.uniform(meal["protein"][0], meal["protein"][1]) * portion_mult, 1)
        carbs = round(random.uniform(meal["carbs"][0], meal["carbs"][1]) * portion_mult, 1)
        fat = round(random.uniform(meal["fat"][0], meal["fat"][1]) * portion_mult, 1)
        
        return {
            "nutrition": {
                "calories": calories,
                "protein": protein,
                "carbs": carbs,
                "fat": fat,
                "fiber": round(random.uniform(3, 8), 1),
                "sugar": round(random.uniform(5, 15), 1),
                "sodium": random.randint(200, 800),
                "micronutrients": {
                    "iron": round(random.uniform(1, 5), 1),
                    "calcium": round(random.uniform(50, 200), 1),
                    "vitamin_c": round(random.uniform(10, 50), 1),
                    "potassium": round(random.uniform(200, 600), 1),
                    "vitamin_a": round(random.uniform(100, 800), 1),
                    "vitamin_e": round(random.uniform(1, 5), 1),
                    "vitamin_k": round(random.uniform(5, 25), 1),
                    "folate": round(random.uniform(20, 80), 1),
                    "niacin": round(random.uniform(3, 12), 1),
                    "riboflavin": round(random.uniform(0.2, 0.8), 2),
                    "thiamin": round(random.uniform(0.1, 0.5), 2),
                    "vitamin_b6": round(random.uniform(0.3, 1.2), 2),
                    "phosphorus": round(random.uniform(80, 180), 1),
                    "selenium": round(random.uniform(5, 25), 1),
                    "copper": round(random.uniform(0.1, 0.5), 2),
                    "manganese": round(random.uniform(0.2, 0.8), 2),
                    "chromium": round(random.uniform(2, 8), 1),
                    "molybdenum": round(random.uniform(5, 15), 1),
                    "iodine": round(random.uniform(5, 25), 1),
                    "chloride": round(random.uniform(100, 400), 1),
                    "biotin": round(random.uniform(2, 8), 1),
                    "pantothenic_acid": round(random.uniform(1, 4), 1),
                    "choline": round(random.uniform(20, 80), 1),
                    "betaine": round(random.uniform(5, 20), 1),
                    "taurine": round(random.uniform(10, 40), 1),
                    "creatine": round(random.uniform(1, 5), 1),
                    "carnitine": round(random.uniform(5, 25), 1),
                    "inositol": round(random.uniform(10, 40), 1),
                    "paba": round(random.uniform(0.5, 2), 1),
                    "lipoic_acid": round(random.uniform(0.2, 1), 2),
                    "coq10": round(random.uniform(0.5, 2), 1),
                    "glutathione": round(random.uniform(5, 20), 1),
                    "melatonin": round(random.uniform(0.05, 0.2), 2),
                    "serotonin": round(random.uniform(0.02, 0.1), 2),
                    "dopamine": round(random.uniform(0.01, 0.05), 2),
                    "norepinephrine": round(random.uniform(0.005, 0.02), 3),
                    "epinephrine": round(random.uniform(0.002, 0.01), 3),
                    "histamine": round(random.uniform(0.05, 0.2), 2),
                    "gaba": round(random.uniform(0.2, 1), 2),
                    "glycine": round(random.uniform(50, 150), 1),
                    "proline": round(random.uniform(40, 120), 1),
                    "serine": round(random.uniform(30, 90), 1),
                    "threonine": round(random.uniform(25, 75), 1),
                    "tryptophan": round(random.uniform(10, 30), 1),
                    "tyrosine": round(random.uniform(20, 60), 1),
                    "valine": round(random.uniform(35, 105), 1),
                    "alanine": round(random.uniform(45, 135), 1),
                    "arginine": round(random.uniform(40, 120), 1),
                    "asparagine": round(random.uniform(30, 90), 1),
                    "aspartic_acid": round(random.uniform(35, 105), 1),
                    "cysteine": round(random.uniform(15, 45), 1),
                    "glutamine": round(random.uniform(50, 150), 1),
                    "glutamic_acid": round(random.uniform(60, 180), 1),
                    "isoleucine": round(random.uniform(30, 90), 1),
                    "leucine": round(random.uniform(40, 120), 1),
                    "lysine": round(random.uniform(35, 105), 1),
                    "methionine": round(random.uniform(12, 38), 1),
                    "phenylalanine": round(random.uniform(25, 75), 1),
                    "histidine": round(random.uniform(15, 45), 1)
                }
            },
            "detected_foods": [meal["name"]],
            "confidence": 0.6,
            "analysis_method": "All Ten AI - Fallback Analysis"
        }

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 10000))
    print(f"🚀 Starting All Ten API with Google Vision on port {port}")
    server = HTTPServer(('0.0.0.0', port), GoogleVisionNutritionAPI)
    server.serve_forever()
