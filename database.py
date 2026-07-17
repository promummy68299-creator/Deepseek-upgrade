import sqlite3
import json
import logging
from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

class Database:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self.init_tables()
    
    def get_connection(self):
        return sqlite3.connect(self.db_path, check_same_thread=False)
    
    def init_tables(self):
        conn = self.get_connection()
        cursor = conn.cursor()
        
        # Users table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                last_name TEXT,
                subscription_plan_id INTEGER,
                subscription_expiry TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                is_admin INTEGER DEFAULT 0,
                approved_payments INTEGER DEFAULT 0
            )
        ''')
        
        # Plans table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS plans (
                plan_id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                description TEXT,
                price REAL NOT NULL,
                validity_days INTEGER NOT NULL,
                media_json TEXT DEFAULT '[]',
                is_active INTEGER DEFAULT 1,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Payments table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS payments (
                payment_id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                plan_id INTEGER NOT NULL,
                amount REAL NOT NULL,
                screenshot_file_id TEXT,
                status TEXT DEFAULT 'pending',
                admin_comment TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(user_id),
                FOREIGN KEY (plan_id) REFERENCES plans(plan_id)
            )
        ''')
        
        # Settings table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS settings (
                setting_key TEXT PRIMARY KEY,
                setting_value TEXT,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Insert default settings if not exists
        default_settings = [
            ('welcome_text', 'Welcome to Premium Subscription Bot! 🎉\n\nGet access to premium content with our subscription plans.\n\nChoose a plan below to get started!'),
            ('welcome_image', ''),
            ('qr_code', ''),
            ('upi_id', ''),
            ('delivery_link', '')
        ]
        
        for key, value in default_settings:
            cursor.execute('''
                INSERT OR IGNORE INTO settings (setting_key, setting_value)
                VALUES (?, ?)
            ''', (key, value))
        
        conn.commit()
        conn.close()
        logger.info("Database tables initialized successfully")
    
    # User methods
    def add_user(self, user_id: int, username: str = '', first_name: str = '', last_name: str = ''):
        conn = self.get_connection()
        cursor = conn.cursor()
        
        cursor.execute('''
            INSERT OR IGNORE INTO users (user_id, username, first_name, last_name)
            VALUES (?, ?, ?, ?)
        ''', (user_id, username, first_name, last_name))
        
        conn.commit()
        conn.close()
    
    def get_user(self, user_id: int) -> Optional[Dict[str, Any]]:
        conn = self.get_connection()
        cursor = conn.cursor()
        
        cursor.execute('SELECT * FROM users WHERE user_id = ?', (user_id,))
        row = cursor.fetchone()
        conn.close()
        
        if row:
            columns = [description[0] for description in cursor.description]
            return dict(zip(columns, row))
        return None
    
    def get_all_users(self) -> List[Dict[str, Any]]:
        conn = self.get_connection()
        cursor = conn.cursor()
        
        cursor.execute('SELECT * FROM users ORDER BY created_at DESC')
        rows = cursor.fetchall()
        conn.close()
        
        columns = [description[0] for description in cursor.description]
        return [dict(zip(columns, row)) for row in rows]
    
    def get_active_users(self) -> List[Dict[str, Any]]:
        conn = self.get_connection()
        cursor = conn.cursor()
        
        now = datetime.now().isoformat()
        cursor.execute('''
            SELECT * FROM users 
            WHERE subscription_expiry > ? AND subscription_plan_id IS NOT NULL
            ORDER BY created_at DESC
        ''', (now,))
        rows = cursor.fetchall()
        conn.close()
        
        columns = [description[0] for description in cursor.description]
        return [dict(zip(columns, row)) for row in rows]
    
    def update_user_subscription(self, user_id: int, plan_id: int, days: int):
        conn = self.get_connection()
        cursor = conn.cursor()
        
        expiry = (datetime.now() + timedelta(days=days)).isoformat()
        cursor.execute('''
            UPDATE users 
            SET subscription_plan_id = ?, subscription_expiry = ?
            WHERE user_id = ?
        ''', (plan_id, expiry, user_id))
        
        conn.commit()
        conn.close()
    
    # Plan methods
    def add_plan(self, name: str, description: str, price: float, validity_days: int) -> int:
        conn = self.get_connection()
        cursor = conn.cursor()
        
        cursor.execute('''
            INSERT INTO plans (name, description, price, validity_days)
            VALUES (?, ?, ?, ?)
        ''', (name, description, price, validity_days))
        
        plan_id = cursor.lastrowid
        conn.commit()
        conn.close()
        return plan_id
    
    def get_plan(self, plan_id: int) -> Optional[Dict[str, Any]]:
        conn = self.get_connection()
        cursor = conn.cursor()
        
        cursor.execute('SELECT * FROM plans WHERE plan_id = ? AND is_active = 1', (plan_id,))
        row = cursor.fetchone()
        conn.close()
        
        if row:
            columns = [description[0] for description in cursor.description]
            return dict(zip(columns, row))
        return None
    
    def get_all_plans(self) -> List[Dict[str, Any]]:
        conn = self.get_connection()
        cursor = conn.cursor()
        
        cursor.execute('SELECT * FROM plans WHERE is_active = 1 ORDER BY price ASC')
        rows = cursor.fetchall()
        conn.close()
        
        columns = [description[0] for description in cursor.description]
        return [dict(zip(columns, row)) for row in rows]
    
    def update_plan(self, plan_id: int, **kwargs):
        conn = self.get_connection()
        cursor = conn.cursor()
        
        allowed_fields = ['name', 'description', 'price', 'validity_days', 'media_json', 'is_active']
        updates = []
        values = []
        
        for key, value in kwargs.items():
            if key in allowed_fields:
                updates.append(f"{key} = ?")
                values.append(value)
        
        if updates:
            values.append(plan_id)
            query = f"UPDATE plans SET {', '.join(updates)}, updated_at = CURRENT_TIMESTAMP WHERE plan_id = ?"
            cursor.execute(query, values)
            conn.commit()
        
        conn.close()
    
    def delete_plan(self, plan_id: int):
        conn = self.get_connection()
        cursor = conn.cursor()
        
        cursor.execute('UPDATE plans SET is_active = 0 WHERE plan_id = ?', (plan_id,))
        conn.commit()
        conn.close()
    
    def add_media_to_plan(self, plan_id: int, media_type: str, file_id: str):
        conn = self.get_connection()
        cursor = conn.cursor()
        
        cursor.execute('SELECT media_json FROM plans WHERE plan_id = ?', (plan_id,))
        row = cursor.fetchone()
        
        if row:
            media_list = json.loads(row[0]) if row[0] else []
            media_list.append({
                'type': media_type,
                'file_id': file_id,
                'added_at': datetime.now().isoformat()
            })
            cursor.execute('UPDATE plans SET media_json = ?, updated_at = CURRENT_TIMESTAMP WHERE plan_id = ?', 
                         (json.dumps(media_list), plan_id))
            conn.commit()
        
        conn.close()
    
    def delete_media_from_plan(self, plan_id: int, media_index: int):
        conn = self.get_connection()
        cursor = conn.cursor()
        
        cursor.execute('SELECT media_json FROM plans WHERE plan_id = ?', (plan_id,))
        row = cursor.fetchone()
        
        if row:
            media_list = json.loads(row[0]) if row[0] else []
            if 0 <= media_index < len(media_list):
                del media_list[media_index]
                cursor.execute('UPDATE plans SET media_json = ?, updated_at = CURRENT_TIMESTAMP WHERE plan_id = ?', 
                             (json.dumps(media_list), plan_id))
                conn.commit()
        
        conn.close()
    
    # Payment methods
    def add_payment(self, user_id: int, plan_id: int, amount: float, screenshot_file_id: str) -> int:
        conn = self.get_connection()
        cursor = conn.cursor()
        
        cursor.execute('''
            INSERT INTO payments (user_id, plan_id, amount, screenshot_file_id, status)
            VALUES (?, ?, ?, ?, 'pending')
        ''', (user_id, plan_id, amount, screenshot_file_id))
        
        payment_id = cursor.lastrowid
        conn.commit()
        conn.close()
        return payment_id
    
    def get_payment(self, payment_id: int) -> Optional[Dict[str, Any]]:
        conn = self.get_connection()
        cursor = conn.cursor()
        
        cursor.execute('SELECT * FROM payments WHERE payment_id = ?', (payment_id,))
        row = cursor.fetchone()
        conn.close()
        
        if row:
            columns = [description[0] for description in cursor.description]
            return dict(zip(columns, row))
        return None
    
    def get_pending_payments(self) -> List[Dict[str, Any]]:
        conn = self.get_connection()
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT p.*, u.username, u.first_name, u.last_name, pl.name as plan_name
            FROM payments p
            JOIN users u ON p.user_id = u.user_id
            JOIN plans pl ON p.plan_id = pl.plan_id
            WHERE p.status = 'pending'
            ORDER BY p.created_at ASC
        ''')
        rows = cursor.fetchall()
        conn.close()
        
        columns = [description[0] for description in cursor.description]
        return [dict(zip(columns, row)) for row in rows]
    
    def approve_payment(self, payment_id: int, admin_comment: str = ''):
        conn = self.get_connection()
        cursor = conn.cursor()
        
        cursor.execute('''
            UPDATE payments 
            SET status = 'approved', admin_comment = ?, updated_at = CURRENT_TIMESTAMP
            WHERE payment_id = ?
        ''', (admin_comment, payment_id))
        
        # Update user's approved payments count
        cursor.execute('SELECT user_id, plan_id FROM payments WHERE payment_id = ?', (payment_id,))
        payment = cursor.fetchone()
        
        if payment:
            user_id, plan_id = payment
            cursor.execute('UPDATE users SET approved_payments = approved_payments + 1 WHERE user_id = ?', (user_id,))
            
            # Update subscription
            plan = self.get_plan(plan_id)
            if plan:
                self.update_user_subscription(user_id, plan_id, plan['validity_days'])
        
        conn.commit()
        conn.close()
    
    def reject_payment(self, payment_id: int, admin_comment: str = ''):
        conn = self.get_connection()
        cursor = conn.cursor()
        
        cursor.execute('''
            UPDATE payments 
            SET status = 'rejected', admin_comment = ?, updated_at = CURRENT_TIMESTAMP
            WHERE payment_id = ?
        ''', (admin_comment, payment_id))
        
        conn.commit()
        conn.close()
    
    def get_user_payments(self, user_id: int) -> List[Dict[str, Any]]:
        conn = self.get_connection()
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT p.*, pl.name as plan_name
            FROM payments p
            JOIN plans pl ON p.plan_id = pl.plan_id
            WHERE p.user_id = ?
            ORDER BY p.created_at DESC
        ''', (user_id,))
        rows = cursor.fetchall()
        conn.close()
        
        columns = [description[0] for description in cursor.description]
        return [dict(zip(columns, row)) for row in rows]
    
    # Settings methods
    def get_setting(self, key: str) -> Optional[str]:
        conn = self.get_connection()
        cursor = conn.cursor()
        
        cursor.execute('SELECT setting_value FROM settings WHERE setting_key = ?', (key,))
        row = cursor.fetchone()
        conn.close()
        
        return row[0] if row else None
    
    def set_setting(self, key: str, value: str):
        conn = self.get_connection()
        cursor = conn.cursor()
        
        cursor.execute('''
            INSERT OR REPLACE INTO settings (setting_key, setting_value, updated_at)
            VALUES (?, ?, CURRENT_TIMESTAMP)
        ''', (key, value))
        
        conn.commit()
        conn.close()
    
    # Admin methods
    def is_admin(self, user_id: int) -> bool:
        conn = self.get_connection()
        cursor = conn.cursor()
        
        cursor.execute('SELECT is_admin FROM users WHERE user_id = ?', (user_id,))
        row = cursor.fetchone()
        conn.close()
        
        return bool(row and row[0])
    
    def set_admin(self, user_id: int, is_admin: bool = True):
        conn = self.get_connection()
        cursor = conn.cursor()
        
        cursor.execute('UPDATE users SET is_admin = ? WHERE user_id = ?', (1 if is_admin else 0, user_id))
        conn.commit()
        conn.close()
    
    # Stats methods
    def get_stats(self) -> Dict[str, int]:
        conn = self.get_connection()
        cursor = conn.cursor()
        
        stats = {}
        
        cursor.execute('SELECT COUNT(*) FROM users')
        stats['total_users'] = cursor.fetchone()[0]
        
        cursor.execute('SELECT COUNT(*) FROM plans WHERE is_active = 1')
        stats['total_plans'] = cursor.fetchone()[0]
        
        cursor.execute('SELECT COUNT(*) FROM payments WHERE status = "pending"')
        stats['pending_payments'] = cursor.fetchone()[0]
        
        cursor.execute('SELECT COUNT(*) FROM payments WHERE status = "approved"')
        stats['approved_payments'] = cursor.fetchone()[0]
        
        cursor.execute('SELECT COUNT(*) FROM payments WHERE status = "rejected"')
        stats['rejected_payments'] = cursor.fetchone()[0]
        
        cursor.execute('SELECT COUNT(*) FROM users WHERE subscription_expiry > CURRENT_TIMESTAMP')
        stats['active_subscriptions'] = cursor.fetchone()[0]
        
        conn.close()
        return stats
    
    # Database backup/restore
    def backup_database(self) -> bytes:
        conn = self.get_connection()
        with open('backup.db', 'w') as f:
            for line in conn.iterdump():
                f.write(f'{line}\n')
        conn.close()
        
        with open('backup.db', 'rb') as f:
            data = f.read()
        
        import os
        os.remove('backup.db')
        return data
    
    def restore_database(self, data: bytes):
        conn = self.get_connection()
        cursor = conn.cursor()
        
        # Drop all tables
        cursor.execute('SELECT name FROM sqlite_master WHERE type="table"')
        tables = cursor.fetchall()
        for table in tables:
            cursor.execute(f'DROP TABLE IF EXISTS {table[0]}')
        
        conn.commit()
        conn.close()
        
        # Restore from backup
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.executescript(data.decode('utf-8'))
        conn.commit()
        conn.close()