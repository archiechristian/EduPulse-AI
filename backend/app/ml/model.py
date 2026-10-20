"""Performance prediction + failure-risk models.

Two gradient-boosted tree models (scikit-learn) are trained on a blend of:
  * a synthetic cohort that encodes well-known educational relationships
    (attendance, homework completion and trajectory drive outcomes), and
  * the institution's real history: for every student with 2+ exams we use the
    data *before* their latest exam as features and the latest exam as the target.
As the institution accumulates data, real samples (weighted x3) dominate.
XGBoost can be swapped in by replacing the estimator classes; the API is identical.
"""
import threading
from datetime import datetime

import joblib
import numpy as np
from sklearn.ensemble import GradientBoostingClassifier, GradientBoostingRegressor
from sklearn.metrics import mean_absolute_error, r2_score, roc_auc_score
from sklearn.model_selection import train_test_split
from sqlalchemy.orm import Session

from ..config import BASE_DIR
from ..models import Student
from .features import FEATURE_LABELS, FEATURES, compute_features, exam_series, feature_vector, load_lookup, load_student_data

ARTIFACT = BASE_DIR / "app" / "ml" / "artifacts" / "models.joblib"
FAIL_THRESHOLD = 40.0
_lock = threading.Lock()
_bundle: dict | None = None


def synthetic_cohort(n: int = 4000, seed: int = 42) -> tuple[np.ndarray, np.ndarray]:
    rng = np.random.default_rng(seed)
    ability = rng.normal(0, 1, n)
    attendance = np.clip(0.86 + 0.06 * ability + rng.normal(0, 0.08, n), 0.3, 1.0)
    completion = np.clip(0.8 + 0.1 * ability + 0.3 * (attendance - 0.86) + rng.normal(0, 0.12, n), 0, 1)
    avg = np.clip(62 + 14 * ability + rng.normal(0, 5, n), 5, 99)
    trend = rng.normal(0, 3, n) + 6 * (completion - 0.8) + 10 * (attendance - 0.86)
    recent = np.clip(avg + trend * 1.5 + rng.normal(0, 4, n), 0, 100)
    hw_avg = np.clip(avg / 100 + rng.normal(0, 0.1, n), 0, 1)
    std = np.abs(rng.normal(5, 3, n)) + np.abs(trend)
    fails = np.clip(rng.poisson(np.maximum(0, (48 - avg) / 7) + 0.05), 0, 6)
    X = np.column_stack([avg, recent, trend, attendance, completion, hw_avg, fails, std])
    y = (
        12.5 + 0.45 * recent + 0.35 * avg + 2.2 * trend + 25 * (attendance - 0.86)
        + 10 * (completion - 0.8) + 8 * (hw_avg - 0.62) - 1.5 * fails + rng.normal(0, 5, n)
    )
    return X, np.clip(y, 0, 100)


def real_samples(db: Session) -> tuple[np.ndarray, np.ndarray]:
    students = db.query(Student).all()
    lk = load_lookup(db)
    data = load_student_data(db, students)
    X, y = [], []
    for sd in data.values():
        series = exam_series(sd, lk)
        if len(series) < 2:
            continue
        last = series[-1]["exam_id"]
        history = [m for m in sd.marks if m.exam_id != last]
        X.append(feature_vector(compute_features(sd, lk, marks=history)))
        y.append(series[-1]["pct"])
    return np.array(X).reshape(-1, len(FEATURES)), np.array(y)


def train(db: Session | None = None) -> dict:
    Xs, ys = synthetic_cohort()
    Xr, yr = (real_samples(db) if db is not None else (np.empty((0, len(FEATURES))), np.empty(0)))
    X = np.vstack([Xs, Xr]) if len(Xr) else Xs
    y = np.concatenate([ys, yr]) if len(yr) else ys
    w = np.concatenate([np.ones(len(ys)), np.full(len(yr), 3.0)]) if len(yr) else np.ones(len(ys))
    labels = (y < FAIL_THRESHOLD).astype(int)

    X_tr, X_te, y_tr, y_te, w_tr, _, l_tr, l_te = train_test_split(X, y, w, labels, test_size=0.2, random_state=7, stratify=labels)
    reg = GradientBoostingRegressor(n_estimators=250, max_depth=3, learning_rate=0.05, subsample=0.9, random_state=7)
    reg.fit(X_tr, y_tr, sample_weight=w_tr)
    clf = GradientBoostingClassifier(n_estimators=250, max_depth=3, learning_rate=0.05, subsample=0.9, random_state=7)
    clf.fit(X_tr, l_tr, sample_weight=w_tr)

    pred = reg.predict(X_te)
    proba = clf.predict_proba(X_te)[:, 1]
    importances = {k: round(float(v), 4) for k, v in zip(FEATURES, reg.feature_importances_)}
    bundle = {
        "regressor": reg,
        "classifier": clf,
        "meta": {
            "trained_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
            "algorithm": "Gradient Boosted Trees (scikit-learn)",
            "synthetic_samples": int(len(ys)),
            "real_samples": int(len(yr)),
            "metrics": {
                "mae": round(float(mean_absolute_error(y_te, pred)), 2),
                "r2": round(float(r2_score(y_te, pred)), 3),
                "risk_auc": round(float(roc_auc_score(l_te, proba)), 3) if len(set(l_te)) > 1 else None,
            },
            "feature_importance": [
                {"feature": k, "label": FEATURE_LABELS[k], "importance": v}
                for k, v in sorted(importances.items(), key=lambda kv: -kv[1])
            ],
        },
    }
    ARTIFACT.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(bundle, ARTIFACT)
    global _bundle
    with _lock:
        _bundle = bundle
    return bundle["meta"]


def get_bundle() -> dict:
    global _bundle
    with _lock:
        if _bundle is not None:
            return _bundle
    if ARTIFACT.exists():
        try:
            loaded = joblib.load(ARTIFACT)
            with _lock:
                _bundle = loaded
            return loaded
        except Exception:  # noqa: BLE001 - corrupted/incompatible artifact -> retrain
            pass
    train(None)
    return _bundle  # type: ignore[return-value]


def risk_level(prob: float) -> str:
    if prob >= 0.55:
        return "high"
    if prob >= 0.25:
        return "medium"
    return "low"


def predict(features: dict) -> dict:
    bundle = get_bundle()
    x = np.array([feature_vector(features)])
    predicted = float(np.clip(bundle["regressor"].predict(x)[0], 0, 100))
    prob = float(bundle["classifier"].predict_proba(x)[0, 1])
    return {
        "predicted_score": round(predicted, 1),
        "risk_probability": round(prob, 3),
        "risk_level": risk_level(prob),
        "reasons": explain(features),
        "has_data": bool(features.get("_has_data")),
    }


def explain(f: dict) -> list[dict]:
    """Human readable drivers behind the prediction (negative first)."""
    out = []
    if f["attendance_rate"] < 0.75:
        out.append({"type": "negative", "factor": "attendance", "text": f"Attendance is {f['attendance_rate']*100:.0f}% (below the 75% threshold)"})
    elif f["attendance_rate"] < 0.85:
        out.append({"type": "negative", "factor": "attendance", "text": f"Attendance is slipping at {f['attendance_rate']*100:.0f}%"})
    if f["trend"] <= -2:
        out.append({"type": "negative", "factor": "trend", "text": f"Scores are declining by ~{abs(f['trend']):.1f} points per exam"})
    if f["fail_subjects"] >= 1:
        out.append({"type": "negative", "factor": "subjects", "text": f"Below pass mark in {f['fail_subjects']} subject(s)"})
    if f["assignment_completion"] < 0.7:
        out.append({"type": "negative", "factor": "homework", "text": f"Only {f['assignment_completion']*100:.0f}% of homework completed"})
    if f["recent_pct"] < FAIL_THRESHOLD + 5:
        out.append({"type": "negative", "factor": "recent", "text": f"Latest exam score was {f['recent_pct']:.0f}%"})
    if f["score_std"] > 12:
        out.append({"type": "negative", "factor": "consistency", "text": "Performance is inconsistent between exams"})
    if f["trend"] >= 2:
        out.append({"type": "positive", "factor": "trend", "text": f"Improving by ~{f['trend']:.1f} points per exam"})
    if f["attendance_rate"] >= 0.93:
        out.append({"type": "positive", "factor": "attendance", "text": "Excellent attendance"})
    if f["assignment_completion"] >= 0.9:
        out.append({"type": "positive", "factor": "homework", "text": "Consistently completes homework"})
    if f["avg_pct"] >= 80:
        out.append({"type": "positive", "factor": "average", "text": f"Strong overall average of {f['avg_pct']:.0f}%"})
    return out
