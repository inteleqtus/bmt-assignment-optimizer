from flask import Flask, jsonify
import os

app = Flask(__name__)

@app.route('/')
def health():
    return jsonify({"status": "healthy", "message": "BMT API running"})

@app.route('/test')
def test():
    return jsonify({"message": "Test working without OR-Tools"})

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
