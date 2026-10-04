BEGIN;
-- Itemised usage behind each rated charge: one row per call, SMS or chargeable data session.
-- charge_minor is this event's rated share of the linked sandbox.money_entries posting (one posting may
-- cover several events, e.g. a batch of SMS); the posting stays the ledger's source of truth.
CREATE TABLE IF NOT EXISTS sandbox.rated_events (
 id uuid PRIMARY KEY, sandbox_id uuid NOT NULL REFERENCES sandbox.sandbox_runs(id) ON DELETE CASCADE,
 account_id uuid NOT NULL,
 event_kind text NOT NULL CHECK(event_kind IN ('VOICE_CALL','SMS','DATA_SESSION')),
 direction text NOT NULL DEFAULT 'OUTGOING' CHECK(direction IN ('OUTGOING','INCOMING')),
 counterparty text, started_at timestamptz NOT NULL,
 duration_seconds integer CHECK(duration_seconds>=0), volume_bytes bigint CHECK(volume_bytes>=0),
 rate_label text, charge_minor bigint NOT NULL DEFAULT 0 CHECK(charge_minor>=0),
 charge_entry_id uuid, recorded_at timestamptz NOT NULL,
 UNIQUE(sandbox_id,id),
 FOREIGN KEY(sandbox_id,account_id) REFERENCES sandbox.accounts(sandbox_id,id),
 FOREIGN KEY(sandbox_id,charge_entry_id) REFERENCES sandbox.money_entries(sandbox_id,id)
);
CREATE INDEX IF NOT EXISTS rated_events_account_time ON sandbox.rated_events(sandbox_id,account_id,started_at);
CREATE INDEX IF NOT EXISTS rated_events_charge ON sandbox.rated_events(sandbox_id,charge_entry_id);
COMMIT;
