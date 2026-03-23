"""
Create Agent-Specific Database Tables
======================================
Creates new tables for the voice agent system:
  - agent_call_logs: Full call records with transcripts and outcomes
  - agent_dnc_list: Do Not Call list
  - agent_managers: Manager pool for round-robin allocation
  - agent_meetings: Meeting/site visit scheduling and tracking
  - agent_call_attempts: Track call attempts per contact per day

Run once: python create_agent_tables.py
"""

import os
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
    # ── 1. Agent Call Logs ──────────────────────────────────────
    """
    CREATE TABLE IF NOT EXISTS agent_call_logs (
        id              VARCHAR(36) PRIMARY KEY,
        call_id         VARCHAR(100),
        room_name       VARCHAR(100),
        phone_number    VARCHAR(20) NOT NULL,
        caller_name     VARCHAR(255),
        contact_type    ENUM('buyer','broker') DEFAULT 'buyer',
        direction       ENUM('inbound','outbound') DEFAULT 'outbound',

        -- Outcome fields (Section 3.2)
        disposition     VARCHAR(50),
        qualification   VARCHAR(50),
        interest_level  VARCHAR(20),
        outcome_reason  TEXT,
        next_action     VARCHAR(50),

        -- Meeting / scheduling
        meeting_slot    DATETIME NULL,
        manager_assigned VARCHAR(255) NULL,
        next_callback   DATETIME NULL,

        -- Transcript & recording
        transcript      LONGTEXT,
        call_summary    TEXT,
        recording_url   VARCHAR(500),

        -- Duration & timing
        duration_seconds INT DEFAULT 0,
        started_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
        ended_at        DATETIME NULL,

        -- Notes
        notes           TEXT,

        -- Metadata
        created_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
        updated_at      DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,

        INDEX idx_phone (phone_number),
        INDEX idx_started (started_at),
        INDEX idx_disposition (disposition)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,

    # ── 2. Do Not Call List (Section 5.1) ───────────────────────
    """
    CREATE TABLE IF NOT EXISTS agent_dnc_list (
        id          INT AUTO_INCREMENT PRIMARY KEY,
        phone_number VARCHAR(20) NOT NULL UNIQUE,
        reason      VARCHAR(255) DEFAULT 'user_requested',
        added_by    VARCHAR(100) DEFAULT 'agent',
        created_at  DATETIME DEFAULT CURRENT_TIMESTAMP,

        INDEX idx_phone (phone_number)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,

    # ── 3. Manager Pool (Section 3.4) ──────────────────────────
    """
    CREATE TABLE IF NOT EXISTS agent_managers (
        id              INT AUTO_INCREMENT PRIMARY KEY,
        name            VARCHAR(255) NOT NULL,
        phone           VARCHAR(20),
        email           VARCHAR(255),
        regions         TEXT COMMENT 'Comma-separated regions/locations',
        projects        TEXT COMMENT 'Comma-separated project names',
        is_active       TINYINT(1) DEFAULT 1,
        do_not_allocate TINYINT(1) DEFAULT 0,
        preferred       TINYINT(1) DEFAULT 0,
        last_allocated  DATETIME NULL COMMENT 'For round-robin tracking',
        total_allocated INT DEFAULT 0,
        created_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
        updated_at      DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,

        INDEX idx_active (is_active),
        INDEX idx_last_alloc (last_allocated)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,

    # ── 4. Meeting / Site Visit Tracking (Section 3.3) ──────────
    """
    CREATE TABLE IF NOT EXISTS agent_meetings (
        id              VARCHAR(36) PRIMARY KEY,
        call_log_id     VARCHAR(36) COMMENT 'FK to agent_call_logs',
        phone_number    VARCHAR(20) NOT NULL,
        contact_name    VARCHAR(255),
        contact_type    ENUM('buyer','broker') DEFAULT 'buyer',

        -- Meeting details
        meeting_type    ENUM('site_visit','office_meeting','call_back','other') DEFAULT 'site_visit',
        meeting_date    DATE,
        meeting_time    TIME,
        meeting_datetime DATETIME,
        location        VARCHAR(500),
        project_name    VARCHAR(255),

        -- Manager / allocation
        manager_id      INT NULL,
        manager_name    VARCHAR(255),

        -- Status tracking
        status          ENUM('scheduled','confirmed','completed','cancelled','no_show') DEFAULT 'scheduled',
        qr_scanned      TINYINT(1) DEFAULT 0 COMMENT 'QR code confirmation for broker meetings',
        completed_at    DATETIME NULL,

        -- Calendar
        calendar_event_id VARCHAR(255) NULL COMMENT 'Google Calendar event ID',
        calendar_invite_sent TINYINT(1) DEFAULT 0,

        notes           TEXT,
        created_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
        updated_at      DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,

        INDEX idx_phone (phone_number),
        INDEX idx_status (status),
        INDEX idx_date (meeting_date),
        INDEX idx_manager (manager_id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,

    # ── 5. Call Attempts per Day (Section 6.3) ──────────────────
    """
    CREATE TABLE IF NOT EXISTS agent_call_attempts (
        id          INT AUTO_INCREMENT PRIMARY KEY,
        phone_number VARCHAR(20) NOT NULL,
        attempt_date DATE NOT NULL,
        attempt_count INT DEFAULT 1,
        last_attempt_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        last_result  VARCHAR(50) COMMENT 'answered, no_answer, busy, failed',
        call_log_id  VARCHAR(36) NULL,

        UNIQUE KEY uk_phone_date (phone_number, attempt_date),
        INDEX idx_phone (phone_number),
        INDEX idx_date (attempt_date)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
]


def create_tables():
    conn = get_connection()
    cur = conn.cursor()

    print("=" * 60)
    print("Creating Agent Tables")
    print("=" * 60)

    for i, ddl in enumerate(TABLES):
        table_name = ddl.strip().split("IF NOT EXISTS")[1].split("(")[0].strip()
        try:
            cur.execute(ddl)
            conn.commit()
            print(f"  [OK] [{i+1}/{len(TABLES)}] Created: {table_name}")
        except Exception as e:
            print(f"  [ERROR] [{i+1}/{len(TABLES)}] Error creating {table_name}: {e}")

    # Verify all tables exist
    print()
    print("Verifying tables...")
    expected = [
        "agent_call_logs", "agent_dnc_list", "agent_managers",
        "agent_meetings", "agent_call_attempts"
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
