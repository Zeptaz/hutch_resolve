BEGIN;
CREATE TABLE sandbox.sandbox_runs (
 id uuid PRIMARY KEY, fixture_version integer NOT NULL, simulation_clock timestamptz NOT NULL,
 owner_session uuid, created_at timestamptz NOT NULL DEFAULT now(), expires_at timestamptz NOT NULL
);
CREATE TABLE sandbox.customers (
 id uuid PRIMARY KEY, sandbox_id uuid NOT NULL REFERENCES sandbox.sandbox_runs(id) ON DELETE CASCADE,
 display_name text NOT NULL, preferred_language text NOT NULL CHECK(preferred_language IN ('en','si','ta')),
 UNIQUE(sandbox_id,id)
);
CREATE TABLE sandbox.accounts (
 id uuid PRIMARY KEY, sandbox_id uuid NOT NULL REFERENCES sandbox.sandbox_runs(id) ON DELETE CASCADE,
 customer_id uuid NOT NULL, line_alias text NOT NULL, account_type text NOT NULL CHECK(account_type='PREPAID'),
 status text NOT NULL CHECK(status IN ('ACTIVE','SUSPENDED','CLOSED')), region_code text NOT NULL,
 version integer NOT NULL DEFAULT 1, UNIQUE(sandbox_id,id), UNIQUE(sandbox_id,line_alias),
 FOREIGN KEY(sandbox_id,customer_id) REFERENCES sandbox.customers(sandbox_id,id)
);
CREATE TABLE sandbox.balance_snapshots (
 id uuid PRIMARY KEY, sandbox_id uuid NOT NULL REFERENCES sandbox.sandbox_runs(id) ON DELETE CASCADE,
 account_id uuid NOT NULL, wallet_kind text NOT NULL, amount_minor bigint NOT NULL, currency char(3) NOT NULL DEFAULT 'LKR',
 as_of timestamptz NOT NULL, last_posting_seq bigint NOT NULL,
 FOREIGN KEY(sandbox_id,account_id) REFERENCES sandbox.accounts(sandbox_id,id)
);
CREATE TABLE sandbox.money_entries (
 id uuid PRIMARY KEY, sandbox_id uuid NOT NULL REFERENCES sandbox.sandbox_runs(id) ON DELETE CASCADE,
 account_id uuid NOT NULL, wallet_kind text NOT NULL, posting_seq bigint NOT NULL, amount_minor bigint NOT NULL,
 currency char(3) NOT NULL DEFAULT 'LKR', kind text NOT NULL, occurred_at timestamptz NOT NULL,
 posted_at timestamptz NOT NULL, reversal_of uuid, reference text,
 UNIQUE(sandbox_id,id), UNIQUE(sandbox_id,account_id,wallet_kind,posting_seq),
 FOREIGN KEY(sandbox_id,account_id) REFERENCES sandbox.accounts(sandbox_id,id),
 FOREIGN KEY(sandbox_id,reversal_of) REFERENCES sandbox.money_entries(sandbox_id,id)
);
CREATE TABLE sandbox.recharges (
 id uuid PRIMARY KEY, sandbox_id uuid NOT NULL REFERENCES sandbox.sandbox_runs(id) ON DELETE CASCADE,
 account_id uuid NOT NULL, channel text NOT NULL, payment_ref text NOT NULL, amount_minor bigint NOT NULL,
 payment_status text NOT NULL CHECK(payment_status IN ('CAPTURED','PENDING','FAILED')),
 fulfilment_status text NOT NULL CHECK(fulfilment_status IN ('FULFILLED','PENDING','FAILED')),
 credited_entry_id uuid, created_at timestamptz NOT NULL,
 FOREIGN KEY(sandbox_id,account_id) REFERENCES sandbox.accounts(sandbox_id,id),
 FOREIGN KEY(sandbox_id,credited_entry_id) REFERENCES sandbox.money_entries(sandbox_id,id)
);
CREATE TABLE sandbox.offers (
 id uuid PRIMARY KEY, sandbox_id uuid NOT NULL REFERENCES sandbox.sandbox_runs(id) ON DELETE CASCADE,
 offer_kind text NOT NULL CHECK(offer_kind IN ('PACKAGE','VAS')), name text NOT NULL,
 price_minor bigint NOT NULL, currency char(3) NOT NULL DEFAULT 'LKR', validity_seconds integer,
 quota_bytes bigint, recurring boolean NOT NULL DEFAULT false, version integer NOT NULL DEFAULT 1,
 UNIQUE(sandbox_id,id)
);
CREATE TABLE sandbox.subscriptions (
 id uuid PRIMARY KEY, sandbox_id uuid NOT NULL REFERENCES sandbox.sandbox_runs(id) ON DELETE CASCADE,
 account_id uuid NOT NULL, offer_id uuid NOT NULL, status text NOT NULL,
 starts_at timestamptz NOT NULL, expires_at timestamptz, renew_enabled boolean NOT NULL DEFAULT false,
 next_renewal_at timestamptz, activation_evidence_ref text, version integer NOT NULL DEFAULT 1,
 FOREIGN KEY(sandbox_id,account_id) REFERENCES sandbox.accounts(sandbox_id,id),
 FOREIGN KEY(sandbox_id,offer_id) REFERENCES sandbox.offers(sandbox_id,id), UNIQUE(sandbox_id,id)
);
CREATE TABLE sandbox.subscription_events (
 id uuid PRIMARY KEY, sandbox_id uuid NOT NULL REFERENCES sandbox.sandbox_runs(id) ON DELETE CASCADE,
 subscription_id uuid NOT NULL, event_type text NOT NULL, charge_entry_id uuid,
 effective_at timestamptz NOT NULL, recorded_at timestamptz NOT NULL,
 FOREIGN KEY(sandbox_id,subscription_id) REFERENCES sandbox.subscriptions(sandbox_id,id),
 FOREIGN KEY(sandbox_id,charge_entry_id) REFERENCES sandbox.money_entries(sandbox_id,id)
);
CREATE TABLE sandbox.quota_buckets (
 id uuid PRIMARY KEY, sandbox_id uuid NOT NULL REFERENCES sandbox.sandbox_runs(id) ON DELETE CASCADE,
 account_id uuid NOT NULL, subscription_id uuid, bucket_kind text NOT NULL,
 valid_from timestamptz NOT NULL, valid_to timestamptz,
 FOREIGN KEY(sandbox_id,account_id) REFERENCES sandbox.accounts(sandbox_id,id),
 FOREIGN KEY(sandbox_id,subscription_id) REFERENCES sandbox.subscriptions(sandbox_id,id), UNIQUE(sandbox_id,id)
);
CREATE TABLE sandbox.quota_snapshots (
 id uuid PRIMARY KEY, sandbox_id uuid NOT NULL REFERENCES sandbox.sandbox_runs(id) ON DELETE CASCADE,
 bucket_id uuid NOT NULL, remaining_bytes bigint NOT NULL CHECK(remaining_bytes>=0),
 as_of timestamptz NOT NULL, last_quota_seq bigint NOT NULL,
 FOREIGN KEY(sandbox_id,bucket_id) REFERENCES sandbox.quota_buckets(sandbox_id,id)
);
CREATE TABLE sandbox.quota_entries (
 id uuid PRIMARY KEY, sandbox_id uuid NOT NULL REFERENCES sandbox.sandbox_runs(id) ON DELETE CASCADE,
 bucket_id uuid NOT NULL, sequence bigint NOT NULL, delta_bytes bigint NOT NULL,
 entry_kind text NOT NULL CHECK(entry_kind IN ('GRANT','CONSUME','EXPIRE','REVERSE')),
 usage_record_id uuid, reversal_of uuid, occurred_at timestamptz NOT NULL, recorded_at timestamptz NOT NULL,
 UNIQUE(sandbox_id,id), UNIQUE(sandbox_id,bucket_id,sequence),
 FOREIGN KEY(sandbox_id,bucket_id) REFERENCES sandbox.quota_buckets(sandbox_id,id),
 FOREIGN KEY(sandbox_id,reversal_of) REFERENCES sandbox.quota_entries(sandbox_id,id)
);
CREATE TABLE sandbox.usage_records (
 id uuid PRIMARY KEY, sandbox_id uuid NOT NULL REFERENCES sandbox.sandbox_runs(id) ON DELETE CASCADE,
 account_id uuid NOT NULL, bucket_id uuid, interval_start timestamptz NOT NULL, interval_end timestamptz NOT NULL,
 bytes bigint NOT NULL CHECK(bytes>=0), usage_kind text NOT NULL CHECK(usage_kind IN ('IN_BUNDLE','OUT_OF_BUNDLE')),
 charge_entry_id uuid, category text, recorded_at timestamptz NOT NULL,
 FOREIGN KEY(sandbox_id,account_id) REFERENCES sandbox.accounts(sandbox_id,id),
 FOREIGN KEY(sandbox_id,bucket_id) REFERENCES sandbox.quota_buckets(sandbox_id,id),
 FOREIGN KEY(sandbox_id,charge_entry_id) REFERENCES sandbox.money_entries(sandbox_id,id), UNIQUE(sandbox_id,id)
);
ALTER TABLE sandbox.quota_entries ADD CONSTRAINT quota_usage_fk FOREIGN KEY(sandbox_id,usage_record_id) REFERENCES sandbox.usage_records(sandbox_id,id);
CREATE TABLE sandbox.service_checks (
 id uuid PRIMARY KEY, sandbox_id uuid NOT NULL REFERENCES sandbox.sandbox_runs(id) ON DELETE CASCADE,
 account_id uuid NOT NULL, check_type text NOT NULL, result text NOT NULL, origin text NOT NULL,
 observed_at timestamptz NOT NULL, expires_at timestamptz NOT NULL, detail jsonb NOT NULL DEFAULT '{}'::jsonb,
 FOREIGN KEY(sandbox_id,account_id) REFERENCES sandbox.accounts(sandbox_id,id)
);
CREATE TABLE sandbox.incidents (
 id uuid PRIMARY KEY, sandbox_id uuid NOT NULL REFERENCES sandbox.sandbox_runs(id) ON DELETE CASCADE,
 region_code text NOT NULL, service text NOT NULL, status text NOT NULL,
 starts_at timestamptz NOT NULL, ends_at timestamptz, updated_at timestamptz NOT NULL, eta timestamptz
);
CREATE TABLE sandbox.tickets (
 id uuid PRIMARY KEY, sandbox_id uuid NOT NULL REFERENCES sandbox.sandbox_runs(id) ON DELETE CASCADE,
 account_id uuid NOT NULL, case_ref text NOT NULL, category text NOT NULL, queue text NOT NULL,
 status text NOT NULL, packet jsonb NOT NULL, agent_notes text NOT NULL DEFAULT '', version integer NOT NULL DEFAULT 1,
 UNIQUE(sandbox_id,case_ref), FOREIGN KEY(sandbox_id,account_id) REFERENCES sandbox.accounts(sandbox_id,id)
);
CREATE TABLE sandbox.provider_operations (
 id uuid PRIMARY KEY, sandbox_id uuid NOT NULL REFERENCES sandbox.sandbox_runs(id) ON DELETE CASCADE,
 provider text NOT NULL, idempotency_key text NOT NULL, request_hash text NOT NULL,
 target_id uuid NOT NULL, expected_version integer, status text NOT NULL,
 result jsonb NOT NULL DEFAULT '{}'::jsonb, created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(sandbox_id,provider,idempotency_key)
);
CREATE TABLE sandbox.fault_profiles (
 id uuid PRIMARY KEY, sandbox_id uuid NOT NULL REFERENCES sandbox.sandbox_runs(id) ON DELETE CASCADE,
 provider text NOT NULL, operation text NOT NULL, selector jsonb NOT NULL DEFAULT '{}'::jsonb,
 fault_type text NOT NULL, parameters jsonb NOT NULL DEFAULT '{}'::jsonb, remaining_uses integer NOT NULL DEFAULT 1 CHECK(remaining_uses>=0)
);
CREATE INDEX money_entries_statement ON sandbox.money_entries(sandbox_id,account_id,wallet_kind,posting_seq);
CREATE INDEX usage_account_window ON sandbox.usage_records(sandbox_id,account_id,interval_start);
CREATE INDEX ticket_queue ON sandbox.tickets(sandbox_id,queue,status);
CREATE INDEX incident_region_service ON sandbox.incidents(sandbox_id,region_code,service,starts_at);
COMMIT;
