import hashlib
import io
import json
import re
from datetime import date
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from flask import Blueprint, jsonify, request, send_file

from ..extensions import db
from ..models import ReportGenerationRequest, WeeklyReport
from ..services.ai_tasks import enqueue_ai_task
from ..services.language import normalize_language
from ..services.report_service import generate_report_task
from .errors import APIError


bp = Blueprint("reports", __name__)


@bp.post("/weekly-reports")
def create_report():
    payload = request.get_json(silent=True) or {}
    try:
        language = normalize_language(payload.get("language"))
    except ValueError as error:
        raise APIError(400, "INVALID_ARGUMENT", "language 必须是 zh-CN 或 en") from error
    request_id = _uuid(payload.get("request_id"))
    period_type = payload.get("period_type")
    if period_type not in {"week", "custom"}:
        raise APIError(400, "INVALID_ARGUMENT", "period_type 必须是 week 或 custom")
    date_from = _date(payload.get("date_from"), "date_from")
    date_to = _date(payload.get("date_to"), "date_to")
    if date_from > date_to:
        raise APIError(400, "INVALID_ARGUMENT", "开始日期不能晚于结束日期")
    timezone_name = payload.get("timezone")
    if not isinstance(timezone_name, str):
        raise APIError(400, "INVALID_ARGUMENT", "timezone 必须是 IANA 时区")
    try:
        ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as error:
        raise APIError(400, "INVALID_ARGUMENT", "timezone 必须是有效的 IANA 时区") from error
    normalized = {
        "period_type": period_type,
        "date_from": date_from.isoformat(),
        "date_to": date_to.isoformat(),
        "timezone": timezone_name,
        "language": language,
    }
    payload_hash = hashlib.sha256(
        json.dumps(normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    existing_request = db.session.get(ReportGenerationRequest, request_id)
    if existing_request:
        if existing_request.payload_hash != payload_hash:
            raise APIError(409, "IDEMPOTENCY_CONFLICT", "同一 request_id 已用于不同参数")
        return jsonify({"data": {"report_id": existing_request.report_id, "status": existing_request.report.status}}), 202

    report = db.session.scalar(
        db.select(WeeklyReport).where(
            WeeklyReport.date_from == date_from,
            WeeklyReport.date_to == date_to,
            WeeklyReport.timezone == timezone_name,
        )
    )
    if report is None:
        report = WeeklyReport(
            period_type=period_type,
            date_from=date_from,
            date_to=date_to,
            timezone=timezone_name,
            language=language,
            status="generating",
        )
        db.session.add(report)
        db.session.flush()
    else:
        report.period_type = period_type
        report.language = language
        report.status = "generating"
        report.error_message = None
    db.session.add(
        ReportGenerationRequest(request_id=request_id, report_id=report.id, payload_hash=payload_hash)
    )
    db.session.commit()
    enqueue_ai_task(generate_report_task, report.id)
    return jsonify({"data": {"report_id": report.id, "status": "generating"}}), 202


@bp.get("/weekly-reports")
def list_reports():
    try:
        page = max(1, int(request.args.get("page", 1)))
        page_size = min(100, max(1, int(request.args.get("page_size", 20))))
    except ValueError as error:
        raise APIError(400, "INVALID_ARGUMENT", "page 和 page_size 必须是整数") from error
    query = db.select(WeeklyReport).where(WeeklyReport.status == "ready").order_by(WeeklyReport.generated_at.desc())
    pagination = db.paginate(query, page=page, per_page=page_size, error_out=False)
    return jsonify(
        {
            "data": {
                "items": [
                    {
                        "id": item.id,
                        "title": (item.content_json or {}).get("title", "学习周报"),
                        "date_from": item.date_from.isoformat(),
                        "date_to": item.date_to.isoformat(),
                        "generated_at": item.generated_at.isoformat() if item.generated_at else None,
                        "language": item.language,
                    }
                    for item in pagination.items
                ],
                "total": pagination.total,
                "page": page,
                "page_size": page_size,
            }
        }
    )


@bp.get("/weekly-reports/<report_id>")
def get_report(report_id: str):
    report = db.session.get(WeeklyReport, report_id)
    if report is None:
        raise APIError(404, "NOT_FOUND", "学习报告不存在")
    return jsonify(
        {
            "data": {
                "report_id": report.id,
                "status": report.status,
                "period_type": report.period_type,
                "date_from": report.date_from.isoformat(),
                "date_to": report.date_to.isoformat(),
                "timezone": report.timezone,
                "language": report.language,
                "data_cutoff_at": report.data_cutoff_at.isoformat() if report.data_cutoff_at else None,
                "generated_at": report.generated_at.isoformat() if report.generated_at else None,
                "statistics": report.statistics_json,
                "content": report.content_json,
                "error_message": report.error_message,
            }
        }
    )


@bp.get("/weekly-reports/<report_id>/download")
def download_report(report_id: str):
    report = db.session.get(WeeklyReport, report_id)
    if report is None:
        raise APIError(404, "NOT_FOUND", "学习报告不存在")
    if report.status != "ready" or not report.statistics_json or not report.content_json:
        raise APIError(409, "REPORT_NOT_READY", "学习报告尚未生成完成")
    markdown = _report_markdown(report)
    filename = f"学习报告_{report.date_from.isoformat()}_{report.date_to.isoformat()}.md"
    return send_file(
        io.BytesIO(markdown.encode("utf-8")),
        mimetype="text/markdown; charset=utf-8",
        as_attachment=True,
        download_name=filename,
        max_age=0,
    )


def _uuid(value) -> str:
    if not isinstance(value, str):
        raise APIError(400, "INVALID_ARGUMENT", "request_id 必须是 UUID")
    try:
        return str(UUID(value))
    except ValueError as error:
        raise APIError(400, "INVALID_ARGUMENT", "request_id 必须是 UUID") from error


def _date(value, field: str) -> date:
    if not isinstance(value, str):
        raise APIError(400, "INVALID_ARGUMENT", f"{field} 必须是 YYYY-MM-DD")
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise APIError(400, "INVALID_ARGUMENT", f"{field} 必须是 YYYY-MM-DD") from error


def _report_markdown(report: WeeklyReport) -> str:
    content = report.content_json
    statistics = report.statistics_json
    english = report.language == "en"
    learned = _markdown_list(
        content["learned"], "No learning content is available to summarize." if english else "暂无可归纳的学习内容。"
    )
    weak_points = _markdown_list(
        content["weak_points"],
        "There is not enough assessment data to identify weak knowledge points." if english else "暂无足够的测评数据判断薄弱知识点。",
    )
    suggestions = _markdown_list(
        content["next_week_suggestions"],
        "Keep building your learning and assessment history." if english else "继续积累学习和测评记录。",
        ordered=True,
    )
    average = statistics["average_score_rate"]
    average_text = "—" if average is None else f"{average:g}%"
    labels = (
        {
            "period": "Period",
            "timezone": "Time zone",
            "cutoff": "Data through",
            "statistics": "Learning data",
            "uploads": "Materials uploaded / replaced",
            "questions": "Questions asked",
            "assessments": "Assessments completed",
            "average": "Average score rate",
            "summary": "Learning during this period",
            "learned": "What you learned",
            "weak": "Weak knowledge points",
            "suggestions": "Next steps",
            "generated": "Generated",
            "empty_summary": "There is no learning activity for this period.",
        }
        if english
        else {
            "period": "统计时间", "timezone": "时区", "cutoff": "数据截止", "statistics": "学习数据",
            "uploads": "上传／覆盖资料", "questions": "提出问题", "assessments": "完成测评",
            "average": "平均得分率", "summary": "这段时间的学习", "learned": "学到了什么",
            "weak": "薄弱知识点", "suggestions": "下一步建议", "generated": "生成时间",
            "empty_summary": "这段时间暂无学习记录。",
        }
    )
    return "\n".join(
        [
            f"# {_escape_markdown(content['title']).replace(chr(10), ' ')}",
            "",
            f"> {labels['period']}: {report.date_from.isoformat()}—{report.date_to.isoformat()}  ",
            f"> {labels['timezone']}: {_escape_markdown(report.timezone)}  ",
            f"> {labels['cutoff']}: {report.data_cutoff_at.isoformat()}",
            "",
            f"## {labels['statistics']}",
            "",
            f"- {labels['uploads']}: {statistics['uploads_count']}",
            f"- {labels['questions']}: {statistics['questions_count']}",
            f"- {labels['assessments']}: {statistics['assessments_count']}",
            f"- {labels['average']}: {average_text}",
            "",
            f"## {labels['summary']}",
            "",
            _escape_markdown(content["summary"] or labels["empty_summary"]),
            "",
            f"## {labels['learned']}",
            "",
            learned,
            "",
            f"## {labels['weak']}",
            "",
            weak_points,
            "",
            f"## {labels['suggestions']}",
            "",
            suggestions,
            "",
            f"{labels['generated']}: {report.generated_at.isoformat()}",
            "",
        ]
    )


def _escape_markdown(value: str) -> str:
    escaped = str(value).replace("\\", "\\\\").replace("<", "&lt;").replace(">", "&gt;")
    return re.sub(r"([`*_{}\[\]()#+\-.!|])", r"\\\1", escaped)


def _markdown_list(values: list[str], empty_text: str, ordered: bool = False) -> str:
    items = values or [empty_text]
    lines = []
    for index, item in enumerate(items, start=1):
        prefix = f"{index}." if ordered else "-"
        text = _escape_markdown(item).replace("\r\n", "\n").replace("\r", "\n")
        lines.append(f"{prefix} {text.replace(chr(10), '  ' + chr(10) + '  ')}")
    return "\n".join(lines)
