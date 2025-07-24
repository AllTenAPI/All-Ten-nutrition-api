from flask import Flask, request, jsonify
from flask_cors import CORS
import base64
import json
import os

app = Flask(__name__)
CORS(app)

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

@app.route('/analyze_food', methods=['POST'])
def analyze_food():
    try:
        data = request.get_json()
        if not data or 'image' not in data:
            return jsonify({'error': 'No image data provided'}), 400
        
        # For now, return simulated nutrition data
        # In a real implementation, you would process the image here
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
            'detected_foods': ['Sample Food Item']
        }
        
        return jsonify(nutrition_data)
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(debug=False, host='0.0.0.0', port=port) 