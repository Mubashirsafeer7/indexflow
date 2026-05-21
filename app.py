from flask import Flask, request, jsonify
from flask_cors import CORS
from google.oauth2 import service_account
from googleapiclient.discovery import build
import sqlite3, time, os
from datetime import datetime

app = Flask(__name__)
CORS(app)

SCOPES = ['https://www.googleapis.com/auth/indexing']

def get_service():
    creds = service_account.Credentials.from_service_account_file(
        'credentials.json', scopes=SCOPES)
    return build('indexing', 'v3', credentials=creds)

def init_db():
    conn = sqlite3.connect('indexing.db')
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS tasks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT, created_at TEXT,
        status TEXT DEFAULT 'pending',
        total_urls INTEGER DEFAULT 0,
        indexed INTEGER DEFAULT 0,
        failed INTEGER DEFAULT 0)''')
    c.execute('''CREATE TABLE IF NOT EXISTS urls (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        task_id INTEGER, url TEXT,
        status TEXT DEFAULT 'pending',
        submitted_at TEXT, result TEXT)''')
    conn.commit()
    conn.close()

init_db()

@app.route('/api/stats')
def stats():
    conn = sqlite3.connect('indexing.db')
    c = conn.cursor()
    c.execute('SELECT COUNT(*) FROM tasks')
    total_tasks = c.fetchone()[0]
    c.execute('SELECT SUM(total_urls),SUM(indexed),SUM(failed) FROM tasks')
    r = c.fetchone()
    conn.close()
    return jsonify({'total_tasks':total_tasks,'total_urls':r[0] or 0,'total_indexed':r[1] or 0,'total_failed':r[2] or 0})

@app.route('/api/tasks', methods=['GET'])
def get_tasks():
    conn = sqlite3.connect('indexing.db')
    c = conn.cursor()
    c.execute('SELECT * FROM tasks ORDER BY created_at DESC')
    tasks = [{'id':r[0],'name':r[1],'created_at':r[2],'status':r[3],'total_urls':r[4],'indexed':r[5],'failed':r[6]} for r in c.fetchall()]
    conn.close()
    return jsonify(tasks)

@app.route('/api/tasks', methods=['POST'])
def create_task():
    data = request.json
    name = data.get('name', f'Task {datetime.now().strftime("%Y-%m-%d %H:%M")}')
    urls = [u.strip() for u in data.get('urls', []) if u.strip()]
    if not urls:
        return jsonify({'error': 'No URLs'}), 400
    conn = sqlite3.connect('indexing.db')
    c = conn.cursor()
    c.execute('INSERT INTO tasks (name,created_at,status,total_urls) VALUES (?,?,?,?)',
              (name, datetime.now().isoformat(), 'processing', len(urls)))
    task_id = c.lastrowid
    for url in urls:
        c.execute('INSERT INTO urls (task_id,url) VALUES (?,?)', (task_id, url))
    conn.commit()
    try:
        service = get_service()
        indexed = 0
        failed = 0
        for url in urls:
            try:
                service.urlNotifications().publish(body={'url':url,'type':'URL_UPDATED'}).execute()
                c.execute('UPDATE urls SET status=?,submitted_at=? WHERE task_id=? AND url=?',
                         ('submitted', datetime.now().isoformat(), task_id, url))
                indexed += 1
                time.sleep(0.3)
            except Exception as e:
                c.execute('UPDATE urls SET status=?,result=? WHERE task_id=? AND url=?',
                         ('failed', str(e), task_id, url))
                failed += 1
        status = 'completed' if failed==0 else ('failed' if indexed==0 else 'partial')
        c.execute('UPDATE tasks SET status=?,indexed=?,failed=? WHERE id=?',
                 (status, indexed, failed, task_id))
        conn.commit()
        conn.close()
        return jsonify({'task_id':task_id,'indexed':indexed,'failed':failed,'status':status})
    except Exception as e:
        c.execute('UPDATE tasks SET status=? WHERE id=?', ('error', task_id))
        conn.commit()
        conn.close()
        return jsonify({'error': str(e)}), 500

@app.route('/api/tasks/<int:task_id>/urls')
def get_urls(task_id):
    conn = sqlite3.connect('indexing.db')
    c = conn.cursor()
    c.execute('SELECT * FROM urls WHERE task_id=?', (task_id,))
    urls = [{'id':r[0],'url':r[2],'status':r[3],'submitted_at':r[4]} for r in c.fetchall()]
    conn.close()
    return jsonify(urls)

@app.route('/api/tasks/<int:task_id>', methods=['DELETE'])
def delete_task(task_id):
    conn = sqlite3.connect('indexing.db')
    c = conn.cursor()
    c.execute('DELETE FROM urls WHERE task_id=?', (task_id,))
    c.execute('DELETE FROM tasks WHERE id=?', (task_id,))
    conn.commit()
    conn.close()
    return jsonify({'success': True})

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
