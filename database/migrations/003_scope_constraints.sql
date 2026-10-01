BEGIN;
ALTER TABLE resolve.sessions
  ADD CONSTRAINT sessions_sandbox_account_fk
    FOREIGN KEY(sandbox_id,account_id) REFERENCES sandbox.accounts(sandbox_id,id),
  ADD CONSTRAINT sessions_sandbox_id_uq UNIQUE(sandbox_id,id);
ALTER TABLE resolve.conversations ADD COLUMN sandbox_id uuid;
UPDATE resolve.conversations c SET sandbox_id=s.sandbox_id FROM resolve.sessions s WHERE s.id=c.session_id;
ALTER TABLE resolve.conversations
  ADD CONSTRAINT conversations_sandbox_fk FOREIGN KEY(sandbox_id) REFERENCES sandbox.sandbox_runs(id),
  ADD CONSTRAINT conversations_scoped_session_fk FOREIGN KEY(sandbox_id,session_id) REFERENCES resolve.sessions(sandbox_id,id),
  ADD CONSTRAINT conversations_sandbox_id_uq UNIQUE(sandbox_id,id);
ALTER TABLE resolve.cases ADD COLUMN sandbox_id uuid;
UPDATE resolve.cases c SET sandbox_id=co.sandbox_id FROM resolve.conversations co WHERE co.id=c.conversation_id;
ALTER TABLE resolve.cases ALTER COLUMN sandbox_id SET NOT NULL;
ALTER TABLE resolve.cases
  ADD CONSTRAINT cases_sandbox_fk FOREIGN KEY(sandbox_id) REFERENCES sandbox.sandbox_runs(id),
  ADD CONSTRAINT cases_sandbox_account_fk FOREIGN KEY(sandbox_id,account_id) REFERENCES sandbox.accounts(sandbox_id,id),
  ADD CONSTRAINT cases_scoped_conversation_fk FOREIGN KEY(sandbox_id,conversation_id) REFERENCES resolve.conversations(sandbox_id,id),
  ADD CONSTRAINT cases_sandbox_id_uq UNIQUE(sandbox_id,id);
ALTER TABLE resolve.cases ADD CONSTRAINT cases_id_sandbox_uq UNIQUE(id,sandbox_id);
ALTER TABLE resolve.conversations DROP CONSTRAINT conversation_active_case_fk;
ALTER TABLE resolve.conversations
  ADD CONSTRAINT conversation_scoped_active_case_fk FOREIGN KEY(active_case_id,sandbox_id) REFERENCES resolve.cases(id,sandbox_id);
ALTER TABLE resolve.investigations
  ADD CONSTRAINT investigations_case_id_uq UNIQUE(case_id,id);
ALTER TABLE resolve.action_proposals
  ADD CONSTRAINT proposals_case_investigation_fk FOREIGN KEY(case_id,investigation_id) REFERENCES resolve.investigations(case_id,id),
  ADD CONSTRAINT proposals_case_id_uq UNIQUE(case_id,id);
ALTER TABLE resolve.operations
  ADD CONSTRAINT operations_case_proposal_fk FOREIGN KEY(case_id,proposal_id) REFERENCES resolve.action_proposals(case_id,id);
ALTER TABLE resolve.voice_bindings ADD COLUMN sandbox_id uuid;
UPDATE resolve.voice_bindings vb SET sandbox_id=co.sandbox_id FROM resolve.conversations co WHERE co.id=vb.conversation_id;
ALTER TABLE resolve.voice_bindings ALTER COLUMN sandbox_id SET NOT NULL;
ALTER TABLE resolve.voice_bindings
  ADD CONSTRAINT voice_binding_scoped_conversation_fk FOREIGN KEY(sandbox_id,conversation_id) REFERENCES resolve.conversations(sandbox_id,id),
  ADD CONSTRAINT voice_binding_scoped_account_fk FOREIGN KEY(sandbox_id,account_id) REFERENCES sandbox.accounts(sandbox_id,id);
COMMIT;
