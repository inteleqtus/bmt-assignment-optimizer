from flask import Flask, jsonify
import os

# Test OR-Tools import
try:
    from ortools.linear_solver import pywraplp
    ORTOOLS_AVAILABLE = True
    ortools_status = "loaded successfully"
except ImportError as e:
    ORTOOLS_AVAILABLE = False
    ortools_status = f"import failed: {str(e)}"

app = Flask(__name__)

@app.route('/')
def health():
    return jsonify({
        "status": "healthy", 
        "message": "BMT API running",
        "ortools_available": ORTOOLS_AVAILABLE,
        "ortools_status": ortools_status
    })

@app.route('/test')
def test():
    if not ORTOOLS_AVAILABLE:
        return jsonify({
            "error": "OR-Tools not available",
            "status": ortools_status
        })
    
    return jsonify({
        "message": "OR-Tools loaded, optimization ready",
        "ortools_version": "9.6.2499"
    })

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
