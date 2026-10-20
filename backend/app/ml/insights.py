"""Explainable analytics built on top of the raw data and model output:
weak subjects/topics, attendance analysis, growth timelines, study plans, AI reports
and teacher interventions. Every generator works without an LLM; when Gemini/OpenAI
is configured the text is enriched by the LLM."""
import json
from collections import Counter, defaultdict
from datetime import date, timedelta

from . import llm
from .features import Lookup, StudentData, attendance_rate, exam_series, homework_stats

DEFAULT_BANDS = [
    {"grade": "A+", "min": 90, "gpa": 10},
    {"grade": "A", "min": 80, "gpa": 9},
    {"grade": "B+", "min": 70, "gpa": 8},
    {"grade": "B", "min": 60, "gpa": 7},
    {"grade": "C", "min": 50, "gpa": 6},
    {"grade": "D", "min": 40, "gpa": 5},
    {"grade": "F", "min": 0, "gpa": 0},
]
WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def grade_for(pct: float | None, bands: list[dict] | None) -> str | None:
    if pct is None:
        return None
    for b in sorted(bands or DEFAULT_BANDS, key=lambda b: -float(b.get("min", 0))):
        if pct >= float(b.get("min", 0)):
            return str(b.get("grade"))
    return None


def subject_performance(sd: StudentData, lk: Lookup, bands, class_avgs: dict[int, float] | None = None) -> list[dict]:
    per_subject: dict[int, dict[int, list[float]]] = defaultdict(lambda: defaultdict(lambda: [0.0, 0.0]))
    for m in sd.marks:
        per_subject[m.subject_id][m.exam_id][0] += m.score
        per_subject[m.subject_id][m.exam_id][1] += m.max_score
    out = []
    for sid, exams in per_subject.items():
        subj = lk.subjects.get(sid)
        if subj is None:
            continue
        total = sum(v[0] for v in exams.values())
        mx = sum(v[1] for v in exams.values())
        if mx <= 0:
            continue
        pct = total / mx * 100
        ordered = sorted(exams.items(), key=lambda kv: (lk.exams[kv[0]].date or date.min) if kv[0] in lk.exams else date.min)
        series = [round(v[0] / v[1] * 100, 1) for _, v in ordered if v[1] > 0]
        change = round(series[-1] - series[0], 1) if len(series) >= 2 else 0.0
        if pct < subj.pass_mark:
            status = "failing"
        elif pct < 60:
            status = "weak"
        elif pct < 75:
            status = "average"
        else:
            status = "strong"
        out.append({
            "subject_id": sid,
            "subject": subj.name,
            "pct": round(pct, 1),
            "grade": grade_for(pct, bands),
            "status": status,
            "change": change,
            "series": series,
            "class_avg": round(class_avgs[sid], 1) if class_avgs and sid in class_avgs else None,
            "pass_mark": subj.pass_mark,
        })
    out.sort(key=lambda r: r["pct"])
    return out


def weak_topics(sd: StudentData, lk: Lookup, threshold: float = 65.0, limit: int = 8) -> list[dict]:
    agg: dict[int, list[float]] = defaultdict(lambda: [0.0, 0.0, 0])
    for m in sd.marks:
        if m.topic_id is None:
            continue
        agg[m.topic_id][0] += m.score
        agg[m.topic_id][1] += m.max_score
        agg[m.topic_id][2] += 1
    rows = []
    for tid, (score, mx, n) in agg.items():
        topic = lk.topics.get(tid)
        if topic is None or mx <= 0:
            continue
        pct = score / mx * 100
        if pct < threshold:
            subj = lk.subjects.get(topic.subject_id)
            rows.append({
                "topic_id": tid,
                "topic": topic.name,
                "subject_id": topic.subject_id,
                "subject": subj.name if subj else "",
                "pct": round(pct, 1),
                "assessments": n,
                "severity": "high" if pct < 45 else "medium" if pct < 55 else "low",
            })
    rows.sort(key=lambda r: r["pct"])
    return rows[:limit]


def attendance_analysis(sd: StudentData) -> dict:
    recs = sd.attendance
    counts = Counter(r.status for r in recs)
    rate = attendance_rate(recs)
    weekday_abs = Counter(WEEKDAYS[r.date.weekday()] for r in recs if r.status == "absent")
    monthly: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for r in recs:
        if r.status == "excused":
            continue
        key = r.date.strftime("%Y-%m")
        monthly[key][1] += 1
        if r.status in ("present", "late"):
            monthly[key][0] += 1
    longest = cur = 0
    for r in recs:
        cur = cur + 1 if r.status == "absent" else 0
        longest = max(longest, cur)
    cutoff = date.today() - timedelta(days=21)
    recent = [r for r in recs if r.date >= cutoff]
    recent_rate = attendance_rate(recent)

    notes = []
    if rate is not None:
        if rate < 0.75:
            notes.append("Attendance is below the 75% minimum; this strongly predicts lower exam scores.")
        if recent_rate is not None and rate - recent_rate > 0.08:
            notes.append(f"Attendance dropped to {recent_rate*100:.0f}% over the last 3 weeks.")
        if weekday_abs and sum(weekday_abs.values()) >= 3:
            day, n = weekday_abs.most_common(1)[0]
            if n / sum(weekday_abs.values()) >= 0.4:
                notes.append(f"{n} of {sum(weekday_abs.values())} absences fall on a {day} - check for a recurring cause.")
        if longest >= 3:
            notes.append(f"Longest consecutive absence: {longest} school days.")
        if counts.get("late", 0) >= 5:
            notes.append(f"Late {counts['late']} times - punctuality may be affecting first-period learning.")
        if not notes:
            notes.append("Attendance is healthy and consistent.")
    return {
        "rate": round(rate * 100, 1) if rate is not None else None,
        "recent_rate": round(recent_rate * 100, 1) if recent_rate is not None else None,
        "total_days": len(recs),
        "present": counts.get("present", 0),
        "absent": counts.get("absent", 0),
        "late": counts.get("late", 0),
        "excused": counts.get("excused", 0),
        "longest_absence_streak": longest,
        "weekday_absences": [{"day": d, "count": weekday_abs.get(d, 0)} for d in WEEKDAYS[:6]],
        "monthly": [{"month": k, "rate": round(v[0] / v[1] * 100, 1)} for k, v in sorted(monthly.items()) if v[1]],
        "notes": notes,
    }


def growth_timeline(sd: StudentData, lk: Lookup) -> list[dict]:
    per: dict[int, dict[str, list[float]]] = defaultdict(lambda: defaultdict(lambda: [0.0, 0.0]))
    for m in sd.marks:
        subj = lk.subjects.get(m.subject_id)
        name = subj.name if subj else str(m.subject_id)
        per[m.exam_id][name][0] += m.score
        per[m.exam_id][name][1] += m.max_score
    rows = []
    for item in exam_series(sd, lk):
        row = {"exam": item["exam"], "date": item["date"].isoformat() if item["date"] else None, "overall": item["pct"]}
        for name, (s, mx) in per[item["exam_id"]].items():
            if mx:
                row[name] = round(s / mx * 100, 1)
        rows.append(row)
    return rows


def homework_summary(sd: StudentData) -> dict:
    completion, avg, done, due = homework_stats(sd)
    pending = []
    today = date.today()
    for a in sorted(sd.assignments, key=lambda a: a.due_date or date.max):
        sub = sd.submissions.get(a.id)
        status = sub.status if sub else ("missing" if a.due_date and a.due_date < today else "pending")
        pending.append({
            "assignment_id": a.id,
            "title": a.title,
            "due_date": a.due_date.isoformat() if a.due_date else None,
            "status": status,
            "score": sub.score if sub else None,
            "max_score": a.max_score,
            "subject_id": a.subject_id,
            "feedback": sub.feedback if sub else None,
        })
    return {
        "completion_rate": round(completion * 100, 1) if completion is not None else None,
        "average_score": round(avg * 100, 1) if avg is not None else None,
        "completed": done,
        "due": due,
        "items": pending,
    }


# ---------------------------------------------------------------------------
# Study plan
# ---------------------------------------------------------------------------

ACTIVITIES = [
    ("Concept review", "Re-read notes and watch one short explainer video, then summarise the idea in 5 lines."),
    ("Guided practice", "Solve 6-8 textbook examples, checking each step against the worked solution."),
    ("Timed problem set", "Attempt 10 mixed questions in 25 minutes without notes, then mark them."),
    ("Active recall quiz", "Write everything you remember on a blank page, then fill gaps from notes."),
    ("Error-log review", "Revisit every mistake from recent tests and redo them correctly."),
]


def study_plan(name: str, features: dict, prediction: dict, subjects: list[dict], topics: list[dict]) -> dict:
    risk = prediction.get("risk_level", "low")
    weekly_minutes = {"high": 540, "medium": 420, "low": 300}[risk]

    focus = [
        {"subject": t["subject"], "topic": t["topic"], "current": t["pct"], "target": min(100, round(max(t["pct"] + 15, 65))),
         "weight": max(5.0, 75 - t["pct"])}
        for t in topics[:6]
    ]
    covered = {f["subject"] for f in focus}
    for s in subjects:
        if s["status"] in ("failing", "weak") and s["subject"] not in covered:
            focus.append({"subject": s["subject"], "topic": "Core fundamentals", "current": s["pct"],
                          "target": min(100, round(max(s["pct"] + 12, 60))), "weight": max(5.0, 75 - s["pct"])})
    if not focus:  # strong student: stretch goals on lowest subjects
        for s in subjects[:3]:
            focus.append({"subject": s["subject"], "topic": "Advanced problems & enrichment", "current": s["pct"],
                          "target": min(100, round(s["pct"] + 5)), "weight": 5.0})

    total_w = sum(f["weight"] for f in focus) or 1
    for f in focus:
        f["weekly_minutes"] = int(round(weekly_minutes * f["weight"] / total_w / 15) * 15) or 15
        f["priority"] = "high" if f["current"] < 45 else "medium" if f["current"] < 60 else "low"

    # Distribute sessions over the week (weighted round-robin).
    slots = {"Mon": 2, "Tue": 2, "Wed": 2, "Thu": 2, "Fri": 1, "Sat": 3, "Sun": 1}
    remaining = {i: f["weekly_minutes"] for i, f in enumerate(focus)}
    schedule = []
    act_i = 0
    for day, n in slots.items():
        sessions = []
        for _ in range(n):
            if not remaining or max(remaining.values()) <= 0:
                break
            idx = max(remaining, key=lambda k: remaining[k])
            minutes = min(45, max(15, remaining[idx]))
            remaining[idx] -= minutes
            if day == "Sun":
                activity, how = "Weekly review", "Redo the hardest question from each session this week and update your error log."
            else:
                activity, how = ACTIVITIES[act_i % len(ACTIVITIES)]
                act_i += 1
            sessions.append({"subject": focus[idx]["subject"], "topic": focus[idx]["topic"], "minutes": minutes,
                             "activity": activity, "how": how})
        schedule.append({"day": day, "sessions": sessions})

    tips = []
    if features["attendance_rate"] < 0.85:
        tips.append("Attend every class this month - each missed lesson makes the next topic harder to follow.")
    if features["assignment_completion"] < 0.8:
        tips.append("Finish homework the same day it is set; start with a 10-minute timer to beat procrastination.")
    if features["trend"] <= -2:
        tips.append("Your scores have been falling - meet your teacher this week to pinpoint what changed.")
    if features["score_std"] > 12:
        tips.append("Results swing between exams: revise little and often rather than cramming the night before.")
    tips.append("Use spaced repetition: revisit each topic 1, 3 and 7 days after studying it.")
    tips.append("Sleep 8+ hours before tests - memory consolidation happens during sleep.")

    plan = {
        "student": name,
        "risk_level": risk,
        "weekly_hours": round(weekly_minutes / 60, 1),
        "focus_areas": focus,
        "schedule": schedule,
        "tips": tips,
        "motivation": None,
        "generated_by": "rules",
    }

    if llm.provider():
        enriched = llm.generate_json(
            "Create personalised study tips for this student as JSON with keys "
            '"tips" (list of 4-6 short, specific, actionable strings) and "motivation" (one encouraging sentence).\n'
            f"Student: {name}\nFocus areas: {json.dumps(focus)}\nFeatures: {json.dumps({k: v for k, v in features.items() if not k.startswith('_')})}",
            system="You are an expert learning coach for school students. Be concrete and kind.",
        )
        if enriched and isinstance(enriched.get("tips"), list):
            plan["tips"] = [str(t) for t in enriched["tips"]][:6]
            plan["motivation"] = enriched.get("motivation")
            plan["generated_by"] = llm.provider()
    if not plan["motivation"]:
        plan["motivation"] = (
            "Small, steady sessions beat last-minute cramming - stick to this plan for 3 weeks and you'll see the difference."
            if risk != "low" else "You're doing well - keep the momentum and push into stretch goals."
        )
    return plan


# ---------------------------------------------------------------------------
# AI report
# ---------------------------------------------------------------------------

def ai_report(name: str, features: dict, prediction: dict, subjects: list[dict], topics: list[dict],
              attendance: dict, homework: dict) -> dict:
    strong = [s for s in subjects if s["status"] == "strong"]
    weak = [s for s in subjects if s["status"] in ("weak", "failing")]
    strengths = [f"{s['subject']}: {s['pct']}% ({s['grade']})" for s in sorted(strong, key=lambda s: -s["pct"])[:3]]
    strengths += [r["text"] for r in prediction["reasons"] if r["type"] == "positive"]
    concerns = [f"{s['subject']}: {s['pct']}% - {s['status']}" for s in weak]
    concerns += [r["text"] for r in prediction["reasons"] if r["type"] == "negative" and r["factor"] != "subjects"]
    recs = []
    if topics:
        recs.append("Prioritise these topics: " + ", ".join(f"{t['topic']} ({t['subject']})" for t in topics[:4]) + ".")
    if features["attendance_rate"] < 0.85:
        recs.append("Improve attendance to at least 90% - set up a parent-teacher check-in.")
    if features["assignment_completion"] < 0.8:
        recs.append("Build a daily homework routine; teachers to share weekly homework status with parents.")
    if features["trend"] <= -2:
        recs.append("Schedule a one-to-one review to diagnose the recent decline.")
    if prediction["risk_level"] == "high":
        recs.append("Enrol in remedial sessions and review progress every two weeks.")
    if not recs:
        recs.append("Maintain current routine and add enrichment work in the strongest subjects.")

    trend_word = "improving" if features["trend"] >= 1.5 else "declining" if features["trend"] <= -1.5 else "stable"
    summary = (
        f"{name} has an overall average of {features['avg_pct']:.1f}% with a {trend_word} trend "
        f"and {attendance.get('rate') or 0:.0f}% attendance. The model predicts {prediction['predicted_score']:.0f}% "
        f"in the next exam with a {prediction['risk_level']} failure risk ({prediction['risk_probability']*100:.0f}%)."
    )
    outlook = {
        "high": "Without intervention, there is a significant chance of failing at least one subject. Early, targeted support can reverse this.",
        "medium": "Progress is fragile. Addressing the concerns above over the next 3-4 weeks should move the student into the safe zone.",
        "low": "The student is on track. Focus on consistency and stretching into higher-order problems.",
    }[prediction["risk_level"]]

    report = {"summary": summary, "strengths": strengths or ["Consistent effort recorded."], "concerns": concerns or ["No major concerns detected."],
              "recommendations": recs, "outlook": outlook, "generated_by": "rules"}

    if llm.provider():
        ctx = {
            "student": name, "features": {k: v for k, v in features.items() if not k.startswith("_")},
            "prediction": prediction, "subjects": subjects, "weak_topics": topics,
            "attendance": {k: attendance[k] for k in ("rate", "recent_rate", "absent", "late", "notes")},
            "homework": {k: homework[k] for k in ("completion_rate", "average_score")},
        }
        enriched = llm.generate_json(
            "Write a concise student progress report as JSON with keys: summary (2-3 sentences), strengths (list), "
            "concerns (list), recommendations (list of specific actions for student, teacher and parent), outlook (1-2 sentences). "
            f"Data:\n{json.dumps(ctx, default=str)}",
            system="You are an experienced school academic counsellor writing for parents and teachers. Be specific, balanced and evidence-based.",
        )
        if enriched and enriched.get("summary"):
            for k in ("summary", "strengths", "concerns", "recommendations", "outlook"):
                if enriched.get(k):
                    report[k] = enriched[k]
            report["generated_by"] = llm.provider()
    return report


# ---------------------------------------------------------------------------
# Teacher assistant
# ---------------------------------------------------------------------------

def suggest_interventions(ctx: dict) -> list[dict]:
    out = []
    for s in ctx.get("at_risk", [])[:8]:
        factors = {r["factor"] for r in s["reasons"] if r["type"] == "negative"}
        actions = []
        if "attendance" in factors:
            actions.append("Call parents about attendance; agree a daily check-in")
        if "trend" in factors:
            actions.append("One-to-one conference to diagnose the recent decline")
        if "homework" in factors:
            actions.append("Enrol in supervised homework club; send weekly homework report home")
        if "subjects" in factors or "recent" in factors:
            actions.append("Remedial small-group sessions twice a week on failing subjects")
        if "consistency" in factors:
            actions.append("Weekly low-stakes quizzes to build steady revision habits")
        if not actions:
            actions.append("Monitor closely and check in weekly")
        out.append({"target": s["name"], "student_id": s["student_id"], "priority": s["risk_level"], "actions": actions})
    for t in ctx.get("class_weak_topics", [])[:3]:
        if t["share_weak"] >= 0.35:
            out.append({"target": f"Whole class - {t['topic']} ({t['subject']})", "priority": "medium",
                        "actions": [f"{t['share_weak']*100:.0f}% of students are weak here (avg {t['avg_pct']}%). Re-teach with a different approach "
                                    "(worked examples + peer instruction) and follow with an exit ticket."]})
    if ctx.get("top_performers"):
        out.append({"target": "Top performers", "priority": "low",
                    "actions": ["Offer enrichment problems and invite them to act as peer tutors: " + ", ".join(ctx["top_performers"][:3])]})
    return out


def assistant_reply(message: str, ctx: dict, history: list[dict] | None = None) -> dict:
    interventions = suggest_interventions(ctx)
    if llm.provider():
        text = llm.generate(
            f"Context (JSON):\n{json.dumps(ctx, default=str)}\n\nTeacher's question: {message}",
            system=(
                "You are EduPulse AI Teacher Assistant. Using ONLY the provided class/student data, answer the teacher's "
                "question, identify problems and recommend concrete, prioritised classroom interventions. Use short "
                "markdown with headings and bullet points. Name students where relevant. Do not invent data."
            ),
            history=history,
        )
        if text:
            return {"reply": text, "interventions": interventions, "generated_by": llm.provider()}

    lines = [f"### {ctx.get('scope', 'Overview')}"]
    s = ctx.get("summary", {})
    if s:
        lines.append(
            f"- **{s.get('students', 0)} students**, average score **{s.get('avg_pct', 0)}%**, "
            f"attendance **{s.get('attendance', 0)}%**, homework completion **{s.get('homework', 0)}%**."
        )
    msg = message.lower()
    at_risk = ctx.get("at_risk", [])
    if at_risk:
        lines.append("\n### Students needing attention")
        for st in at_risk[:6]:
            why = "; ".join(r["text"] for r in st["reasons"] if r["type"] == "negative")[:180] or "multiple small concerns"
            lines.append(f"- **{st['name']}** - {st['risk_level']} risk, predicted {st['predicted_score']}% ({why})")
    else:
        lines.append("\nNo students are currently flagged as at-risk.")
    if ctx.get("class_weak_topics") and any(k in msg for k in ("topic", "weak", "class", "teach", "subject", "plan", "")):
        lines.append("\n### Topics the class struggles with")
        for t in ctx["class_weak_topics"][:5]:
            lines.append(f"- {t['topic']} ({t['subject']}): average {t['avg_pct']}%, {t['share_weak']*100:.0f}% of students weak")
    if ctx.get("student"):
        st = ctx["student"]
        lines.append(f"\n### Focus: {st['name']}")
        lines.append(f"- Predicted next score {st['predicted_score']}%, risk {st['risk_level']}.")
        for t in st.get("weak_topics", [])[:4]:
            lines.append(f"- Weak topic: {t['topic']} ({t['subject']}) at {t['pct']}%")
    if interventions:
        lines.append("\n### Recommended interventions")
        for iv in interventions[:8]:
            lines.append(f"- **{iv['target']}** ({iv['priority']}): " + "; ".join(iv["actions"]))
    lines.append("\n_Rule-based analysis. Configure GEMINI_API_KEY or OPENAI_API_KEY for conversational answers._")
    return {"reply": "\n".join(lines), "interventions": interventions, "generated_by": "rules"}
