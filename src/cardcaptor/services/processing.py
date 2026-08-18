"""Full processing pipeline: detection -> OCR -> matching -> grading -> review flags."""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

import cv2
import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import AppConfig, get_config
from ..db.models import Activity, Card, SourceImage, Student, Submission, utcnow
from ..ocr import OCREngine, get_default_engine
from ..vision.color import analyze_card_color
from ..vision.detector import detect_cards, normalized_center
from ..vision.perspective import correct_perspective
from . import audit
from .activity import grading_rules_for
from .grading import grade_answer, mcq_choices
from .review import apply_flags
from .roster import match_student

logger = logging.getLogger(__name__)

ProgressCallback = Callable[[str], None]


@dataclass
class ProcessingResult:
    images_processed: int = 0
    images_skipped: int = 0
    cards_detected: int = 0
    submissions_created: int = 0
    submissions_updated: int = 0
    needs_review: int = 0
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "images_processed": self.images_processed,
            "images_skipped": self.images_skipped,
            "cards_detected": self.cards_detected,
            "submissions_created": self.submissions_created,
            "submissions_updated": self.submissions_updated,
            "needs_review": self.needs_review,
            "errors": self.errors,
        }


def _resolve_path(config: AppConfig, stored_path: str) -> Path:
    path = Path(stored_path)
    return path if path.is_absolute() else config.data_dir / path


def _has_instructor_input(card: Card) -> bool:
    """True when a card carries instructor corrections that must be preserved."""
    submission = card.submission
    if submission is None:
        return False
    return any(
        value is not None
        for value in (
            submission.instructor_name,
            submission.instructor_email,
            submission.instructor_answer,
            submission.instructor_student_id,
            submission.instructor_score,
            submission.reviewed_at,
        )
    )


def detect_cards_for_image(
    session: Session,
    source_image: SourceImage,
    config: AppConfig,
    force: bool = False,
) -> list[Card]:
    """Detect and extract all cards from one source image."""
    existing = list(
        session.scalars(select(Card).where(Card.source_image_id == source_image.id))
    )
    if existing and not force:
        return existing
    if existing and force:
        protected = [card for card in existing if _has_instructor_input(card)]
        if protected:
            # Re-detecting would discard reviewed or corrected submissions, so the
            # existing cards are kept and only their recognition is redone.
            return existing
        for card in existing:
            session.delete(card)
        session.flush()

    image_path = _resolve_path(config, source_image.stored_path)
    image = cv2.imread(str(image_path), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError(f"Could not read image: {image_path}")

    cards_dir = config.cards_dir / f"activity_{source_image.activity_id}"
    cards_dir.mkdir(parents=True, exist_ok=True)

    created: list[Card] = []
    for detection in detect_cards(image):
        crop = correct_perspective(image, detection.corners)
        color = analyze_card_color(crop)
        card_uuid = str(uuid.uuid4())
        crop_path = cards_dir / f"{card_uuid}.png"
        cv2.imwrite(str(crop_path), crop)
        cx, cy = normalized_center(detection, image.shape)

        card = Card(
            source_image_id=source_image.id,
            card_uuid=card_uuid,
            crop_path=str(crop_path.relative_to(config.data_dir)),
            corner_points=json.dumps(
                [[float(x), float(y)] for x, y in np.asarray(detection.corners).reshape(4, 2)]
            ),
            position_x=cx,
            position_y=cy,
            color_lab_l=color.lab_l,
            color_lab_a=color.lab_a,
            color_lab_b=color.lab_b,
            color_category=color.category,
            color_confidence=color.confidence,
        )
        session.add(card)
        created.append(card)

    session.flush()
    audit.log_action(
        session,
        "source_image",
        source_image.id,
        "card_detection",
        "system",
        {"cards_detected": len(created)},
    )
    return created


def _candidates_json(candidates: list[tuple[str, float]]) -> str:
    return json.dumps([[text, round(float(conf), 4)] for text, conf in candidates])


def recognize_card(
    engine: OCREngine, card_image: np.ndarray, constraints: Optional[list[str]], answer_field: str
) -> dict[str, object]:
    """Run OCR on the three card regions."""
    regions = engine.segment_card_regions(card_image)
    name_result = engine.recognize_field(regions.get("name", card_image), "name")
    email_result = engine.recognize_field(regions.get("email", card_image), "email")
    answer_result = engine.recognize_field(
        regions.get("answer", card_image), answer_field, constraints
    )
    return {"name": name_result, "email": email_result, "answer": answer_result}


def process_card(
    session: Session,
    activity: Activity,
    card: Card,
    engine: OCREngine,
    roster: list[Student],
    config: AppConfig,
    force: bool = False,
) -> Optional[Submission]:
    """Run OCR, roster matching and grading for a single card."""
    submission = session.scalars(
        select(Submission).where(Submission.card_id == card.id)
    ).first()
    if submission is not None and submission.processed_at is not None and not force:
        return submission

    if submission is None:
        submission = Submission(activity_id=activity.id, card_id=card.id)
        session.add(submission)

    crop_path = _resolve_path(config, card.crop_path)
    card_image = cv2.imread(str(crop_path), cv2.IMREAD_COLOR)
    if card_image is None:
        raise ValueError(f"Could not read card crop: {crop_path}")

    rules = grading_rules_for(activity)
    answer_field = "mcq" if rules.rule_type == "mcq" else (
        "number" if rules.rule_type == "numeric" else "answer"
    )
    constraints = mcq_choices(rules) if rules.rule_type == "mcq" else None

    results = recognize_card(engine, card_image, constraints, answer_field)
    name_result = results["name"]
    email_result = results["email"]
    answer_result = results["answer"]

    submission.raw_name = name_result.raw_text
    submission.raw_email = email_result.raw_text
    submission.raw_answer = answer_result.raw_text
    submission.name_confidence = name_result.confidence
    submission.email_confidence = email_result.confidence
    submission.answer_confidence = answer_result.confidence
    submission.name_candidates = _candidates_json(name_result.candidates)
    submission.email_candidates = _candidates_json(email_result.candidates)
    submission.answer_candidates = _candidates_json(answer_result.candidates)

    email_domain = activity.course.email_domain if activity.course is not None else None
    match = match_student(
        submission.effective_name, submission.effective_email, roster, email_domain
    )
    submission.student_id = match.student_id
    submission.match_confidence = match.confidence
    submission.match_method = "ambiguous" if match.ambiguous else match.method

    grade = grade_answer(submission.effective_answer, rules)
    submission.auto_score = grade.score
    submission.auto_grade_rule = grade.rule_used
    if submission.instructor_score is None:
        submission.final_score = grade.score
    submission.processed_at = utcnow()

    session.flush()
    audit.log_action(
        session,
        "submission",
        submission.id,
        "ocr_result",
        "system",
        {
            "card_uuid": card.card_uuid,
            "name_confidence": submission.name_confidence,
            "email_confidence": submission.email_confidence,
            "answer_confidence": submission.answer_confidence,
            "match_method": submission.match_method,
            "match_confidence": submission.match_confidence,
            "auto_score": submission.auto_score,
        },
    )
    return submission


def process_activity(
    session: Session,
    activity: Activity,
    config: Optional[AppConfig] = None,
    engine: Optional[OCREngine] = None,
    force: bool = False,
    progress: Optional[ProgressCallback] = None,
) -> ProcessingResult:
    """Run the full pipeline for every imported photo of an activity.

    The pipeline is resumable: already-processed images and cards are skipped
    unless ``force`` is set.
    """
    config = config or get_config()
    config.ensure_dirs()
    engine = engine or get_default_engine(config)
    result = ProcessingResult()

    roster = list(
        session.scalars(select(Student).where(Student.course_id == activity.course_id))
    )

    images = list(
        session.scalars(
            select(SourceImage)
            .where(SourceImage.activity_id == activity.id)
            .order_by(SourceImage.id)
        )
    )

    all_cards: list[Card] = []
    for image in images:
        try:
            existing = list(
                session.scalars(select(Card).where(Card.source_image_id == image.id))
            )
            if existing and not force:
                result.images_skipped += 1
                all_cards.extend(existing)
                continue
            cards = detect_cards_for_image(session, image, config, force=force)
            all_cards.extend(cards)
            result.images_processed += 1
            result.cards_detected += len(cards)
            if progress:
                progress(f"{image.original_filename}: {len(cards)} card(s) detected")
        except Exception as exc:  # keep processing the remaining photos
            logger.exception("Failed to detect cards in %s", image.original_filename)
            result.errors.append(f"{image.original_filename}: {exc}")

    for card in all_cards:
        try:
            before = session.scalars(
                select(Submission).where(Submission.card_id == card.id)
            ).first()
            existed = before is not None and before.processed_at is not None
            submission = process_card(
                session, activity, card, engine, roster, config, force=force
            )
            if submission is None:
                continue
            if existed and not force:
                continue
            if existed:
                result.submissions_updated += 1
            else:
                result.submissions_created += 1
            if progress:
                progress(f"card {card.card_uuid[:8]}: processed")
        except Exception as exc:
            logger.exception("Failed to process card %s", card.card_uuid)
            result.errors.append(f"card {card.card_uuid}: {exc}")

    session.flush()
    submissions = list(
        session.scalars(select(Submission).where(Submission.activity_id == activity.id))
    )
    thresholds = {
        "ocr_confidence": config.ocr_confidence_threshold,
        "min_confidence_for_auto_review": config.min_confidence_for_auto_review,
        "match_confidence": config.min_match_confidence,
    }
    for submission in submissions:
        apply_flags(submission, submissions, activity, thresholds)
    result.needs_review = sum(1 for s in submissions if s.needs_review)

    audit.log_action(
        session, "activity", activity.id, "process", "system", result.as_dict()
    )
    session.commit()
    return result
