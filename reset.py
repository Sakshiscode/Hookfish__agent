from db_helper import get_connection
from dotenv import load_dotenv

load_dotenv()

with get_connection() as conn:
    with conn.cursor() as cur:
        cur.execute(
            "DELETE FROM agent_call_attempts WHERE phone_number = '+917028425604 '"
        )
        conn.commit()
        print('Reset done')
