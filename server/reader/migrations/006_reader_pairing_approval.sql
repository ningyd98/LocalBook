-- Require trusted local owner approval before Reader device registration.
ALTER TABLE pairing_tokens ADD COLUMN approved_at INTEGER;
