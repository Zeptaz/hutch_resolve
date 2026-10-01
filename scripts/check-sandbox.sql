WITH active AS (SELECT id FROM sandbox.sandbox_runs ORDER BY created_at DESC LIMIT 1)
SELECT a.line_alias, b.amount_minor AS closing_minor,
       sum(m.amount_minor) AS posted_minor,
       b.amount_minor-sum(m.amount_minor) AS delta_minor
FROM active r
JOIN sandbox.accounts a ON a.sandbox_id=r.id
JOIN sandbox.balance_snapshots b ON b.sandbox_id=a.sandbox_id AND b.account_id=a.id AND b.last_posting_seq=4
JOIN sandbox.money_entries m ON m.sandbox_id=a.sandbox_id AND m.account_id=a.id
WHERE a.line_alias IN ('SIM-LK-0001','SIM-LK-0004')
GROUP BY a.line_alias,b.amount_minor ORDER BY a.line_alias;

WITH active AS (SELECT id FROM sandbox.sandbox_runs ORDER BY created_at DESC LIMIT 1)
SELECT a.line_alias, sum(qe.delta_bytes) AS movement_bytes,
       max(qs.remaining_bytes) AS snapshot_bytes
FROM active r
JOIN sandbox.accounts a ON a.sandbox_id=r.id
JOIN sandbox.quota_buckets qb ON qb.sandbox_id=a.sandbox_id AND qb.account_id=a.id
JOIN sandbox.quota_entries qe ON qe.sandbox_id=qb.sandbox_id AND qe.bucket_id=qb.id
JOIN sandbox.quota_snapshots qs ON qs.sandbox_id=qb.sandbox_id AND qs.bucket_id=qb.id
WHERE a.line_alias IN ('SIM-LK-0002','SIM-LK-0003')
GROUP BY a.line_alias ORDER BY a.line_alias;

WITH active AS (SELECT id FROM sandbox.sandbox_runs ORDER BY created_at DESC LIMIT 1)
SELECT count(*) AS customers,
       (SELECT count(*) FROM sandbox.accounts WHERE sandbox_id=(SELECT id FROM active)) AS accounts,
       (SELECT count(*) FROM sandbox.fault_profiles WHERE sandbox_id=(SELECT id FROM active)) AS fault_profiles
FROM sandbox.customers WHERE sandbox_id=(SELECT id FROM active);
