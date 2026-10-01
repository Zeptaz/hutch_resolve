BEGIN;
CREATE TABLE resolve.sessions (
 id uuid PRIMARY KEY, credential_hash bytea NOT NULL UNIQUE, role text NOT NULL CHECK(role IN ('CUSTOMER','AGENT','SIMULATOR')),
 account_id uuid, sandbox_id uuid REFERENCES sandbox.sandbox_runs(id), created_at timestamptz NOT NULL DEFAULT now(), expires_at timestamptz NOT NULL,
 revoked_at timestamptz
);
CREATE TABLE resolve.conversations (
 id uuid PRIMARY KEY, session_id uuid NOT NULL REFERENCES resolve.sessions(id), version integer NOT NULL DEFAULT 1,
 active_case_id uuid, created_at timestamptz NOT NULL DEFAULT now(), expires_at timestamptz NOT NULL
);
CREATE TABLE resolve.messages (
 id uuid PRIMARY KEY, conversation_id uuid NOT NULL REFERENCES resolve.conversations(id) ON DELETE CASCADE,
 client_turn_id uuid NOT NULL, speaker text NOT NULL, body text NOT NULL, result jsonb NOT NULL DEFAULT '{}'::jsonb,
 created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(conversation_id,client_turn_id,speaker)
);
CREATE TABLE resolve.cases (
 id uuid PRIMARY KEY, conversation_id uuid NOT NULL REFERENCES resolve.conversations(id), account_id uuid NOT NULL,
 complaint_type text NOT NULL, window_start timestamptz, window_end timestamptz, status text NOT NULL,
 version integer NOT NULL DEFAULT 1, created_at timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE resolve.conversations ADD CONSTRAINT conversation_active_case_fk FOREIGN KEY(active_case_id) REFERENCES resolve.cases(id) DEFERRABLE INITIALLY DEFERRED;
CREATE TABLE resolve.investigations (
 id uuid PRIMARY KEY, case_id uuid NOT NULL REFERENCES resolve.cases(id), revision integer NOT NULL,
 evidence_state text NOT NULL CHECK(evidence_state IN ('SUFFICIENT','PARTIAL','CONFLICTING')),
 finding jsonb NOT NULL, evidence jsonb NOT NULL, created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(case_id,revision)
);
CREATE TABLE resolve.action_proposals (
 id uuid PRIMARY KEY, case_id uuid NOT NULL REFERENCES resolve.cases(id), investigation_id uuid NOT NULL REFERENCES resolve.investigations(id),
 action_type text NOT NULL CHECK(action_type IN ('DEACTIVATE_VAS','SEND_SETTINGS_INSTRUCTIONS','CREATE_REVIEW_TICKET')),
 target_id uuid NOT NULL, target_version integer, consequences jsonb NOT NULL, proposal_hash text NOT NULL,
 expires_at timestamptz NOT NULL, invalidated_at timestamptz
);
CREATE TABLE resolve.operations (
 id uuid PRIMARY KEY, case_id uuid NOT NULL REFERENCES resolve.cases(id), proposal_id uuid NOT NULL REFERENCES resolve.action_proposals(id),
 idempotency_key text NOT NULL UNIQUE, status text NOT NULL CHECK(status IN ('PENDING','RUNNING','SUCCEEDED','FAILED','UNKNOWN','REVIEW_REQUIRED')),
 confirmation jsonb NOT NULL, outcome jsonb NOT NULL DEFAULT '{}'::jsonb,
 lease_until timestamptz, created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE resolve.receipts (
 id uuid PRIMARY KEY, case_id uuid NOT NULL REFERENCES resolve.cases(id), revision integer NOT NULL,
 receipt jsonb NOT NULL, digest_sha256 text NOT NULL, created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(case_id,revision)
);
CREATE TABLE resolve.voice_bindings (
 id uuid PRIMARY KEY, conversation_id uuid NOT NULL REFERENCES resolve.conversations(id), voice_session_id text NOT NULL UNIQUE,
 account_id uuid NOT NULL, origin text NOT NULL, expires_at timestamptz NOT NULL, revoked_at timestamptz
);
CREATE TABLE resolve.integration_events (
 id uuid PRIMARY KEY, provider text NOT NULL, event_id text NOT NULL, request_hash text NOT NULL,
 response jsonb NOT NULL, created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(provider,event_id)
);
CREATE TABLE resolve.audit_events (
 id uuid PRIMARY KEY, session_id uuid REFERENCES resolve.sessions(id), case_id uuid REFERENCES resolve.cases(id),
 event_type text NOT NULL, details jsonb NOT NULL DEFAULT '{}'::jsonb, created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE resolve.model_calls (
 id uuid PRIMARY KEY, conversation_id uuid REFERENCES resolve.conversations(id), provider text NOT NULL,
 model text NOT NULL, input_tokens integer, output_tokens integer, latency_ms integer, outcome text NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE resolve.knowledge_articles (
 id uuid PRIMARY KEY, article_key text NOT NULL, language text NOT NULL CHECK(language IN ('en','si','ta')),
 title text NOT NULL, content text NOT NULL, aliases text[] NOT NULL DEFAULT '{}', source_url text,
 reviewed_at date NOT NULL, version integer NOT NULL, scope text NOT NULL CHECK(scope IN ('PUBLIC','SYNTHETIC')),
 search_vector tsvector GENERATED ALWAYS AS (to_tsvector('simple'::regconfig,title||' '||content)) STORED,
 UNIQUE(article_key,language,version)
);
CREATE INDEX knowledge_search ON resolve.knowledge_articles USING gin(search_vector);
CREATE INDEX knowledge_aliases ON resolve.knowledge_articles USING gin(aliases);
CREATE INDEX messages_turns ON resolve.messages(conversation_id,created_at);
CREATE INDEX cases_conversation ON resolve.cases(conversation_id,created_at);
CREATE INDEX operations_status ON resolve.operations(status,updated_at);
COMMIT;
