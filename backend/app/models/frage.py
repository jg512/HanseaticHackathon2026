"""Offene Fragen zu einem Vorgang, mit Kommentaren. Ohne Einfluss auf Stand und Ausgabe."""

from datetime import datetime, timezone

from ..db import db

QUESTION_STATUS = ("offen", "geklaert")


def _jetzt():
    return datetime.now(timezone.utc)


def _iso(zeit):
    return zeit.isoformat() if zeit else None


class Question(db.Model):
    __tablename__ = "question"

    id = db.Column(db.Integer, primary_key=True)
    bom_id = db.Column(db.ForeignKey("bom.id"), nullable=False)
    bom_item_id = db.Column(db.ForeignKey("bom_item.id"))  # optional: Frage zu einer Position
    text = db.Column(db.String, nullable=False)
    created_by = db.Column(db.String, nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=_jetzt)
    status = db.Column(db.String, nullable=False, default="offen")  # einer aus QUESTION_STATUS
    answer = db.Column(db.String)
    resolved_by = db.Column(db.String)
    resolved_at = db.Column(db.DateTime)

    bom = db.relationship("Bom", back_populates="questions")
    item = db.relationship("BomItem")
    comments = db.relationship("QuestionComment", order_by="QuestionComment.id",
                               cascade="all, delete-orphan")

    def to_dict(self):
        item = self.item
        return {"id": self.id, "bom_id": self.bom_id, "bom_item_id": self.bom_item_id,
                "item": {"id": item.id, "pos": item.pos, "profile_no": item.profile_no,
                         "description": item.description} if item else None,
                "text": self.text, "created_by": self.created_by,
                "created_at": _iso(self.created_at), "status": self.status,
                "answer": self.answer, "resolved_by": self.resolved_by,
                "resolved_at": _iso(self.resolved_at),
                "comments": [c.to_dict() for c in self.comments]}


class QuestionComment(db.Model):
    __tablename__ = "question_comment"

    id = db.Column(db.Integer, primary_key=True)
    question_id = db.Column(db.ForeignKey("question.id"), nullable=False)
    text = db.Column(db.String, nullable=False)
    created_by = db.Column(db.String, nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=_jetzt)

    def to_dict(self):
        return {"id": self.id, "text": self.text, "created_by": self.created_by,
                "created_at": _iso(self.created_at)}
