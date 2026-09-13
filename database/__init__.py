import os

import psycopg
from dotenv import load_dotenv
from pgvector.psycopg import register_vector
from psycopg.rows import namedtuple_row

load_dotenv()

# Initializing database
DB_NAME = os.environ["DB_NAME"]
DB_USER = os.environ["DB_USER"]
DB_PASSWORD = os.environ["DB_PASSWORD"]
DB_HOST = os.environ["DB_HOST"]
connection = psycopg.connect(dbname=DB_NAME, user=DB_USER, password=DB_PASSWORD, host=DB_HOST, port=5432, row_factory=namedtuple_row)
connection.execute("CREATE EXTENSION IF NOT EXISTS vector")
register_vector(connection)
cur = connection.cursor()


def drop_tables() -> None:
    cur.execute("""
    DROP TABLE IF EXISTS app_user CASCADE;
    DROP TABLE IF EXISTS telegram_user CASCADE;
    DROP TABLE IF EXISTS channel CASCADE;
    DROP TABLE IF EXISTS dialog CASCADE;
    DROP TABLE IF EXISTS dialog_priority CASCADE;
    DROP TABLE IF EXISTS public_message CASCADE;
    DROP TABLE IF EXISTS private_message CASCADE;
    """)

def initialize_db() -> None:
    cur.execute("""                    
                CREATE TABLE IF NOT EXISTS app_user
                (
                    user_id                BIGINT PRIMARY KEY REFERENCES telegram_user (user_id),
                    string_session         TEXT    NOT NULL,
                    set_read_after_summary BOOLEAN NOT NULL,
                    set_read_after_search  BOOLEAN NOT NULL,
                    allow_all              BOOLEAN NOT NULL,
                    preloading             BOOLEAN NOT NULL,
                    history_size           BIGINT  NOT NULL, 
                    is_admin               BOOLEAN NOT NULL DEFAULT FALSE,
                    last_status            BIGINT
                );

                CREATE TABLE IF NOT EXISTS telegram_user
                (
                    user_id   BIGINT PRIMARY KEY,
                    user_name TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS channel
                (
                    channel_id BIGINT,
                    topic_id   BIGINT DEFAULT 0,
                    topic_name TEXT,

                    PRIMARY KEY (channel_id, topic_id)
                );
                
                CREATE TABLE IF NOT EXISTS dialog
                (
                    dialog_id  BIGINT,
                    user_id    BIGINT REFERENCES app_user (user_id) ON DELETE CASCADE,
                    title      TEXT    NOT NULL,
                    is_allowed BOOLEAN NOT NULL DEFAULT TRUE,
                    channel_id BIGINT  NULL,
                    topic_id   BIGINT  NULL,

                    PRIMARY KEY (dialog_id, user_id),
                    FOREIGN KEY (channel_id, topic_id)
                        REFERENCES channel (channel_id, topic_id)
                        ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS dialog_priority
                (
                    dialog_id BIGINT,
                    user_id   BIGINT,
                    date_time TIMESTAMPTZ,

                    PRIMARY KEY (dialog_id, user_id, date_time),
                    FOREIGN KEY (dialog_id, user_id)
                        REFERENCES dialog (dialog_id, user_id)
                        ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS public_message
                (
                    message_id        BIGINT,
                    channel_id        BIGINT,
                    topic_id          BIGINT,
                    sender_id         BIGINT REFERENCES telegram_user (user_id) ON DELETE CASCADE,
                    date_time         TIMESTAMPTZ NOT NULL,
                    text              TEXT,
                    media_description TEXT,
                    embedding         vector(1024),

                    PRIMARY KEY (message_id, channel_id, topic_id),
                    FOREIGN KEY (channel_id, topic_id)
                        REFERENCES channel (channel_id, topic_id)
                        ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS private_message
                (
                    message_id        BIGINT,
                    dialog_id         BIGINT,
                    user_id           BIGINT,
                    sender_id         BIGINT REFERENCES telegram_user (user_id) ON DELETE CASCADE,
                    date_time         TIMESTAMPTZ NOT NULL,
                    text              TEXT,
                    media_description TEXT,
                    embedding         vector(1024),

                    PRIMARY KEY (message_id, dialog_id, user_id),
                    FOREIGN KEY (dialog_id, user_id)
                        REFERENCES dialog (dialog_id, user_id)
                        ON DELETE CASCADE
                );

                CREATE INDEX ON public_message USING hnsw (embedding vector_cosine_ops);
                CREATE INDEX ON private_message USING hnsw (embedding vector_cosine_ops);
                """)
    connection.commit()
