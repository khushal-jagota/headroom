-- Chief, every Manager with a conversation, five largest Ticket conversations by stored event payload bytes.
SELECT json_object('kind','chief','id','chief','conversation_id',conversation_id) FROM agents WHERE agent_key='chief_of_staff';
SELECT json_object('kind','manager','id',m.id,'conversation_id',a.conversation_id) FROM managers m JOIN agents a ON a.agent_key='manager_'||m.id WHERE a.conversation_id IS NOT NULL ORDER BY m.created_at;
SELECT json_object('kind','ticket','id',t.id,'conversation_id',t.conversation_id,'payload_bytes',s.bytes) FROM tickets t JOIN (SELECT conversation_id, SUM(LENGTH(payload)) AS bytes FROM conversation_events GROUP BY conversation_id) s ON s.conversation_id=t.conversation_id ORDER BY s.bytes DESC LIMIT 5;
