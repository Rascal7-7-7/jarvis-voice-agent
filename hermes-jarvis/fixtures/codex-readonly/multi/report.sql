SELECT u.name, COUNT(*) AS orders
FROM users u, orders o
WHERE u.id = o.user_id
  AND o.created_at > '2026-01-01'
ORDER BY orders DESC;
