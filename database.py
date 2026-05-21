import sqlite3
from datetime import datetime

def get_db_connection():
    return sqlite3.connect('tickets.db', timeout=10, check_same_thread=False)

def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS tickets (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ticket_id TEXT UNIQUE,
        user_id INTEGER,
        username TEXT,
        text TEXT,
        status TEXT DEFAULT 'open',
        assigned_to INTEGER DEFAULT NULL,
        created_at TEXT,
        closed_at TEXT DEFAULT NULL
    )
    ''')
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ticket_id TEXT,
        from_user TEXT,
        message TEXT,
        file_id TEXT,
        file_type TEXT,
        created_at TEXT
    )
    ''')
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS assigned_tickets (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ticket_id TEXT UNIQUE,
        admin_id INTEGER,
        assigned_at TEXT
    )
    ''')
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS roles (
        user_id INTEGER PRIMARY KEY,
        role TEXT NOT NULL
    )
    ''')
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS notes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ticket_id TEXT,
        admin_id INTEGER,
        text TEXT,
        created_at TEXT
    )
    ''')
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS blacklist (
        user_id INTEGER PRIMARY KEY,
        reason TEXT,
        created_at TEXT
    )
    ''')
    conn.commit()
    conn.close()

def create_ticket(ticket_id, user_id, username, text):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
    INSERT INTO tickets (ticket_id, user_id, username, text, status, created_at)
    VALUES (?, ?, ?, ?, ?, ?)
    ''', (ticket_id, user_id, username, text, 'open', datetime.now().isoformat()))
    conn.commit()
    conn.close()
    return ticket_id

def update_ticket_status(ticket_id, status):
    conn = get_db_connection()
    cursor = conn.cursor()
    closed_at = datetime.now().isoformat() if status == 'closed' else None
    cursor.execute('''
    UPDATE tickets SET status = ?, closed_at = ? WHERE ticket_id = ?
    ''', (status, closed_at, ticket_id))
    conn.commit()
    conn.close()

def assign_ticket(ticket_id, admin_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
    INSERT OR REPLACE INTO assigned_tickets (ticket_id, admin_id, assigned_at)
    VALUES (?, ?, ?)
    ''', (ticket_id, admin_id, datetime.now().isoformat()))
    cursor.execute('''
    UPDATE tickets SET assigned_to = ? WHERE ticket_id = ?
    ''', (admin_id, ticket_id))
    conn.commit()
    conn.close()

def get_assigned_admin(ticket_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT admin_id FROM assigned_tickets WHERE ticket_id = ?', (ticket_id,))
    row = cursor.fetchone()
    conn.close()
    return row[0] if row else None

def unassign_ticket(ticket_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('DELETE FROM assigned_tickets WHERE ticket_id = ?', (ticket_id,))
    cursor.execute('UPDATE tickets SET assigned_to = NULL WHERE ticket_id = ?', (ticket_id,))
    conn.commit()
    conn.close()

def get_ticket(ticket_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM tickets WHERE ticket_id = ?', (ticket_id,))
    row = cursor.fetchone()
    conn.close()
    return row

def get_user_tickets(user_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
    SELECT ticket_id, text, status, created_at 
    FROM tickets 
    WHERE user_id = ? 
    ORDER BY created_at DESC
    ''', (user_id,))
    rows = cursor.fetchall()
    conn.close()
    return rows

def get_all_tickets():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
    SELECT id, ticket_id, user_id, username, text, status, created_at, closed_at, assigned_to
    FROM tickets 
    ORDER BY created_at DESC
    ''')
    rows = cursor.fetchall()
    conn.close()
    return rows

def get_open_tickets():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
    SELECT * FROM tickets WHERE status = 'open' ORDER BY created_at
    ''')
    rows = cursor.fetchall()
    conn.close()
    return rows

def save_message(ticket_id, from_user, message, file_id=None, file_type=None):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
    INSERT INTO messages (ticket_id, from_user, message, file_id, file_type, created_at)
    VALUES (?, ?, ?, ?, ?, ?)
    ''', (ticket_id, from_user, message, file_id, file_type, datetime.now().isoformat()))
    conn.commit()
    conn.close()

def get_messages(ticket_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
    SELECT from_user, message, file_id, file_type, created_at 
    FROM messages 
    WHERE ticket_id = ? 
    ORDER BY created_at
    ''', (ticket_id,))
    rows = cursor.fetchall()
    conn.close()
    return rows

def set_role(user_id: int, role: str):
    conn = get_db_connection()
    conn.execute("INSERT OR REPLACE INTO roles (user_id, role) VALUES (?, ?)", (user_id, role))
    conn.commit()
    conn.close()

def get_role(user_id: int) -> str:
    conn = get_db_connection()
    row = conn.execute("SELECT role FROM roles WHERE user_id = ?", (user_id,)).fetchone()
    conn.close()
    return row[0] if row else "user"

def get_all_roles():
    conn = get_db_connection()
    rows = conn.execute("SELECT user_id, role FROM roles").fetchall()
    conn.close()
    return dict(rows)

def add_note(ticket_id: str, admin_id: int, text: str):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
    INSERT INTO notes (ticket_id, admin_id, text, created_at)
    VALUES (?, ?, ?, ?)
    ''', (ticket_id, admin_id, text, datetime.now().isoformat()))
    conn.commit()
    conn.close()

def get_notes(ticket_id: str):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
    SELECT admin_id, text, created_at FROM notes WHERE ticket_id = ? ORDER BY created_at
    ''', (ticket_id,))
    rows = cursor.fetchall()
    conn.close()
    return rows

def add_to_blacklist(user_id: int, reason: str = ""):
    conn = get_db_connection()
    conn.execute("INSERT OR REPLACE INTO blacklist (user_id, reason, created_at) VALUES (?, ?, ?)",
                 (user_id, reason, datetime.now().isoformat()))
    conn.commit()
    conn.close()

def remove_from_blacklist(user_id: int):
    conn = get_db_connection()
    conn.execute("DELETE FROM blacklist WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()

def get_blacklist():
    conn = get_db_connection()
    rows = conn.execute("SELECT user_id, reason, created_at FROM blacklist ORDER BY created_at").fetchall()
    conn.close()
    return rows

def is_blacklisted(user_id: int) -> bool:
    conn = get_db_connection()
    row = conn.execute("SELECT 1 FROM blacklist WHERE user_id = ?", (user_id,)).fetchone()
    conn.close()
    return row is not None

init_db()
print("✅ База данных инициализирована")