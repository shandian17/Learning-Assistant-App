import json
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from ..extensions import db
from ..models import (
    AssessmentSubmission,
    ChatMessage,
    MaterialVersion,
    WeeklyReport,
)
from ..models.base import utc_now
from .ai_prompts import CHAT_SYSTEM_PROMPT
from .language import language_name
from .llm_client import LLMClient


class ReportAIError(RuntimeError):
    pass


def generate_report_task(app, report_id: str) -> None:
    with app.app_context():
        report = db.session.get(WeeklyReport, report_id)
        if report is None or report.status != "generating":
            return
        try:
            zone = ZoneInfo(report.timezone)
            start = datetime.combine(report.date_from, time.min, zone).astimezone(timezone.utc)
            end = datetime.combine(report.date_to + timedelta(days=1), time.min, zone).astimezone(timezone.utc)
            cutoff = utc_now()
            versions = list(
                db.session.scalars(
                    db.select(MaterialVersion)
                    .where(
                        MaterialVersion.status == "ready",
                        MaterialVersion.uploaded_at >= start,
                        MaterialVersion.uploaded_at < end,
                        MaterialVersion.uploaded_at <= cutoff,
                    )
                    .order_by(MaterialVersion.uploaded_at)
                )
            )
            messages = list(
                db.session.scalars(
                    db.select(ChatMessage)
                    .where(
                        ChatMessage.role == "user",
                        ChatMessage.created_at >= start,
                        ChatMessage.created_at < end,
                        ChatMessage.created_at <= cutoff,
                    )
                    .order_by(ChatMessage.created_at)
                )
            )
            submissions = list(
                db.session.scalars(
                    db.select(AssessmentSubmission)
                    .where(
                        AssessmentSubmission.status == "graded",
                        AssessmentSubmission.graded_at >= start,
                        AssessmentSubmission.graded_at < end,
                        AssessmentSubmission.graded_at <= cutoff,
                    )
                    .order_by(AssessmentSubmission.graded_at)
                )
            )
            rates = [float(item.total_score / item.max_score * 100) for item in submissions]
            statistics = {
                "uploads_count": len(versions),
                "questions_count": len(messages),
                "assessments_count": len(submissions),
                "average_score_rate": round(sum(rates) / len(rates), 1) if rates else None,
            }
            assessments = []
            weak_points = []
            for submission in submissions:
                scope = [item.filename_snapshot for item in submission.assessment.materials]
                assessments.append(
                    {
                        "graded_at": submission.graded_at.isoformat(),
                        "materials": scope,
                        "score": float(submission.total_score),
                        "max_score": float(submission.max_score),
                        "score_rate": round(float(submission.total_score / submission.max_score * 100), 1),
                    }
                )
                for answer in submission.answers:
                    if answer.score is not None and answer.score < 1:
                        weak_points.append(
                            {
                                "knowledge_point": answer.question.knowledge_point_name_snapshot,
                                "score": float(answer.score),
                                "feedback": answer.feedback,
                            }
                        )
            task = (
                "Generate a learning report from these records. Return valid JSON only with exactly these fields: title, summary, learned, weak_points, and next_week_suggestions. The first two are strings and the last three are string arrays. Do not return HTML. When there is no activity, state that there was no learning activity during this period. When there are questions but no assessments, do not infer mastery."
                if report.language == "en"
                else "根据以下学习记录生成周报。只返回合法 JSON，字段必须为 title、summary、learned、weak_points、next_week_suggestions；前两项为字符串，后三项为字符串数组，不返回 HTML。没有记录时明确写这段时间暂无学习记录；有提问但没有测评时不要推断掌握程度。"
            )
            model_input = {
                "response_language": language_name(report.language),
                "task": task,
                "period": {"date_from": report.date_from.isoformat(), "date_to": report.date_to.isoformat()},
                "statistics": statistics,
                "uploaded_materials": [item.original_filename for item in versions],
                "learner_questions": [item.content for item in messages],
                "assessments": assessments,
                "weak_point_records": weak_points,
            }
            result = LLMClient().chat_json(
                [
                    {"role": "system", "content": CHAT_SYSTEM_PROMPT},
                    {"role": "user", "content": json.dumps(model_input, ensure_ascii=False)},
                ],
                temperature=0.2,
            )
            content = _validate_report_content(result)
            report.statistics_json = statistics
            report.content_json = content
            report.data_cutoff_at = cutoff
            report.generated_at = utc_now()
            report.error_message = None
            report.status = "ready"
            db.session.commit()
        except Exception as error:
            app.logger.exception("Weekly report generation failed for %s", report_id)
            db.session.rollback()
            report = db.session.get(WeeklyReport, report_id)
            if report is not None and report.status == "generating":
                report.status = "generation_failed"
                report.error_message = str(error)[:1000] or "生成周报失败"
                db.session.commit()
        finally:
            db.session.remove()


def _validate_report_content(result) -> dict:
    if not isinstance(result, dict):
        raise ReportAIError("周报内容结构无效")
    title = result.get("title")
    summary = result.get("summary")
    if not isinstance(title, str) or not title.strip() or not isinstance(summary, str):
        raise ReportAIError("周报标题或总结无效")
    content = {"title": title.strip(), "summary": summary.strip()}
    for key in ("learned", "weak_points", "next_week_suggestions"):
        value = result.get(key)
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            raise ReportAIError("周报列表结构无效")
        content[key] = [item.strip() for item in value if item.strip()]
    return content
