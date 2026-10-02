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
JOIN LATERAL (
  SELECT remaining_bytes FROM sandbox.quota_snapshots qs
  WHERE qs.sandbox_id=qb.sandbox_id AND qs.bucket_id=qb.id
  ORDER BY qs.as_of DESC,qs.last_quota_seq DESC,qs.id LIMIT 1
) qs ON true
WHERE a.line_alias IN ('SIM-LK-0002','SIM-LK-0003')
GROUP BY a.line_alias,qs.remaining_bytes ORDER BY a.line_alias;

WITH active AS (SELECT id FROM sandbox.sandbox_runs ORDER BY created_at DESC LIMIT 1)
SELECT count(*) AS customers,
       (SELECT count(*) FROM sandbox.accounts WHERE sandbox_id=(SELECT id FROM active)) AS accounts,
       (SELECT count(*) FROM sandbox.fault_profiles WHERE sandbox_id=(SELECT id FROM active)) AS fault_profiles
FROM sandbox.customers WHERE sandbox_id=(SELECT id FROM active);

-- Fail loudly when the versioned fixture invariants drift.
DO $$
DECLARE
  active_run uuid;
  fixture_version integer;
  opening_minor bigint;
  closing_minor bigint;
  posting_minor bigint;
  duplicate_snapshots integer;
  quota_remaining bigint;
  quota_snapshot bigint;
  scenario record;
BEGIN
  SELECT id, sandbox_runs.fixture_version
    INTO active_run, fixture_version
    FROM sandbox.sandbox_runs
    ORDER BY created_at DESC, id DESC
    LIMIT 1;
  IF active_run IS NULL OR fixture_version <> 2 THEN
    RAISE EXCEPTION 'Expected latest fixture version 2; found %', fixture_version;
  END IF;

  SELECT b.amount_minor INTO opening_minor
    FROM sandbox.balance_snapshots b JOIN sandbox.accounts a
      ON (a.sandbox_id,a.id)=(b.sandbox_id,b.account_id)
   WHERE b.sandbox_id=active_run AND a.line_alias='SIM-LK-0002' AND b.last_posting_seq=0
   ORDER BY b.as_of LIMIT 1;
  SELECT b.amount_minor INTO closing_minor
    FROM sandbox.balance_snapshots b JOIN sandbox.accounts a
      ON (a.sandbox_id,a.id)=(b.sandbox_id,b.account_id)
   WHERE b.sandbox_id=active_run AND a.line_alias='SIM-LK-0002' AND b.last_posting_seq=1
   ORDER BY b.as_of DESC LIMIT 1;
  SELECT coalesce(sum(m.amount_minor),0) INTO posting_minor
    FROM sandbox.money_entries m JOIN sandbox.accounts a
      ON (a.sandbox_id,a.id)=(m.sandbox_id,m.account_id)
   WHERE m.sandbox_id=active_run AND a.line_alias='SIM-LK-0002';
  IF opening_minor <> 10000 OR closing_minor <> 2000 OR posting_minor <> -8000
     OR opening_minor + posting_minor <> closing_minor THEN
    RAISE EXCEPTION 'Fixture B ledger invariant failed: opening %, postings %, closing %', opening_minor, posting_minor, closing_minor;
  END IF;

  FOR scenario IN
    SELECT * FROM (VALUES
      ('SIM-LK-0001'::text, 42000::bigint),
      ('SIM-LK-0004'::text, 35000::bigint)
    ) AS expected(line_alias, expected_closing)
  LOOP
    SELECT b.amount_minor INTO opening_minor
      FROM sandbox.balance_snapshots b JOIN sandbox.accounts a
        ON (a.sandbox_id,a.id)=(b.sandbox_id,b.account_id)
     WHERE b.sandbox_id=active_run AND a.line_alias=scenario.line_alias AND b.last_posting_seq=0
     ORDER BY b.as_of LIMIT 1;
    SELECT b.amount_minor INTO closing_minor
      FROM sandbox.balance_snapshots b JOIN sandbox.accounts a
        ON (a.sandbox_id,a.id)=(b.sandbox_id,b.account_id)
     WHERE b.sandbox_id=active_run AND a.line_alias=scenario.line_alias AND b.last_posting_seq=4
     ORDER BY b.as_of DESC LIMIT 1;
    SELECT coalesce(sum(m.amount_minor),0) INTO posting_minor
      FROM sandbox.money_entries m JOIN sandbox.accounts a
        ON (a.sandbox_id,a.id)=(m.sandbox_id,m.account_id)
     WHERE m.sandbox_id=active_run AND a.line_alias=scenario.line_alias AND m.posting_seq BETWEEN 1 AND 4;
    IF opening_minor <> 0 OR posting_minor <> 42000
       OR closing_minor <> scenario.expected_closing THEN
      RAISE EXCEPTION 'Fixture % ledger changed: opening %, postings %, closing %',
        scenario.line_alias, opening_minor, posting_minor, closing_minor;
    END IF;
  END LOOP;

  SELECT count(*) INTO duplicate_snapshots FROM (
    SELECT b.account_id,b.wallet_kind,b.as_of
      FROM sandbox.balance_snapshots b
     WHERE b.sandbox_id=active_run
     GROUP BY b.account_id,b.wallet_kind,b.as_of
    HAVING count(*) > 1
  ) duplicates;
  IF duplicate_snapshots <> 0 THEN
    RAISE EXCEPTION 'Fixture contains % duplicate balance snapshots', duplicate_snapshots;
  END IF;

  IF EXISTS (
    SELECT 1 FROM sandbox.usage_records u JOIN sandbox.accounts a
      ON (a.sandbox_id,a.id)=(u.sandbox_id,u.account_id)
     WHERE u.sandbox_id=active_run AND a.line_alias='SIM-LK-0002' AND u.category IS NOT NULL
  ) THEN
    RAISE EXCEPTION 'Fixture B must not claim a usage category';
  END IF;
  SELECT sum(qe.delta_bytes), qs.remaining_bytes
    INTO quota_remaining, quota_snapshot
    FROM sandbox.accounts a
    JOIN sandbox.quota_buckets qb ON (qb.sandbox_id,qb.account_id)=(a.sandbox_id,a.id)
    JOIN sandbox.quota_entries qe ON (qe.sandbox_id,qe.bucket_id)=(qb.sandbox_id,qb.id)
    JOIN LATERAL (
      SELECT snapshot.remaining_bytes
        FROM sandbox.quota_snapshots snapshot
       WHERE snapshot.sandbox_id=qb.sandbox_id AND snapshot.bucket_id=qb.id
       ORDER BY snapshot.as_of DESC, snapshot.last_quota_seq DESC
       LIMIT 1
    ) qs ON true
   WHERE a.sandbox_id=active_run AND a.line_alias='SIM-LK-0002'
   GROUP BY qs.remaining_bytes;
  IF quota_remaining <> 0 OR quota_snapshot <> 0 THEN
    RAISE EXCEPTION 'Fixture B quota invariant failed: movements %, snapshot %', quota_remaining, quota_snapshot;
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM sandbox.accounts a
      JOIN sandbox.subscriptions s ON (s.sandbox_id,s.account_id)=(a.sandbox_id,a.id)
      JOIN sandbox.offers o ON (o.sandbox_id,o.id)=(s.sandbox_id,s.offer_id)
      JOIN sandbox.quota_buckets qb ON (qb.sandbox_id,qb.subscription_id)=(s.sandbox_id,s.id)
      JOIN sandbox.quota_entries qe ON (qe.sandbox_id,qe.bucket_id)=(qb.sandbox_id,qb.id)
     WHERE a.sandbox_id=active_run AND a.line_alias='SIM-LK-0003'
     GROUP BY o.quota_bytes
    HAVING o.quota_bytes=10000000000 AND sum(qe.delta_bytes)=9700000000
  ) THEN
    RAISE EXCEPTION 'Fixture C offer/grant/quota relationship is inconsistent';
  END IF;
END $$;
