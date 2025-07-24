# All Ten Nutrition API - Deployment Guide

## Render Deployment Options

### Option 1: Automatic Deployment (Recommended)
1. Connect your GitHub repository to Render
2. Use the `render.yaml` file for automatic configuration
3. Render will use:
   - Python 3.11.7
   - Build Command: `pip install -r requirements-simple.txt`
   - Start Command: `gunicorn app-simple:app`

### Option 2: Manual Configuration
If automatic deployment fails, manually configure in Render:

**Build Command:**
```bash
pip install Flask Flask-CORS gunicorn
```

**Start Command:**
```bash
gunicorn app-simple:app
```

**Environment Variables:**
- `PYTHON_VERSION`: `3.11.7`

### Option 3: Minimal Setup
If all else fails, use the most basic setup:

**Build Command:**
```bash
pip install Flask Flask-CORS gunicorn
```

**Start Command:**
```bash
python app-simple.py
```

## Testing Your API

Once deployed, test with:
```bash
# Health check
curl https://your-app-name.onrender.com/health

# Root endpoint
curl https://your-app-name.onrender.com/

# Test nutrition analysis
curl -X POST https://your-app-name.onrender.com/analyze_food \
  -H "Content-Type: application/json" \
  -d '{"image": "base64_encoded_image_data"}'
```

## API Endpoints

- `GET /health` - Health check
- `GET /` - API information
- `POST /analyze_food` - Analyze food image (returns simulated data)

## Troubleshooting

1. **Python version issues**: Use Python 3.11.7
2. **Build failures**: Use `requirements-simple.txt` or manual pip install
3. **Start failures**: Use `app-simple.py` instead of `app.py` 