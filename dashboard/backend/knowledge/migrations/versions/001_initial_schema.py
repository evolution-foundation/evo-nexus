"""Initial Knowledge Base schema — 7 tables + indexes.

Creates the complete remote Postgres schema for pgvector-knowledge v1:
  knowledge_config, knowledge_spaces, knowledge_units, knowledge_documents,
  knowledge_chunks (with HNSW + GIN indexes), knowledge_api_keys,
  knowledge_api_usage, knowledge_classify_queue.

Revision ID: 001
Revises:
Create Date: 2026-04-20
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()

    # ------------------------------------------------------------------
    # pgvector extension — must exist before creating vector columns
    # ------------------------------------------------------------------
    conn.execute(sa.text("CREATE EXTENSION IF NOT EXISTS vector"))

    # ------------------------------------------------------------------
    # knowledge_config — global config (singleton row, id = 1)
    # ------------------------------------------------------------------
    op.create_table(
        "knowledge_config",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("embedder_provider", sa.Text(), nullable=False),
        sa.Column("embedder_model", sa.Text(), nullable=False),
        sa.Column("vector_dim", sa.Integer(), nullable=False),
        sa.Column("parser_default", sa.Text(), server_default="marker"),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint("id = 1", name="ck_knowledge_config_singleton"),
    )

    # ------------------------------------------------------------------
    # knowledge_spaces
    # ------------------------------------------------------------------
    op.create_table(
        "knowledge_spaces",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("slug", sa.Text(), unique=True, nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("owner_id", sa.Text()),
        sa.Column("visibility", sa.Text(), server_default="private"),
        sa.Column("access_rules", postgresql.JSONB(), server_default="{}"),
        sa.Column("content_type_boosts", postgresql.JSONB(), server_default="{}"),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
        ),
    )

    # ------------------------------------------------------------------
    # knowledge_units
    # ------------------------------------------------------------------
    op.create_table(
        "knowledge_units",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "space_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("knowledge_spaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("slug", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("sequence_idx", sa.Integer(), server_default="0"),
        sa.Column("prerequisites", postgresql.ARRAY(postgresql.UUID(as_uuid=True))),
        sa.Column("metadata", postgresql.JSONB(), server_default="{}"),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("space_id", "slug", name="uq_knowledge_units_space_slug"),
    )

    # ------------------------------------------------------------------
    # knowledge_documents
    # ------------------------------------------------------------------
    op.create_table(
        "knowledge_documents",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "space_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("knowledge_spaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "unit_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("knowledge_units.id", ondelete="SET NULL"),
        ),
        sa.Column("source_type", sa.Text()),
        sa.Column("source_uri", sa.Text()),
        sa.Column("mime_type", sa.Text()),
        sa.Column("title", sa.Text()),
        sa.Column("description", sa.Text()),
        sa.Column("tags", postgresql.ARRAY(sa.Text())),
        sa.Column("content_type", sa.Text()),
        sa.Column("difficulty_level", sa.Text()),
        sa.Column("topics", postgresql.ARRAY(sa.Text())),
        sa.Column("owner_id", sa.Text()),
        sa.Column("visibility", sa.Text(), server_default="inherit"),
        sa.Column("metadata", postgresql.JSONB()),
        sa.Column("size_bytes", sa.BigInteger()),
        sa.Column("status", sa.Text(), server_default="pending"),
        sa.Column("error_message", sa.Text()),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
        ),
        sa.Column("indexed_at", sa.TIMESTAMP(timezone=True)),
    )
    op.create_index(
        "idx_docs_content_type",
        "knowledge_documents",
        ["content_type"],
    )
    op.create_index(
        "idx_docs_topics",
        "knowledge_documents",
        ["topics"],
        postgresql_using="gin",
    )

    # ------------------------------------------------------------------
    # knowledge_chunks — vector column dimension is set at migration time
    # using the vector_dim from knowledge_config. The migration seeded below
    # uses 768 (local provider default). A future migration handles dim changes.
    # ------------------------------------------------------------------
    conn.execute(
        sa.text(
            """
            CREATE TABLE knowledge_chunks (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                document_id UUID REFERENCES knowledge_documents(id) ON DELETE CASCADE NOT NULL,
                space_id UUID REFERENCES knowledge_spaces(id) ON DELETE CASCADE NOT NULL,
                unit_id UUID,
                chunk_idx INT NOT NULL,
                chunk_type TEXT,
                content TEXT NOT NULL,
                metadata JSONB,
                embedding vector(768),
                content_tsv tsvector GENERATED ALWAYS AS (to_tsvector('portuguese', content)) STORED,
                created_at TIMESTAMPTZ DEFAULT now()
            )
            """
        )
    )
    # HNSW index for vector similarity search (pgvector 0.5+)
    conn.execute(
        sa.text(
            """
            CREATE INDEX idx_chunks_embedding ON knowledge_chunks
            USING hnsw (embedding vector_cosine_ops)
            WITH (m = 16, ef_construction = 64)
            """
        )
    )
    # GIN index for full-text search (BM25-like)
    conn.execute(
        sa.text(
            "CREATE INDEX idx_chunks_tsv ON knowledge_chunks USING gin (content_tsv)"
        )
    )
    conn.execute(
        sa.text("CREATE INDEX idx_chunks_space ON knowledge_chunks(space_id)")
    )
    conn.execute(
        sa.text("CREATE INDEX idx_chunks_unit ON knowledge_chunks(unit_id)")
    )

    # ------------------------------------------------------------------
    # knowledge_api_keys
    # ------------------------------------------------------------------
    op.create_table(
        "knowledge_api_keys",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("token_hash", sa.Text(), nullable=False),
        sa.Column("name", sa.Text()),
        sa.Column("space_ids", postgresql.ARRAY(postgresql.UUID(as_uuid=True))),
        sa.Column("scopes", postgresql.ARRAY(sa.Text())),
        sa.Column("rate_limit_per_min", sa.Integer(), server_default="60"),
        sa.Column("rate_limit_per_day", sa.Integer(), server_default="10000"),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
        ),
        sa.Column("last_used_at", sa.TIMESTAMP(timezone=True)),
        sa.Column("expires_at", sa.TIMESTAMP(timezone=True)),
    )

    # ------------------------------------------------------------------
    # knowledge_api_usage — fixed window rate limiting counter
    # ------------------------------------------------------------------
    op.create_table(
        "knowledge_api_usage",
        sa.Column(
            "api_key_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("knowledge_api_keys.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("window_start", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("request_count", sa.Integer(), server_default="0"),
        sa.PrimaryKeyConstraint("api_key_id", "window_start"),
    )
    op.create_index(
        "idx_usage_window",
        "knowledge_api_usage",
        ["window_start"],
    )

    # ------------------------------------------------------------------
    # knowledge_classify_queue — async document intelligence queue
    # Includes locked_at + lock_timeout_seconds for atomic checkout (ADR-008)
    # ------------------------------------------------------------------
    op.create_table(
        "knowledge_classify_queue",
        sa.Column(
            "document_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("knowledge_documents.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("attempts", sa.Integer(), server_default="0"),
        sa.Column("last_error", sa.Text()),
        sa.Column(
            "enqueued_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
        ),
        sa.Column("locked_at", sa.TIMESTAMP(timezone=True)),
        sa.Column("locked_by", sa.Text()),
        sa.Column("lock_timeout_seconds", sa.Integer(), server_default="600"),
    )
    op.create_index(
        "idx_classify_queue_pending",
        "knowledge_classify_queue",
        ["enqueued_at"],
        postgresql_where=sa.text("locked_at IS NULL"),
    )

    # ------------------------------------------------------------------
    # Seed knowledge_config with local provider defaults
    # ------------------------------------------------------------------
    conn.execute(
        sa.text(
            """
            INSERT INTO knowledge_config (id, embedder_provider, embedder_model, vector_dim)
            VALUES (1, 'local', 'sentence-transformers/paraphrase-multilingual-mpnet-base-v2', 768)
            ON CONFLICT (id) DO NOTHING
            """
        )
    )


def downgrade() -> None:
    op.drop_table("knowledge_classify_queue")
    op.drop_table("knowledge_api_usage")
    op.drop_table("knowledge_api_keys")
    op.get_bind().execute(sa.text("DROP TABLE IF EXISTS knowledge_chunks"))
    op.drop_table("knowledge_documents")
    op.drop_table("knowledge_units")
    op.drop_table("knowledge_spaces")
    op.drop_table("knowledge_config")
