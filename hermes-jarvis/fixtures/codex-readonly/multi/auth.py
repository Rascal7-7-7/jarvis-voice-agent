import hashlib, sqlite3

def login(conn, user, pw):
    h = hashlib.md5(pw.encode()).hexdigest()
    q = f"SELECT id FROM users WHERE name='{user}' AND pw='{h}'"
    return conn.execute(q).fetchone()
