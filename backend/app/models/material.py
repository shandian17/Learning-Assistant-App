from ..extensions import db
from .base import CreatedAtMixin, TimestampMixin, UUIDPrimaryKeyMixin, utc_now


material_version_status = db.Enum(
    "processing", "ready", "failed", name="material_version_status", native_enum=False, create_constraint=True
)


class Material(UUIDPrimaryKeyMixin, TimestampMixin, db.Model):
    __tablename__ = "materials"

    filename = db.Column(db.String(512), nullable=False)
    normalized_filename = db.Column(db.String(512), nullable=False)
    file_type = db.Column(db.String(32), nullable=False)
    current_version_id = db.Column(
        db.String(36),
        db.ForeignKey("material_versions.id", name="fk_material_current_version", use_alter=True),
        nullable=True,
    )

    versions = db.relationship(
        "MaterialVersion",
        back_populates="material",
        foreign_keys="MaterialVersion.material_id",
        order_by="MaterialVersion.version_no",
        lazy="selectin",
        overlaps="current_version",
    )
    current_version = db.relationship(
        "MaterialVersion",
        foreign_keys=[current_version_id],
        post_update=True,
        uselist=False,
        overlaps="material,versions",
    )
    note = db.relationship("MaterialNote", back_populates="material", uselist=False)

    __table_args__ = (db.UniqueConstraint("normalized_filename", name="uq_materials_normalized_filename"),)

    def to_list_dict(self) -> dict:
        latest = self.versions[-1] if self.versions else None
        pending = latest if latest and latest.id != self.current_version_id else None
        display_version = pending or self.current_version or latest
        data = {
            "id": self.id,
            "filename": self.filename,
            "file_type": self.file_type,
            "file_size": display_version.file_size if display_version else None,
            "language": display_version.language if display_version else "zh-CN",
            "current_version_id": self.current_version_id,
            "status": display_version.status if display_version else "failed",
            "error_message": display_version.error_message if display_version else "资料没有可用版本",
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }
        if self.current_version:
            data["current_version"] = {
                "id": self.current_version.id,
                "status": self.current_version.status,
                "language": self.current_version.language,
            }
        if pending:
            data["pending_version"] = {
                "id": pending.id,
                "status": pending.status,
                "file_size": pending.file_size,
                "language": pending.language,
                "error_message": pending.error_message,
            }
        return data


class MaterialVersion(UUIDPrimaryKeyMixin, db.Model):
    __tablename__ = "material_versions"

    material_id = db.Column(db.String(36), db.ForeignKey("materials.id", ondelete="RESTRICT"), nullable=False)
    version_no = db.Column(db.Integer, nullable=False)
    original_filename = db.Column(db.String(512), nullable=False)
    storage_path = db.Column(db.String(1024), nullable=False)
    file_size = db.Column(db.BigInteger, nullable=False)
    language = db.Column(db.String(8), nullable=False, default="zh-CN", server_default="zh-CN")
    status = db.Column(material_version_status, nullable=False, default="processing")
    error_message = db.Column(db.Text, nullable=True)
    uploaded_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utc_now)
    ready_at = db.Column(db.DateTime(timezone=True), nullable=True)

    material = db.relationship(
        "Material",
        back_populates="versions",
        foreign_keys=[material_id],
        overlaps="current_version",
    )
    chunks = db.relationship("MaterialChunk", back_populates="version", order_by="MaterialChunk.chunk_index")
    knowledge_points = db.relationship("KnowledgePoint", back_populates="version")

    __table_args__ = (
        db.UniqueConstraint("material_id", "version_no", name="uq_material_version_number"),
        db.CheckConstraint("version_no > 0", name="ck_material_version_positive"),
        db.CheckConstraint("file_size >= 0", name="ck_material_version_file_size"),
        db.Index("ix_material_versions_material_status", "material_id", "status"),
    )


class MaterialChunk(UUIDPrimaryKeyMixin, db.Model):
    __tablename__ = "material_chunks"

    version_id = db.Column(db.String(36), db.ForeignKey("material_versions.id", ondelete="RESTRICT"), nullable=False)
    chunk_index = db.Column(db.Integer, nullable=False)
    text = db.Column(db.Text, nullable=False)
    locator_json = db.Column(db.JSON, nullable=False, default=dict)

    version = db.relationship("MaterialVersion", back_populates="chunks")

    __table_args__ = (
        db.UniqueConstraint("version_id", "chunk_index", name="uq_material_chunk_index"),
        db.CheckConstraint("chunk_index >= 0", name="ck_material_chunk_index"),
    )


class KnowledgePoint(UUIDPrimaryKeyMixin, CreatedAtMixin, db.Model):
    __tablename__ = "knowledge_points"

    version_id = db.Column(db.String(36), db.ForeignKey("material_versions.id", ondelete="RESTRICT"), nullable=False)
    name = db.Column(db.String(512), nullable=False)
    description = db.Column(db.Text, nullable=False)

    version = db.relationship("MaterialVersion", back_populates="knowledge_points")
    sources = db.relationship("KnowledgePointSource", back_populates="knowledge_point")


class KnowledgePointSource(db.Model):
    __tablename__ = "knowledge_point_sources"

    knowledge_point_id = db.Column(
        db.String(36), db.ForeignKey("knowledge_points.id", ondelete="RESTRICT"), primary_key=True
    )
    chunk_id = db.Column(db.String(36), db.ForeignKey("material_chunks.id", ondelete="RESTRICT"), primary_key=True)

    knowledge_point = db.relationship("KnowledgePoint", back_populates="sources")
    chunk = db.relationship("MaterialChunk")


class MaterialNote(db.Model):
    __tablename__ = "material_notes"

    material_id = db.Column(db.String(36), db.ForeignKey("materials.id", ondelete="CASCADE"), primary_key=True)
    content = db.Column(db.Text, nullable=False, default="")
    updated_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now)

    material = db.relationship("Material", back_populates="note")
