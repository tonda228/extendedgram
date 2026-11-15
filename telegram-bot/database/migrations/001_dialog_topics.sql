-- Run once against an existing database, before starting the updated bot.
-- Preserve existing rows; dialogs already lost to the old key need resyncing.
BEGIN;

ALTER TABLE dialog_priority DROP CONSTRAINT IF EXISTS dialog_priority_dialog_id_user_id_fkey;
ALTER TABLE dialog_priority DROP CONSTRAINT IF EXISTS dialog_priority_dialog_id_user_id_topic_id_fkey;
ALTER TABLE private_message DROP CONSTRAINT IF EXISTS private_message_dialog_id_user_id_fkey;
ALTER TABLE private_message DROP CONSTRAINT IF EXISTS private_message_dialog_id_user_id_topic_id_fkey;

ALTER TABLE dialog_priority ADD COLUMN IF NOT EXISTS topic_id BIGINT;
ALTER TABLE private_message ADD COLUMN IF NOT EXISTS topic_id BIGINT;

UPDATE dialog SET topic_id = 0 WHERE topic_id IS NULL;
-- The old schema had one dialog row per user/chat. Use its saved topic.
UPDATE dialog_priority p SET topic_id = d.topic_id
FROM dialog d WHERE p.dialog_id = d.dialog_id AND p.user_id = d.user_id
AND p.topic_id IS NULL;
UPDATE private_message p SET topic_id = d.topic_id
FROM dialog d WHERE p.dialog_id = d.dialog_id AND p.user_id = d.user_id
AND p.topic_id IS NULL;

ALTER TABLE dialog DROP CONSTRAINT IF EXISTS dialog_pkey;
ALTER TABLE dialog ADD PRIMARY KEY (dialog_id, user_id, topic_id);
ALTER TABLE dialog_priority DROP CONSTRAINT IF EXISTS dialog_priority_pkey;
ALTER TABLE dialog_priority ADD PRIMARY KEY (dialog_id, user_id, topic_id, date_time);
ALTER TABLE private_message DROP CONSTRAINT IF EXISTS private_message_pkey;
ALTER TABLE private_message ADD PRIMARY KEY (message_id, dialog_id, user_id, topic_id);

ALTER TABLE dialog_priority ALTER COLUMN topic_id SET DEFAULT 0;
ALTER TABLE private_message ALTER COLUMN topic_id SET DEFAULT 0;
ALTER TABLE dialog_priority ADD FOREIGN KEY (dialog_id, user_id, topic_id)
REFERENCES dialog (dialog_id, user_id, topic_id) ON DELETE CASCADE;
ALTER TABLE private_message ADD FOREIGN KEY (dialog_id, user_id, topic_id)
REFERENCES dialog (dialog_id, user_id, topic_id) ON DELETE CASCADE;

COMMIT;
