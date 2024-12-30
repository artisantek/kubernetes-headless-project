from flask import Flask, request, jsonify
import mysql.connector
import os

app = Flask(__name__)

def get_db_connection():
    return mysql.connector.connect(
        host=os.environ.get('DB_HOST', 'mysql-0.mysql'),
        user=os.environ.get('DB_USER', 'appuser'),
        password=os.environ.get('DB_PASSWORD', 'password123'),
        database=os.environ.get('DB_NAME', 'userdb')
    )

@app.route('/')
def index():
    with open('index.html', 'r') as file:
        return file.read()

@app.route('/submit', methods=['POST'])
def submit():
    try:
        data = request.get_json()
        name = data['name']
        email = data['email']

        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO users (name, email) VALUES (%s, %s)",
            (name, email)
        )
        conn.commit()
        cursor.close()
        conn.close()

        return jsonify({"success": True, "message": "User registered successfully!"})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)