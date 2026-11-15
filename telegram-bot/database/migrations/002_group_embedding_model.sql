ALTER TABLE user_group ADD COLUMN IF NOT EXISTS embedding_model TEXT;

CREATE TABLE IF NOT EXISTS public_message_model_embedding (
    model TEXT NOT NULL,
    channel_id BIGINT NOT NULL,
    topic_id BIGINT NOT NULL,
    message_id BIGINT NOT NULL,
    embedding vector(1024) NOT NULL,
    PRIMARY KEY (model, channel_id, topic_id, message_id),
    FOREIGN KEY (message_id, channel_id, topic_id)
        REFERENCES public_message (message_id, channel_id, topic_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS private_message_model_embedding (
    model TEXT NOT NULL,
    dialog_id BIGINT NOT NULL,
    user_id BIGINT NOT NULL,
    topic_id BIGINT NOT NULL,
    message_id BIGINT NOT NULL,
    embedding vector(1024) NOT NULL,
    PRIMARY KEY (model, dialog_id, user_id, topic_id, message_id),
    FOREIGN KEY (message_id, dialog_id, user_id, topic_id)
        REFERENCES private_message (message_id, dialog_id, user_id, topic_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS public_message_model_embedding_message_idx
    ON public_message_model_embedding (message_id, channel_id, topic_id);

CREATE INDEX IF NOT EXISTS private_message_model_embedding_message_idx
    ON private_message_model_embedding (message_id, dialog_id, user_id, topic_id);
