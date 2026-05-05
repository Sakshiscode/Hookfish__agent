from api.db import execute_query
res = execute_query("SELECT COUNT(*) as c FROM agent_contacts WHERE list_id = '3566069d-f8cb-4ca2-9d51-aa72affae2c3'", fetch_one=True)
print('CONTACT_COUNT:' + str(res['c']))
