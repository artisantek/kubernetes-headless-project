from flask import Flask, jsonify
import psycopg2
import psycopg2.extras
import os

app = Flask(__name__)

def get_db_connection():
    return psycopg2.connect(
        host=os.environ.get('DB_HOST', 'postgres-read'),
        user=os.environ.get('DB_USER', 'appuser'),
        password=os.environ.get('DB_PASSWORD', 'password123'),
        dbname=os.environ.get('DB_NAME', 'userdb')
    )

@app.route('/')
def index():
    with open('index.html', 'r') as file:
        return file.read()

@app.route('/users')
def get_users():
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        # Get the hostname of the PostgreSQL pod serving this request
        cursor.execute("SELECT inet_server_addr() as server_ip")
        server_info = cursor.fetchone()

        # Get pod hostname via a simple query
        cursor.execute("SHOW server_version")
        version = cursor.fetchone()

        cursor.execute("SELECT * FROM users ORDER BY created_at DESC")
        users = cursor.fetchall()

        # Convert datetime objects to strings for JSON serialization
        for user in users:
            if user.get('created_at'):
                user['created_at'] = user['created_at'].isoformat()

        cursor.close()
        conn.close()

        return jsonify({
            "success": True,
            "users": users,
            "postgres_server": str(server_info['server_ip'])
        })
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
