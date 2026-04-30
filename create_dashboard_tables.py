"""
Create Dashboard-Specific Database Tables
==========================================
New tables needed by the dashboard UI that don't exist yet:
  - agent_campaigns: Campaign management
  - agent_scripts: AI call scripts
  - agent_contact_lists: Contact list groupings
  - agent_contacts: Individual contacts in lists
  - agent_users: Dashboard team members
  - agent_audit_log: Activity tracking

Run once: python create_dashboard_tables.py
"""

import os
import sys
sys.path.insert(0, os.path.dirname(__file__))

from dotenv import load_dotenv
import pymysql

load_dotenv()


def get_connection():
    return pymysql.connect(
        host=os.getenv("DB_HOST"),
        port=int(os.getenv("DB_PORT")),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
        database=os.getenv("DB_NAME"),
        ssl={"ssl": {}},
        cursorclass=pymysql.cursors.DictCursor,
    )


TABLES = [
    # ── 1. Campaigns ────────────────────────────────────────────
    """
    CREATE TABLE IF NOT EXISTS agent_campaigns (
        id              VARCHAR(36) PRIMARY KEY,
        name            VARCHAR(255) NOT NULL,
        description     TEXT,
        status          VARCHAR(30) DEFAULT 'draft'
                        COMMENT 'draft, scheduled, running, paused, completed',
        contact_list_id VARCHAR(36) NULL COMMENT 'FK to agent_contact_lists',
        script_id       VARCHAR(36) NULL COMMENT 'FK to agent_scripts',
        project_name    VARCHAR(255),

        -- Progress counters
        total_contacts  INT DEFAULT 0,
        calls_made      INT DEFAULT 0,
        calls_connected INT DEFAULT 0,
        calls_converted INT DEFAULT 0,
        connect_rate    DECIMAL(5,2) DEFAULT 0.00,

        -- Scheduling
        scheduled_at    DATETIME NULL,
        started_at      DATETIME NULL,
        completed_at    DATETIME NULL,

        -- Ownership
        created_by      VARCHAR(100) DEFAULT 'admin',

        created_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
        updated_at      DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,

        INDEX idx_status (status),
        INDEX idx_created (created_at)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,

    # ── 2. Scripts ──────────────────────────────────────────────
    """
    CREATE TABLE IF NOT EXISTS agent_scripts (
        id          VARCHAR(36) PRIMARY KEY,
        name        VARCHAR(255) NOT NULL,
        description TEXT,
        content     LONGTEXT COMMENT 'Full script/prompt text',
        voice_id    VARCHAR(100),
        voice_name  VARCHAR(100) DEFAULT 'Riya',
        node_count  INT DEFAULT 0 COMMENT 'Conversation flow complexity',
        is_active   TINYINT(1) DEFAULT 1,
        created_by  VARCHAR(100) DEFAULT 'admin',
        created_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
        updated_at  DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,

        INDEX idx_active (is_active)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,

    # ── 3. Contact Lists ────────────────────────────────────────
    """
    CREATE TABLE IF NOT EXISTS agent_contact_lists (
        id          VARCHAR(36) PRIMARY KEY,
        name        VARCHAR(255) NOT NULL,
        description TEXT,
        total_count INT DEFAULT 0,
        created_by  VARCHAR(100) DEFAULT 'admin',
        created_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
        updated_at  DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,

    # ── 4. Contacts ─────────────────────────────────────────────
    """
    CREATE TABLE IF NOT EXISTS agent_contacts (
        id           VARCHAR(36) PRIMARY KEY,
        list_id      VARCHAR(36) NOT NULL COMMENT 'FK to agent_contact_lists',
        name         VARCHAR(255),
        phone        VARCHAR(20) NOT NULL,
        email        VARCHAR(255),
        company      VARCHAR(255),
        contact_type VARCHAR(20) DEFAULT 'buyer' COMMENT 'buyer or broker',
        status       VARCHAR(50) DEFAULT 'pending' COMMENT 'pending, called, converted, dnc',
        notes        TEXT,
        created_at   DATETIME DEFAULT CURRENT_TIMESTAMP,
        updated_at   DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,

        INDEX idx_list (list_id),
        INDEX idx_phone (phone),
        INDEX idx_status (status)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,

    # ── 5. Users (team members) ─────────────────────────────────
    """
    CREATE TABLE IF NOT EXISTS agent_users (
        id          VARCHAR(36) PRIMARY KEY,
        email       VARCHAR(255) NOT NULL UNIQUE,
        name        VARCHAR(255) NOT NULL,
        password_hash VARCHAR(255) NOT NULL,
        role        VARCHAR(20) DEFAULT 'user' COMMENT 'admin, user, viewer',
        is_active   TINYINT(1) DEFAULT 1,
        last_login  DATETIME NULL,
        created_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
        updated_at  DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,

        INDEX idx_email (email),
        INDEX idx_role (role)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,

    # ── 6. Audit Log ────────────────────────────────────────────
    """
    CREATE TABLE IF NOT EXISTS agent_audit_log (
        id          INT AUTO_INCREMENT PRIMARY KEY,
        user_id     VARCHAR(36),
        user_email  VARCHAR(255),
        action      VARCHAR(100) NOT NULL COMMENT 'login, create_campaign, update_script, etc.',
        entity_type VARCHAR(50) COMMENT 'campaign, script, contact, etc.',
        entity_id   VARCHAR(36),
        details     TEXT COMMENT 'JSON details of the action',
        ip_address  VARCHAR(45),
        created_at  DATETIME DEFAULT CURRENT_TIMESTAMP,

        INDEX idx_user (user_id),
        INDEX idx_action (action),
        INDEX idx_created (created_at)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
]


# Default admin user (password: admin123 — change after first login!)
DEFAULT_ADMIN = """
    INSERT IGNORE INTO agent_users (id, email, name, password_hash, role)
    VALUES (
        'admin-001',
        'admin@hookfish.in',
        'Admin',
        '$2b$12$LJ3m4ks9RqdJYMz4GViGxOhU5v6YxKRqN5V2Q.UXZBz5znSTz/JyG',
        'admin'
    )
"""

# Default script (current Maanikya buyer script)
DEFAULT_SCRIPT = """
    INSERT IGNORE INTO agent_scripts (id, name, description, voice_name, node_count)
    VALUES (
        'script-maanikya-buyer',
        'Maanikya — Buyer Pitch',
        'Buyer outreach script for Maanikya by Viyan Ventures, Mahim West. Two BHK, 50L down, 2.57cr all-inclusive.',
        'Riya',
        8
    )
"""


def create_tables():
    conn = get_connection()
    cur = conn.cursor()

    print("=" * 60)
    print("Creating Dashboard Tables")
    print("=" * 60)

    for i, ddl in enumerate(TABLES):
        table_name = ddl.strip().split("IF NOT EXISTS")[1].split("(")[0].strip()
        try:
            cur.execute(ddl)
            conn.commit()
            print(f"  [OK] [{i+1}/{len(TABLES)}] Created: {table_name}")
        except Exception as e:
            print(f"  [ERROR] [{i+1}/{len(TABLES)}] Error creating {table_name}: {e}")

    # Insert default admin user
    print()
    print("Inserting defaults...")
    try:
        cur.execute(DEFAULT_ADMIN)
        conn.commit()
        print("  [OK] Default admin user created (admin@hookfish.in)")
    except Exception as e:
        print(f"  [INFO] Admin user: {e}")

    try:
        cur.execute(DEFAULT_SCRIPT)
        conn.commit()
        print("  [OK] Default Maanikya script created")
    except Exception as e:
        print(f"  [INFO] Default script: {e}")

    # Verify all tables exist
    print()
    print("Verifying tables...")
    expected = [
        "agent_campaigns", "agent_scripts", "agent_contact_lists",
        "agent_contacts", "agent_users", "agent_audit_log"
    ]
    for table in expected:
        cur.execute(f"SHOW TABLES LIKE '{table}'")
        result = cur.fetchone()
        status = "[OK] EXISTS" if result else "[MISSING]"
        print(f"  {status}: {table}")

    conn.close()
    print()
    print("=" * 60)
    print("Done!")
    print("=" * 60)


if __name__ == "__main__":
    create_tables()
