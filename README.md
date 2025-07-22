# Custom Nutrition Analysis API

A simple Flask-based API for food recognition and nutrition analysis.

## Features

- ✅ **Food Recognition**: Basic color-based food detection
- ✅ **Nutrition Database**: Comprehensive macro and micronutrient data
- ✅ **RESTful API**: Easy to integrate with Flutter app
- ✅ **CORS Enabled**: Works with web applications
- ✅ **Error Handling**: Graceful failure handling

## Setup

### 1. Install Dependencies

```bash
pip install -r requirements.txt
```

### 2. Run Locally

```bash
python app.py
```

The API will be available at: `http://localhost:5000`

### 3. Test the API

```bash
curl -X GET http://localhost:5000/health
```

## API Endpoints

### POST /analyze_food

Analyzes a food image and returns nutrition data.

**Request:**
```json
{
  "image": "base64_encoded_image_data"
}
```

**Response:**
```json
{
  "success": true,
  "detected_foods": ["apple", "banana"],
  "nutrition": {
    "calories": 200,
    "protein": 1.8,
    "carbs": 52,
    "fat": 0.7,
    "fiber": 7.5,
    "sugar": 33,
    "sodium": 3,
    "micronutrients": {
      "vitamin_c": 18.7,
      "iron": 0.5,
      "calcium": 17,
      "potassium": 617,
      // ... more micronutrients
    }
  }
}
```

### GET /health

Health check endpoint.

**Response:**
```json
{
  "status": "healthy"
}
```

## Deployment Options

### Option 1: Heroku

1. **Create Heroku app**:
   ```bash
   heroku create your-nutrition-api
   ```

2. **Add Python buildpack**:
   ```bash
   heroku buildpacks:set heroku/python
   ```

3. **Deploy**:
   ```bash
   git add .
   git commit -m "Add nutrition API"
   git push heroku main
   ```

### Option 2: Railway

1. **Connect GitHub repository**
2. **Railway will auto-deploy**
3. **Get your API URL**

### Option 3: Render

1. **Create new Web Service**
2. **Connect GitHub repository**
3. **Set build command**: `pip install -r requirements.txt`
4. **Set start command**: `gunicorn app:app`

## Integration with Flutter

Update your Flutter app to use your custom API:

```dart
// In lib/config/api_config.dart
static const String customApiUrl = 'https://your-api-url.com';
static const String customApiKey = 'your_api_key_if_needed';
```

## Future Improvements

1. **ML Model Integration**: Replace simple color detection with TensorFlow model
2. **More Foods**: Expand the food database
3. **Portion Estimation**: Add portion size detection
4. **User Feedback**: Allow users to correct recognition errors
5. **Caching**: Cache results for better performance

## Current Limitations

- Basic color-based recognition (not ML-powered)
- Limited food database (5 foods)
- No portion size estimation
- No user feedback mechanism

## Next Steps

1. **Deploy the API** to a cloud platform
2. **Update Flutter app** to use your custom API
3. **Test with real food photos**
4. **Improve recognition accuracy** with ML models 