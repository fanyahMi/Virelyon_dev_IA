"""Base de connaissances APEX : knowledge_base_documents, apex_knowledge_chunks

Revision ID: 0001
Revises:
Create Date: 2026-08-31

CDCF Unifié v3.0, §5.2 (Ingestion) / §5.4 (RAG).

Périmètre volontairement limité aux deux tables nécessaires à l'ingestion et
à la recherche vectorielle. Les tables conversations / messages_apex /
regles_escalade / agent_config_apex / feedback_satisfaction /
conversation_events viendront dans des migrations ultérieures, une étape à
la fois (cf. instruction explicite : ne pas développer APEX d'un coup).
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Dimension du vecteur d'embedding — voir le commentaire équivalent dans
# app/db/models_apex.py : valeur PROVISOIRE (convention OpenAI
# text-embedding-3-small/ada-002), le fournisseur n'étant pas encore choisi.
# Toute modification de cette dimension nécessite une nouvelle migration
# (ALTER COLUMN ... TYPE vector(n)), pas seulement un changement de config.
EMBEDDING_DIMENSION = 1536


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "knowledge_base_documents",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("type_source", sa.String(length=32), nullable=False),
        sa.Column("nom_fichier", sa.String(length=512), nullable=False),
        sa.Column("type_fichier", sa.String(length=64), nullable=True),
        sa.Column("storage_path", sa.Text(), nullable=True),
        sa.Column("contenu_brut", sa.Text(), nullable=True),
        sa.Column(
            "statut_indexation",
            sa.String(length=16),
            nullable=False,
            server_default="en_attente",
        ),
        sa.Column("message_erreur", sa.Text(), nullable=True),
        sa.Column(
            "uploaded_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("uploaded_by", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_index(
        "ix_kb_documents_workspace_id",
        "knowledge_base_documents",
        ["workspace_id"],
    )
    op.create_index(
        "ix_kb_documents_workspace_statut",
        "knowledge_base_documents",
        ["workspace_id", "statut_indexation"],
    )

    op.create_table(
        "apex_knowledge_chunks",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "document_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("knowledge_base_documents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("chunk_texte", sa.Text(), nullable=False),
        sa.Column("embedding", Vector(EMBEDDING_DIMENSION), nullable=False),
        sa.Column("position_dans_document", sa.Integer(), nullable=True),
        sa.Column(
            "metadonnees",
            postgresql.JSONB(),
            nullable=False,
            server_default="{}",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_apex_chunks_workspace_id",
        "apex_knowledge_chunks",
        ["workspace_id"],
    )
    op.create_index(
        "ix_apex_chunks_workspace_document",
        "apex_knowledge_chunks",
        ["workspace_id", "document_id"],
    )
    # Index HNSW pour la recherche par similarité cosinus (CDCF §5.4).
    # Créé en SQL brut : Alembic n'a pas d'API dédiée pour les index pgvector.
    op.execute(
        "CREATE INDEX ix_apex_chunks_embedding_hnsw ON apex_knowledge_chunks "
        "USING hnsw (embedding vector_cosine_ops)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_apex_chunks_embedding_hnsw")
    op.drop_index("ix_apex_chunks_workspace_document", table_name="apex_knowledge_chunks")
    op.drop_index("ix_apex_chunks_workspace_id", table_name="apex_knowledge_chunks")
    op.drop_table("apex_knowledge_chunks")

    op.drop_index("ix_kb_documents_workspace_statut", table_name="knowledge_base_documents")
    op.drop_index("ix_kb_documents_workspace_id", table_name="knowledge_base_documents")
    op.drop_table("knowledge_base_documents")

    # L'extension `vector` n'est PAS supprimée ici : d'autres tables/agents
    # peuvent en dépendre. La supprimer relèverait d'une décision globale,
    # pas de cette migration ponctuelle.
