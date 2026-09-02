"""Modèles ORM SQLAlchemy pour la base de connaissances d'APEX
(CDCF Unifié v3.0, §5.2 Ingestion / §5.4 RAG).

Périmètre de cette étape : uniquement les deux tables nécessaires à
l'ingestion + à la recherche vectorielle (`knowledge_base_documents`,
`apex_knowledge_chunks`). Les autres tables mentionnées par le CDCF
(conversations, messages_apex, regles_escalade, agent_config_apex,
feedback_satisfaction, conversation_events) seront ajoutées aux étapes
suivantes, une par une.

Isolation multi-tenant (CDCF §5.4 : filtrage « jamais laissé au LLM ») :
`workspace_id` est présent sur les deux tables (dupliqué sur les chunks pour
permettre un filtrage direct sans jointure) et DOIT être utilisé dans toute
requête écrite au niveau service — voir app/apex/service.py (étape
ultérieure). Aucune clé étrangère n'est posée vers une éventuelle table
`workspaces` : son existence/propriétaire n'est pas confirmée par le CDCF
(point à clarifier — voir le message d'accompagnement de cette livraison).
"""
import uuid
from datetime import datetime
from enum import StrEnum

from pgvector.sqlalchemy import Vector
from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.config import get_settings
from app.db.base import Base


class StatutIndexation(StrEnum):
    """Cycle de vie de l'indexation d'un document (CDCF §5.2)."""

    EN_ATTENTE = "en_attente"
    INDEXE = "indexe"
    ECHEC = "echec"


class TypeSourceDocument(StrEnum):
    """Origine du contenu ingéré.

    Volontairement limité à ce qui est demandé maintenant (upload de fichier
    et FAQ saisie manuellement). L'import par URL/connecteur n'est pas
    demandé à ce stade — ne pas l'ajouter par anticipation.
    """

    UPLOAD_FICHIER = "upload_fichier"
    FAQ_SAISIE = "faq_saisie"


# Dimension du vecteur d'embedding utilisée par la colonne pgvector.
#
# Le fournisseur d'embedding n'est PAS encore choisi (décision explicitement
# différée). 1536 est une valeur provisoire (convention OpenAI
# text-embedding-3-small / ada-002) et n'a pas d'autre justification.
#
# Cette dimension est lue une seule fois, à l'import de ce module (via
# `get_settings()`, elle-même mise en cache par `lru_cache`), car
# `mapped_column(Vector(dim))` fige `dim` dans la définition de la colonne au
# moment où la classe Python est construite. Changer `EMBEDDING_DIMENSION`
# (variable d'environnement) exige donc de redémarrer le process ET d'écrire
# une nouvelle migration Alembic pour modifier la colonne — ce n'est pas une
# valeur relue dynamiquement à chaque requête.
EMBEDDING_DIMENSION = get_settings().embedding_dimension


class KnowledgeBaseDocument(Base):
    """Un document source de la base de connaissances (avant découpage).

    Le texte brut extrait (`contenu_brut`) est conservé pour permettre une
    ré-indexation (changement de stratégie de chunking, de modèle
    d'embedding...) sans devoir retélécharger le fichier depuis Supabase
    Storage.
    """

    __tablename__ = "knowledge_base_documents"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, index=True
    )

    type_source: Mapped[str] = mapped_column(String(32), nullable=False)
    nom_fichier: Mapped[str] = mapped_column(String(512), nullable=False)
    type_fichier: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Chemin dans Supabase Storage (bucket/clé) — null pour une FAQ saisie
    # directement, sans fichier associé.
    storage_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Texte déjà extrait du fichier (PDF/DOCX/TXT) ou saisi (FAQ).
    contenu_brut: Mapped[str | None] = mapped_column(Text, nullable=True)

    statut_indexation: Mapped[str] = mapped_column(
        String(16), nullable=False, default=StatutIndexation.EN_ATTENTE.value
    )
    message_erreur: Mapped[str | None] = mapped_column(Text, nullable=True)

    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    # Pas de ForeignKey : aucune table "utilisateurs" confirmée par le CDCF
    # à ce stade (voir message d'accompagnement).
    uploaded_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )

    chunks: Mapped[list["ApexKnowledgeChunk"]] = relationship(
        back_populates="document",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class ApexKnowledgeChunk(Base):
    """Un fragment (chunk) indexé d'un document, avec son embedding pgvector.

    `workspace_id` est dupliqué depuis le document parent : le CDCF §5.4
    exige un filtrage par workspace directement dans la requête SQL de
    recherche vectorielle, sans dépendre d'une jointure.
    """

    __tablename__ = "apex_knowledge_chunks"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, index=True
    )
    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("knowledge_base_documents.id", ondelete="CASCADE"),
        nullable=False,
    )

    chunk_texte: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[list[float]] = mapped_column(
        Vector(EMBEDDING_DIMENSION), nullable=False
    )
    position_dans_document: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )
    # Métadonnées libres (ex. page source, titre de section...).
    metadonnees: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    document: Mapped["KnowledgeBaseDocument"] = relationship(back_populates="chunks")
