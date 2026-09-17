import json
from collections import Counter
from decimal import Decimal
from difflib import SequenceMatcher
import re

from ..extensions import db
from ..models import Assessment, AssessmentAnswer, AssessmentQuestion, AssessmentSubmission
from ..models.base import utc_now
from .ai_prompts import GRADING_SYSTEM_PROMPT, QUESTION_SYSTEM_PROMPT
from .language import language_name
from .llm_client import LLMClient


class AssessmentAIError(RuntimeError):
    pass


def question_type_counts(total: int) -> dict[str, int]:
    if total not in {5, 8, 10, 15}:
        raise ValueError("题数必须是 5、8、10 或 15")
    choice = (total + 1) // 2
    true_false = (total + 2) // 4
    return {"single_choice": choice, "true_false": true_false, "short_answer": total - choice - true_false}


def difficulty_counts(total: int) -> dict[str, int]:
    base, remainder = divmod(total, 3)
    counts = {"easy": base, "medium": base, "hard": base}
    if remainder:
        counts["medium"] += 1
    if remainder > 1:
        counts["easy"] += 1
    return counts


def generate_assessment_task(app, assessment_id: str) -> None:
    with app.app_context():
        assessment = db.session.get(Assessment, assessment_id)
        if assessment is None or assessment.status != "generating":
            return
        try:
            documents, source_map, point_ids = _assessment_documents(assessment)
            if not source_map or not point_ids:
                raise AssessmentAIError("所选资料没有足够的可用文本")
            expected_types = assessment.question_counts_json
            total = sum(expected_types.values())
            payload = {
                "response_language": language_name(assessment.language),
                "question_counts": expected_types,
                "difficulty_counts": difficulty_counts(total),
                "documents": documents,
            }
            result = LLMClient().chat_json(
                [
                    {"role": "system", "content": QUESTION_SYSTEM_PROMPT},
                    {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
                ],
                temperature=0.3,
            )
            questions = _validate_generated_questions(
                result, expected_types, difficulty_counts(total), source_map, point_ids
            )
            for item in questions:
                sources = []
                for source in item["sources"]:
                    known = source_map[source["source_id"]]
                    sources.append(
                        {
                            "source_id": source["source_id"],
                            "excerpt": _canonical_source_excerpt(known["text"], source["excerpt"]),
                            "filename": known["filename"],
                            "material_id": known["material_id"],
                            "version_id": known["version_id"],
                            "locator": known["locator"],
                        }
                    )
                db.session.add(
                    AssessmentQuestion(
                        assessment_id=assessment.id,
                        order_no=item["order_no"],
                        type=item["type"],
                        difficulty=item["difficulty"],
                        stem=item["stem"],
                        options_json=item["options"] or None,
                        correct_answer_json=item["correct_answer"],
                        rubric_json=item["rubric"],
                        max_score=Decimal("1.0"),
                        knowledge_point_id=item["knowledge_point_id"],
                        knowledge_point_name_snapshot=point_ids[item["knowledge_point_id"]],
                        sources_snapshot_json=sources,
                    )
                )
            assessment.status = "ready"
            assessment.error_message = None
            db.session.commit()
        except Exception as error:
            app.logger.exception("Assessment generation failed for %s", assessment_id)
            db.session.rollback()
            assessment = db.session.get(Assessment, assessment_id)
            if assessment is not None and assessment.status == "generating":
                assessment.status = "generation_failed"
                assessment.error_message = str(error)[:1000] or "生成题目失败"
                db.session.commit()
        finally:
            db.session.remove()


def grade_submission_task(app, submission_id: str) -> None:
    with app.app_context():
        submission = db.session.get(AssessmentSubmission, submission_id)
        if submission is None or submission.status != "grading":
            return
        assessment = submission.assessment
        try:
            answer_by_question = {item.question_id: item for item in submission.answers}
            input_questions = []
            fixed_scores = {}
            allowed_sources = {}
            for question in assessment.questions:
                answer = answer_by_question[question.id]
                objective_score = None
                if question.type in {"single_choice", "true_false"}:
                    objective_score = 1 if answer.answer_json == question.correct_answer_json else 0
                    fixed_scores[question.id] = objective_score
                elif answer.answer_json is None or answer.answer_json == "":
                    objective_score = 0
                    fixed_scores[question.id] = 0
                sources = question.sources_snapshot_json or []
                allowed_sources[question.id] = {item["source_id"] for item in sources}
                input_questions.append(
                    {
                        "question_id": question.id,
                        "type": question.type,
                        "stem": question.stem,
                        "correct_answer": question.correct_answer_json,
                        "rubric": question.rubric_json,
                        "sources": sources,
                        "learner_answer": answer.answer_json,
                        "objective_score": objective_score,
                    }
                )
            result = LLMClient().chat_json(
                [
                    {"role": "system", "content": GRADING_SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": json.dumps(
                            {
                                "response_language": language_name(assessment.language),
                                "questions": input_questions,
                            },
                            ensure_ascii=False,
                        ),
                    },
                ],
                temperature=0,
            )
            graded = _validate_grading(result, assessment.questions, fixed_scores, allowed_sources)
            total_score = Decimal("0")
            for question in assessment.questions:
                answer = answer_by_question[question.id]
                item = graded[question.id]
                answer.score = Decimal(str(item["score"]))
                answer.feedback = item["feedback"]
                answer.matched_points_json = item["matched_points"]
                answer.missing_points_json = item["missing_or_incorrect_points"]
                total_score += answer.score
            submission.total_score = total_score
            submission.status = "graded"
            submission.graded_at = utc_now()
            submission.error_message = None
            assessment.status = "graded"
            assessment.error_message = None
            db.session.commit()
        except Exception as error:
            app.logger.exception("Assessment grading failed for %s", submission_id)
            db.session.rollback()
            submission = db.session.get(AssessmentSubmission, submission_id)
            if submission is not None and submission.status == "grading":
                submission.status = "grading_failed"
                submission.error_message = str(error)[:1000] or "评分失败"
                submission.assessment.status = "grading_failed"
                submission.assessment.error_message = submission.error_message
                db.session.commit()
        finally:
            db.session.remove()


def _assessment_documents(assessment: Assessment):
    documents = []
    source_map = {}
    point_ids = {}
    for scope in assessment.materials:
        version = scope.version
        points_by_chunk = {}
        for point in version.knowledge_points:
            point_ids[point.id] = point.name
            for link in point.sources:
                points_by_chunk.setdefault(link.chunk_id, point)
        sources = []
        for chunk in version.chunks:
            point = points_by_chunk.get(chunk.id)
            if point is None:
                continue
            source = {
                "source_id": chunk.id,
                "material_id": version.material_id,
                "version_id": version.id,
                "filename": scope.filename_snapshot,
                "locator": chunk.locator_json or {},
                "text": chunk.text,
                "knowledge_point_id": point.id,
                "knowledge_point_name": point.name,
                "knowledge_point_description": point.description,
            }
            sources.append(source)
            source_map[chunk.id] = source
        documents.append(
            {
                "material_id": version.material_id,
                "version_id": version.id,
                "filename": scope.filename_snapshot,
                "declared_language": version.language,
                "sources": sources,
            }
        )
    return documents, source_map, point_ids


def _validate_generated_questions(result, expected_types, expected_difficulties, source_map, point_ids):
    if not isinstance(result, dict):
        raise AssessmentAIError("出题结果结构无效")
    if result.get("status") == "insufficient_source":
        raise AssessmentAIError(str(result.get("reason") or "资料不足，无法生成题目"))
    questions = result.get("questions")
    total = sum(expected_types.values())
    if result.get("status") != "ok" or not isinstance(questions, list) or len(questions) != total:
        raise AssessmentAIError("模型返回的题目数量不正确")
    if Counter(item.get("type") for item in questions) != Counter(expected_types):
        raise AssessmentAIError("模型返回的题型配比不正确")
    if Counter(item.get("difficulty") for item in questions) != Counter(expected_difficulties):
        raise AssessmentAIError("模型返回的难度配比不正确")
    if sorted(item.get("order_no") for item in questions) != list(range(1, total + 1)):
        raise AssessmentAIError("模型返回的题目顺序无效")
    for item in questions:
        if not isinstance(item.get("stem"), str) or not item["stem"].strip():
            raise AssessmentAIError("题干不能为空")
        if item.get("max_score") != 1 or item.get("knowledge_point_id") not in point_ids:
            raise AssessmentAIError("题目知识点或满分无效")
        options = item.get("options")
        answer = item.get("correct_answer")
        if item["type"] == "single_choice":
            if not isinstance(options, list) or [option.get("id") for option in options] != list("ABCD"):
                raise AssessmentAIError("单选题必须包含 A、B、C、D 四个选项")
            if any(not isinstance(option.get("text"), str) or not option["text"].strip() for option in options):
                raise AssessmentAIError("单选题选项内容不能为空")
            if answer not in set("ABCD") or item.get("rubric") is not None:
                raise AssessmentAIError("单选题答案或评分规则无效")
        elif item["type"] == "true_false":
            if options != [] or type(answer) is not bool or item.get("rubric") is not None:
                raise AssessmentAIError("判断题答案结构无效")
        else:
            rubric = item.get("rubric")
            if options != [] or not isinstance(answer, str) or not answer.strip() or not isinstance(rubric, dict):
                raise AssessmentAIError("简答题答案或评分规则无效")
            if not all(rubric.get(key) for key in ("key_points", "full_credit", "half_credit", "zero_credit")):
                raise AssessmentAIError("简答题评分规则不完整")
            if not isinstance(rubric["key_points"], list) or not all(
                isinstance(point, str) and point.strip() for point in rubric["key_points"]
            ):
                raise AssessmentAIError("简答题评分要点结构无效")
        sources = item.get("sources")
        if not isinstance(sources, list) or not sources:
            raise AssessmentAIError("题目缺少资料依据")
        for source in sources:
            source_id = source.get("source_id") if isinstance(source, dict) else None
            excerpt = source.get("excerpt") if isinstance(source, dict) else None
            if source_id not in source_map or not isinstance(excerpt, str) or not excerpt.strip():
                raise AssessmentAIError("题目资料依据无效")
            if source_map[source_id]["knowledge_point_id"] != item["knowledge_point_id"]:
                raise AssessmentAIError("题目来源与知识点不一致")
    return sorted(questions, key=lambda item: item["order_no"])


def _canonical_source_excerpt(source_text: str, hint: str, limit: int = 1200) -> str:
    """Use the model's text only to locate evidence; always persist real source text."""
    normalized_source = " ".join(source_text.split())
    normalized_hint = " ".join(hint.split())
    if len(normalized_source) <= limit:
        return normalized_source

    exact_at = normalized_source.find(normalized_hint)
    if exact_at >= 0:
        start = max(0, exact_at - max(0, limit - len(normalized_hint)) // 2)
        start = min(start, len(normalized_source) - limit)
        return normalized_source[start : start + limit]

    segments = [
        " ".join(segment.split())
        for segment in re.split(r"(?:\r?\n)+|(?<=[。！？!?；;])", source_text)
        if segment.strip()
    ]
    if not segments:
        return normalized_source[:limit]
    closest = max(segments, key=lambda segment: SequenceMatcher(None, normalized_hint, segment).ratio())
    return closest[:limit]


def _validate_grading(result, questions, fixed_scores, allowed_sources):
    if not isinstance(result, dict) or result.get("status") != "ok":
        reason = result.get("reason") if isinstance(result, dict) else None
        raise AssessmentAIError(str(reason or "模型无法完成评分"))
    results = result.get("results")
    if not isinstance(results, list) or len(results) != len(questions):
        raise AssessmentAIError("评分结果没有覆盖全部题目")
    by_id = {}
    question_by_id = {item.id: item for item in questions}
    for item in results:
        question_id = item.get("question_id") if isinstance(item, dict) else None
        if question_id not in question_by_id or question_id in by_id:
            raise AssessmentAIError("评分结果包含无效或重复题目")
        score = item.get("score")
        if score not in (0, 0.5, 1):
            raise AssessmentAIError("评分结果包含不允许的分值")
        if question_id in fixed_scores and score != fixed_scores[question_id]:
            raise AssessmentAIError("模型改写了后台固定分数")
        if not isinstance(item.get("feedback"), str) or not item["feedback"].strip():
            raise AssessmentAIError("评分结果缺少评语")
        if not isinstance(item.get("matched_points"), list) or not isinstance(
            item.get("missing_or_incorrect_points"), list
        ):
            raise AssessmentAIError("评分要点结构无效")
        source_ids = item.get("source_ids")
        if not isinstance(source_ids, list) or not set(source_ids).issubset(allowed_sources[question_id]):
            raise AssessmentAIError("评分引用包含无效来源")
        by_id[question_id] = item
    return by_id
