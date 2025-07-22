from flask import Flask, request, jsonify
from flask_cors import CORS
import base64
import numpy as np
from PIL import Image
import io
import json
import os

app = Flask(__name__)
CORS(app)

# Simple food recognition (you can replace with ML model)
FOOD_DATABASE = {
    "apple": {
        "calories": 95,
        "protein": 0.5,
        "carbs": 25,
        "fat": 0.3,
        "fiber": 4.4,
        "sugar": 19,
        "sodium": 2,
        "micronutrients": {
            "vitamin_c": 8.4,
            "iron": 0.2,
            "calcium": 11,
            "potassium": 195,
            "vitamin_a": 98,
            "vitamin_e": 0.3,
            "vitamin_k": 2.2,
            "folate": 3,
            "niacin": 0.1,
            "riboflavin": 0.1,
            "thiamin": 0.1,
            "vitamin_b6": 0.1,
            "phosphorus": 20,
            "selenium": 0,
            "copper": 0.1,
            "manganese": 0.1,
            "chromium": 0,
            "molybdenum": 0,
            "iodine": 0,
            "chloride": 0,
            "biotin": 0,
            "pantothenic_acid": 0.1,
            "choline": 6,
            "betaine": 0,
            "taurine": 0,
            "creatine": 0,
            "carnitine": 0,
            "inositol": 0,
            "paba": 0,
            "lipoic_acid": 0,
            "coq10": 0,
            "glutathione": 0,
            "melatonin": 0,
            "serotonin": 0,
            "dopamine": 0,
            "norepinephrine": 0,
            "epinephrine": 0,
            "histamine": 0,
            "gaba": 0,
            "glycine": 0,
            "proline": 0,
            "serine": 0,
            "threonine": 0,
            "tryptophan": 0,
            "tyrosine": 0,
            "valine": 0,
            "alanine": 0,
            "arginine": 0,
            "asparagine": 0,
            "aspartic_acid": 0,
            "cysteine": 0,
            "glutamine": 0,
            "glutamic_acid": 0,
            "isoleucine": 0,
            "leucine": 0,
            "lysine": 0,
            "methionine": 0,
            "phenylalanine": 0,
            "histidine": 0,
        }
    },
    "banana": {
        "calories": 105,
        "protein": 1.3,
        "carbs": 27,
        "fat": 0.4,
        "fiber": 3.1,
        "sugar": 14,
        "sodium": 1,
        "micronutrients": {
            "vitamin_c": 10.3,
            "iron": 0.3,
            "calcium": 6,
            "potassium": 422,
            "vitamin_a": 76,
            "vitamin_e": 0.1,
            "vitamin_k": 0.6,
            "folate": 24,
            "niacin": 0.8,
            "riboflavin": 0.1,
            "thiamin": 0.1,
            "vitamin_b6": 0.4,
            "phosphorus": 26,
            "selenium": 1,
            "copper": 0.1,
            "manganese": 0.3,
            "chromium": 0,
            "molybdenum": 0,
            "iodine": 0,
            "chloride": 0,
            "biotin": 0,
            "pantothenic_acid": 0.4,
            "choline": 12,
            "betaine": 0,
            "taurine": 0,
            "creatine": 0,
            "carnitine": 0,
            "inositol": 0,
            "paba": 0,
            "lipoic_acid": 0,
            "coq10": 0,
            "glutathione": 0,
            "melatonin": 0,
            "serotonin": 0,
            "dopamine": 0,
            "norepinephrine": 0,
            "epinephrine": 0,
            "histamine": 0,
            "gaba": 0,
            "glycine": 0,
            "proline": 0,
            "serine": 0,
            "threonine": 0,
            "tryptophan": 0,
            "tyrosine": 0,
            "valine": 0,
            "alanine": 0,
            "arginine": 0,
            "asparagine": 0,
            "aspartic_acid": 0,
            "cysteine": 0,
            "glutamine": 0,
            "glutamic_acid": 0,
            "isoleucine": 0,
            "leucine": 0,
            "lysine": 0,
            "methionine": 0,
            "phenylalanine": 0,
            "histidine": 0,
        }
    },
    "chicken_breast": {
        "calories": 165,
        "protein": 31,
        "carbs": 0,
        "fat": 3.6,
        "fiber": 0,
        "sugar": 0,
        "sodium": 74,
        "micronutrients": {
            "vitamin_c": 0,
            "iron": 1.0,
            "calcium": 15,
            "potassium": 256,
            "vitamin_a": 6,
            "vitamin_e": 0.2,
            "vitamin_k": 0,
            "folate": 4,
            "niacin": 13.7,
            "riboflavin": 0.1,
            "thiamin": 0.1,
            "vitamin_b6": 0.6,
            "phosphorus": 228,
            "selenium": 22,
            "copper": 0.1,
            "manganese": 0,
            "chromium": 0,
            "molybdenum": 0,
            "iodine": 7,
            "chloride": 0,
            "biotin": 0,
            "pantothenic_acid": 0.9,
            "choline": 73,
            "betaine": 0,
            "taurine": 0,
            "creatine": 0,
            "carnitine": 0,
            "inositol": 0,
            "paba": 0,
            "lipoic_acid": 0,
            "coq10": 0,
            "glutathione": 0,
            "melatonin": 0,
            "serotonin": 0,
            "dopamine": 0,
            "norepinephrine": 0,
            "epinephrine": 0,
            "histamine": 0,
            "gaba": 0,
            "glycine": 0,
            "proline": 0,
            "serine": 0,
            "threonine": 0,
            "tryptophan": 0,
            "tyrosine": 0,
            "valine": 0,
            "alanine": 0,
            "arginine": 0,
            "asparagine": 0,
            "aspartic_acid": 0,
            "cysteine": 0,
            "glutamine": 0,
            "glutamic_acid": 0,
            "isoleucine": 0,
            "leucine": 0,
            "lysine": 0,
            "methionine": 0,
            "phenylalanine": 0,
            "histidine": 0,
        }
    },
    "rice": {
        "calories": 130,
        "protein": 2.7,
        "carbs": 28,
        "fat": 0.3,
        "fiber": 0.4,
        "sugar": 0.1,
        "sodium": 0,
        "micronutrients": {
            "vitamin_c": 0,
            "iron": 0.2,
            "calcium": 10,
            "potassium": 35,
            "vitamin_a": 0,
            "vitamin_e": 0.1,
            "vitamin_k": 0,
            "folate": 8,
            "niacin": 1.6,
            "riboflavin": 0.1,
            "thiamin": 0.1,
            "vitamin_b6": 0.1,
            "phosphorus": 43,
            "selenium": 15,
            "copper": 0.1,
            "manganese": 0.5,
            "chromium": 0,
            "molybdenum": 0,
            "iodine": 0,
            "chloride": 0,
            "biotin": 0,
            "pantothenic_acid": 0.4,
            "choline": 9,
            "betaine": 0,
            "taurine": 0,
            "creatine": 0,
            "carnitine": 0,
            "inositol": 0,
            "paba": 0,
            "lipoic_acid": 0,
            "coq10": 0,
            "glutathione": 0,
            "melatonin": 0,
            "serotonin": 0,
            "dopamine": 0,
            "norepinephrine": 0,
            "epinephrine": 0,
            "histamine": 0,
            "gaba": 0,
            "glycine": 0,
            "proline": 0,
            "serine": 0,
            "threonine": 0,
            "tryptophan": 0,
            "tyrosine": 0,
            "valine": 0,
            "alanine": 0,
            "arginine": 0,
            "asparagine": 0,
            "aspartic_acid": 0,
            "cysteine": 0,
            "glutamine": 0,
            "glutamic_acid": 0,
            "isoleucine": 0,
            "leucine": 0,
            "lysine": 0,
            "methionine": 0,
            "phenylalanine": 0,
            "histidine": 0,
        }
    },
    "broccoli": {
        "calories": 55,
        "protein": 3.7,
        "carbs": 11,
        "fat": 0.6,
        "fiber": 5.2,
        "sugar": 1.5,
        "sodium": 33,
        "micronutrients": {
            "vitamin_c": 89.2,
            "iron": 0.7,
            "calcium": 47,
            "potassium": 316,
            "vitamin_a": 623,
            "vitamin_e": 0.8,
            "vitamin_k": 101.6,
            "folate": 63,
            "niacin": 0.6,
            "riboflavin": 0.1,
            "thiamin": 0.1,
            "vitamin_b6": 0.2,
            "phosphorus": 66,
            "selenium": 2.5,
            "copper": 0.1,
            "manganese": 0.2,
            "chromium": 0,
            "molybdenum": 0,
            "iodine": 0,
            "chloride": 0,
            "biotin": 0,
            "pantothenic_acid": 0.6,
            "choline": 18,
            "betaine": 0,
            "taurine": 0,
            "creatine": 0,
            "carnitine": 0,
            "inositol": 0,
            "paba": 0,
            "lipoic_acid": 0,
            "coq10": 0,
            "glutathione": 0,
            "melatonin": 0,
            "serotonin": 0,
            "dopamine": 0,
            "norepinephrine": 0,
            "epinephrine": 0,
            "histamine": 0,
            "gaba": 0,
            "glycine": 0,
            "proline": 0,
            "serine": 0,
            "threonine": 0,
            "tryptophan": 0,
            "tyrosine": 0,
            "valine": 0,
            "alanine": 0,
            "arginine": 0,
            "asparagine": 0,
            "aspartic_acid": 0,
            "cysteine": 0,
            "glutamine": 0,
            "glutamic_acid": 0,
            "isoleucine": 0,
            "leucine": 0,
            "lysine": 0,
            "methionine": 0,
            "phenylalanine": 0,
            "histidine": 0,
        }
    }
}

def simple_food_recognition(image_data):
    """
    Simple food recognition based on image characteristics
    In a real implementation, you'd use a trained ML model
    """
    # Convert base64 to image
    image_bytes = base64.b64decode(image_data)
    image = Image.open(io.BytesIO(image_bytes))
    
    # Convert to numpy array for analysis
    img_array = np.array(image)
    
    # Simple color-based recognition (very basic)
    # In reality, you'd use a proper ML model
    avg_color = np.mean(img_array, axis=(0, 1))
    
    # Simple heuristics based on color
    if avg_color[0] > 150 and avg_color[1] > 150:  # High red/green
        if avg_color[1] > avg_color[0]:  # More green
            return ["broccoli"]
        else:  # More red
            return ["apple"]
    elif avg_color[0] > 200 and avg_color[1] > 200:  # Very bright
        return ["rice"]
    elif avg_color[0] < 100 and avg_color[1] < 100:  # Dark
        return ["chicken_breast"]
    else:
        return ["banana"]  # Default

@app.route('/analyze_food', methods=['POST'])
def analyze_food():
    try:
        data = request.get_json()
        image_data = data.get('image')
        
        if not image_data:
            return jsonify({'error': 'No image data provided'}), 400
        
        # Recognize foods in the image
        detected_foods = simple_food_recognition(image_data)
        
        # Calculate nutrition totals
        total_nutrition = {
            "calories": 0,
            "protein": 0,
            "carbs": 0,
            "fat": 0,
            "fiber": 0,
            "sugar": 0,
            "sodium": 0,
            "micronutrients": {
                "vitamin_c": 0,
                "iron": 0,
                "calcium": 0,
                "potassium": 0,
                "vitamin_a": 0,
                "vitamin_e": 0,
                "vitamin_k": 0,
                "folate": 0,
                "niacin": 0,
                "riboflavin": 0,
                "thiamin": 0,
                "vitamin_b6": 0,
                "phosphorus": 0,
                "selenium": 0,
                "copper": 0,
                "manganese": 0,
                "chromium": 0,
                "molybdenum": 0,
                "iodine": 0,
                "chloride": 0,
                "biotin": 0,
                "pantothenic_acid": 0,
                "choline": 0,
                "betaine": 0,
                "taurine": 0,
                "creatine": 0,
                "carnitine": 0,
                "inositol": 0,
                "paba": 0,
                "lipoic_acid": 0,
                "coq10": 0,
                "glutathione": 0,
                "melatonin": 0,
                "serotonin": 0,
                "dopamine": 0,
                "norepinephrine": 0,
                "epinephrine": 0,
                "histamine": 0,
                "gaba": 0,
                "glycine": 0,
                "proline": 0,
                "serine": 0,
                "threonine": 0,
                "tryptophan": 0,
                "tyrosine": 0,
                "valine": 0,
                "alanine": 0,
                "arginine": 0,
                "asparagine": 0,
                "aspartic_acid": 0,
                "cysteine": 0,
                "glutamine": 0,
                "glutamic_acid": 0,
                "isoleucine": 0,
                "leucine": 0,
                "lysine": 0,
                "methionine": 0,
                "phenylalanine": 0,
                "histidine": 0,
            }
        }
        
        # Sum nutrition from all detected foods
        for food in detected_foods:
            if food in FOOD_DATABASE:
                food_data = FOOD_DATABASE[food]
                total_nutrition["calories"] += food_data["calories"]
                total_nutrition["protein"] += food_data["protein"]
                total_nutrition["carbs"] += food_data["carbs"]
                total_nutrition["fat"] += food_data["fat"]
                total_nutrition["fiber"] += food_data["fiber"]
                total_nutrition["sugar"] += food_data["sugar"]
                total_nutrition["sodium"] += food_data["sodium"]
                
                # Add micronutrients
                for nutrient, value in food_data["micronutrients"].items():
                    total_nutrition["micronutrients"][nutrient] += value
        
        return jsonify({
            "success": True,
            "detected_foods": detected_foods,
            "nutrition": total_nutrition
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/health', methods=['GET'])
def health_check():
    return jsonify({'status': 'healthy', 'message': 'All Ten Nutrition API is running!'})

@app.route('/', methods=['GET'])
def root():
    return jsonify({
        'message': 'All Ten Nutrition API',
        'endpoints': {
            'health': '/health',
            'analyze_food': '/analyze_food'
        }
    })

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(debug=False, host='0.0.0.0', port=port) 