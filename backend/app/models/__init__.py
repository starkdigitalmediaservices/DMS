from app.models.tenant import Tenant
from app.models.user import User, UserRole
from app.models.folder import Folder
from app.models.document import Document
from app.models.document_version import DocumentVersion
from app.models.chunk import Chunk
from app.models.metadata_item import MetadataItem
from app.models.metadata_item_region import MetadataItemRegion
from app.models.audit_log import AuditLog
from app.models.chat_session import ChatSession
from app.models.chat_message import ChatMessage
from app.models.api_log import ApiLog
from app.models.sys_config import SysConfig
from app.models.page import DocumentPage
from app.models.fact import Fact
from app.models.fact_region import FactRegion
from app.models.template import Template
from app.models.entity_node import EntityNode
from app.models.entity_edge import EntityEdge
from app.models.record import Record
from app.models.record_amendment import RecordAmendment
from app.models.corpus_calibration import CorpusCalibration
from app.models.department import Department, DepartmentMember, DepartmentFolder, UserFolder
from app.models.role import Role
from app.models.retention_class import RetentionClass
from app.models.translation import Translation
from app.models.subscription import Subscription
from app.models.license import License
from app.models.table_shape_decision import TableShapeDecision
from app.models.ocr_archive import OCRArchive
from app.models.vlm_archive import VLMArchive
from app.models.field_trust_signal import FieldTrustSignal
from app.models.search_glossary import SearchGlossaryTerm
from app.models.review import ReviewOriginal, ReviewState, ReviewAuditEntry
from app.database import Base

# Every model must be imported here so SQLAlchemy's declarative registry
# (and Alembic's autogenerate) can see it — that's the whole purpose of
# this file, even though nothing in it calls these names directly.
# __all__ tells pyflakes that on purpose, instead of ~30 false-positive
# "imported but unused" warnings breaking CI's syntax/import check.
__all__ = [
    "Tenant", "User", "UserRole", "Folder", "Document", "DocumentVersion",
    "Chunk", "MetadataItem", "MetadataItemRegion", "AuditLog", "ChatSession", "ChatMessage",
    "ApiLog", "SysConfig", "DocumentPage", "Fact", "FactRegion", "Template",
    "EntityNode", "EntityEdge", "Record", "RecordAmendment", "CorpusCalibration",
    "Department", "DepartmentMember", "DepartmentFolder", "UserFolder", "Role", "RetentionClass",
    "Translation", "Subscription", "License", "TableShapeDecision",
    "OCRArchive", "VLMArchive", "FieldTrustSignal", "SearchGlossaryTerm",
    "ReviewOriginal", "ReviewState", "ReviewAuditEntry", "Base",
]


