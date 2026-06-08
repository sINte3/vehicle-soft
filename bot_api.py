"""
bot_api.py -- BOT001 Telegram Foundation
Flask Blueprint: /api/bot/*

BOT001 scope: read-only endpoints + one-time link verification.
BOT001B: inactive-user guard added to link_verify.
BOT002/BOT003 will add request creation and status changes.

Security notes:
- No login/password auth via Telegram (per project policy)
- Bearer tokens validated via bot_api_sessions.token_hash
- No real BOT_TOKEN stored here -- environment variable only
- Sensitive fields (password_hash, tg_link_code_hash) never returned
- Inactive users cannot receive API tokens or be Telegram-linked (BOT001B)
"""

from datetime import timedelta
from functools import wraps

from flask import Blueprint, request, jsonify, g
from sqlalchemy import or_

from models import (
    db, User, Organization, Equipment,
    SparePartRequest, SparePartRequestItem, SparePart,
    SparePartStatusHistory, BotApiSession,
)
from bot_security import (
    utcnow, verify_secret, hash_secret, hash_api_token,
    make_api_token, parse_datetime_safe,
)

bot_api_bp = Blueprint("bot_api", __name__, url_prefix="/api/bot")

# Session valid for 30 days
SESSION_LIFETIME_DAYS = 30
# Link code valid for 10 minutes (set in app route, checked here)
LINK_CODE_EXPIRE_MINUTES = 10


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _json_error(message, code=400):
    return jsonify({"ok": False, "error": message}), code


def _user_to_dict(user):
    """Serialize a User to a safe dict -- never include password_hash or link code."""
    orgs = []
    try:
        for org in user.organizations:
            orgs.append({"id": org.id, "name": org.name, "short_name": org.short_name or ""})
    except Exception:
        pass

    perms = {}
    try:
        from models import UserModulePermission
        for perm in UserModulePermission.query.filter_by(user_id=user.id).all():
            perms[perm.module_code] = bool(perm.has_access)
    except Exception:
        pass

    return {
        "id": user.id,
        "username": user.username,
        "full_name": user.full_name or "",
        "role": user.role,
        "telegram_id": getattr(user, "telegram_id", None),
        "tg_notifications": getattr(user, "tg_notifications", 1),
        "organizations": orgs,
        "module_permissions": perms,
        "is_active": bool(user.is_active),
    }


def _resolve_bearer_token():
    """Extract and validate Bearer token from Authorization header.

    Returns (session, user) on success, or (None, None) on failure.
    Does NOT raise -- callers check the return value.
    """
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return None, None
    raw_token = auth[len("Bearer "):]
    if not raw_token:
        return None, None
    token_hash = hash_api_token(raw_token)
    session = BotApiSession.query.filter_by(token_hash=token_hash).first()
    if not session:
        return None, None
    now = utcnow()
    # Check revocation
    if session.revoked_at is not None:
        return None, None
    # Check expiry
    expires = parse_datetime_safe(session.expires_at)
    if expires and now > expires:
        return None, None
    # Touch last_used_at (best-effort, do not fail the request if this fails)
    try:
        session.last_used_at = now
        db.session.commit()
    except Exception:
        db.session.rollback()
    user = User.query.get(session.user_id)
    if not user or not user.is_active:
        return None, None
    return session, user


def bot_token_required(f):
    """Decorator: require a valid Bearer token in Authorization header."""
    @wraps(f)
    def decorated(*args, **kwargs):
        session, user = _resolve_bearer_token()
        if user is None:
            return _json_error("Unauthorized: valid Bearer token required", 401)
        g.bot_user = user
        g.bot_session = session
        return f(*args, **kwargs)
    return decorated


# ---------------------------------------------------------------------------
# GET /api/bot/health
# ---------------------------------------------------------------------------

@bot_api_bp.route("/health", methods=["GET"])
def health():
    """Public health check -- no auth required."""
    return jsonify({"ok": True, "module": "bot_api", "version": "BOT001"}), 200


# ---------------------------------------------------------------------------
# POST /api/bot/link/verify
# ---------------------------------------------------------------------------

@bot_api_bp.route("/link/verify", methods=["POST"])
def link_verify():
    """Verify a one-time Telegram link code and issue an API session token.

    Request JSON:
        {"telegram_id": 123456789, "code": "123456"}

    On success returns user profile + api_token (shown ONCE).
    On failure returns HTTP 400, 401, 403, or 409 with error description.

    Security:
    - Does NOT accept login/password.
    - Does NOT return password_hash or tg_link_code_hash.
    - Code verified with constant-time HMAC comparison.
    - Clears code fields immediately after successful verification.
    - BOT001B: Inactive users receive HTTP 403. No token is issued,
      no telegram_id is written, no session row is created.
    """
    data = request.get_json(silent=True)
    if not data:
        return _json_error("JSON body required", 400)

    telegram_id = data.get("telegram_id")
    code = data.get("code")

    if not isinstance(telegram_id, int) or telegram_id <= 0:
        return _json_error("telegram_id must be a positive integer", 400)
    if not code or not isinstance(code, str):
        return _json_error("code is required", 400)
    code = code.strip()
    if not code:
        return _json_error("code must not be empty", 400)

    # Find user by link code hash match -- scan users with non-null code
    # [REASON]: We cannot look up by code directly (only hashes stored).
    # We iterate only users that have an active link code, which is a small set.
    matching_user = None
    now = utcnow()

    users_with_code = User.query.filter(
        User.tg_link_code_hash.isnot(None)
    ).all()

    for candidate in users_with_code:
        code_hash = getattr(candidate, "tg_link_code_hash", None)
        if not code_hash:
            continue
        if verify_secret(code, code_hash):
            matching_user = candidate
            break

    if matching_user is None:
        return _json_error("Invalid or expired code", 401)

    # Check expiry
    expires = parse_datetime_safe(getattr(matching_user, "tg_link_code_expires_at", None))
    if expires is None or now > expires:
        # Clear the expired code
        try:
            matching_user.tg_link_code_hash = None
            matching_user.tg_link_code_expires_at = None
            matching_user.tg_link_code_created_at = None
            db.session.commit()
        except Exception:
            db.session.rollback()
        return _json_error("Code has expired. Ask admin to generate a new one.", 401)

    # Check if telegram_id already belongs to a different account
    existing = User.query.filter(
        User.telegram_id == telegram_id,
        User.id != matching_user.id
    ).first()
    if existing:
        return _json_error("This Telegram account is already linked to another user", 409)

    # BOT001B: Inactive-user guard.
    # [REASON]: A deactivated user who still holds a valid link code (e.g. code was
    # generated while active, then the account was deactivated before verification)
    # must NOT receive an api_token or have telegram_id written.
    # The code is intentionally left intact (not cleared); it will expire naturally
    # after 10 minutes. No bot_api_sessions row is created. No telegram_id is written.
    if not matching_user.is_active:
        return _json_error(
            "User is inactive. Telegram linking is not allowed.",
            403
        )

    # Success: write telegram_id, clear code fields, create session
    raw_token = make_api_token()
    token_hash = hash_api_token(raw_token)
    session_expires = now + timedelta(days=SESSION_LIFETIME_DAYS)

    try:
        matching_user.telegram_id = telegram_id
        matching_user.tg_link_code_hash = None
        matching_user.tg_link_code_expires_at = None
        matching_user.tg_link_code_created_at = None

        new_session = BotApiSession(
            user_id=matching_user.id,
            telegram_id=telegram_id,
            token_hash=token_hash,
            expires_at=session_expires,
            created_at=now,
        )
        db.session.add(new_session)
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        # Do not leak internal details
        return _json_error("Server error during link verification", 500)

    return jsonify({
        "ok": True,
        "api_token": raw_token,
        "token_expires_at": session_expires.isoformat(),
        "user": _user_to_dict(matching_user),
    }), 200


# ---------------------------------------------------------------------------
# GET /api/bot/me
# ---------------------------------------------------------------------------

@bot_api_bp.route("/me", methods=["GET"])
@bot_token_required
def me():
    """Return current user profile for authenticated bot session."""
    user = g.bot_user
    return jsonify({"ok": True, "user": _user_to_dict(user)}), 200


# ---------------------------------------------------------------------------
# GET /api/bot/requests
# ---------------------------------------------------------------------------

@bot_api_bp.route("/requests", methods=["GET"])
@bot_token_required
def list_requests():
    """Return paginated list of spare part requests.

    Query params:
        status  -- filter by status (draft/submitted/approved/rejected)
        limit   -- max results, default 20, max 100
        offset  -- pagination offset, default 0

    Admin sees all requests; operator sees only accessible org requests.
    """
    user = g.bot_user

    try:
        limit = min(int(request.args.get("limit", 20)), 100)
    except (TypeError, ValueError):
        limit = 20
    try:
        offset = max(int(request.args.get("offset", 0)), 0)
    except (TypeError, ValueError):
        offset = 0
    status_filter = request.args.get("status", "").strip()

    q = SparePartRequest.query
    if not user.is_admin:
        user_org_ids = user.get_org_ids()
        q = q.filter(SparePartRequest.organization_id.in_(user_org_ids))
    if status_filter:
        q = q.filter(SparePartRequest.status == status_filter)

    total = q.count()
    items = q.order_by(SparePartRequest.created_at.desc()).offset(offset).limit(limit).all()

    results = []
    for r in items:
        org = None
        try:
            org = r.organization
        except Exception:
            pass
        eq = None
        try:
            eq = r.equipment
        except Exception:
            pass
        results.append({
            "id": r.id,
            "request_date": r.request_date.isoformat() if r.request_date else None,
            "status": r.status,
            "organization_id": r.organization_id,
            "organization_name": org.name if org else "",
            "equipment_id": r.equipment_id,
            "equipment_name": (eq.name if eq else ""),
            "items_count": len(r.items) if r.items else 0,
            "created_by": r.created_by,
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "note": r.note or "",
        })

    return jsonify({
        "ok": True,
        "total": total,
        "limit": limit,
        "offset": offset,
        "requests": results,
    }), 200


# ---------------------------------------------------------------------------
# GET /api/bot/requests/<id>
# ---------------------------------------------------------------------------

@bot_api_bp.route("/requests/<int:rid>", methods=["GET"])
@bot_token_required
def get_request(rid):
    """Return full details of a spare part request including items and history."""
    user = g.bot_user
    r = SparePartRequest.query.get(rid)
    if not r:
        return _json_error("Request not found", 404)

    if not user.is_admin and not user.can_access_org(r.organization_id):
        return _json_error("Access denied", 403)

    org = None
    try:
        org = r.organization
    except Exception:
        pass
    eq = None
    try:
        eq = r.equipment
    except Exception:
        pass

    items = []
    for item in (r.items or []):
        items.append({
            "id": item.id,
            "name": item.name,
            "part_number": item.part_number or "",
            "quantity": item.quantity,
            "unit": item.unit or "",
            "note": item.note or "",
        })

    # Status history (if table exists)
    history = []
    try:
        history_rows = SparePartStatusHistory.query.filter_by(
            request_id=rid
        ).order_by(SparePartStatusHistory.changed_at.asc()).all()
        for h in history_rows:
            history.append({
                "id": h.id,
                "old_status": h.old_status,
                "new_status": h.new_status,
                "comment": h.comment or "",
                "changed_by": h.changed_by,
                "changed_at": h.changed_at.isoformat() if h.changed_at else None,
            })
    except Exception:
        pass

    creator_name = ""
    try:
        if r.creator:
            creator_name = r.creator.full_name or r.creator.username
    except Exception:
        pass

    reviewer_name = ""
    try:
        if r.reviewer:
            reviewer_name = r.reviewer.full_name or r.reviewer.username
    except Exception:
        pass

    return jsonify({
        "ok": True,
        "request": {
            "id": r.id,
            "request_date": r.request_date.isoformat() if r.request_date else None,
            "status": r.status,
            "organization_id": r.organization_id,
            "organization_name": org.name if org else "",
            "equipment_id": r.equipment_id,
            "equipment_name": eq.name if eq else "",
            "note": r.note or "",
            "created_by": r.created_by,
            "creator_name": creator_name,
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "reviewed_by": r.reviewed_by,
            "reviewer_name": reviewer_name,
            "reviewed_at": r.reviewed_at.isoformat() if r.reviewed_at else None,
            "review_comment": r.review_comment or "",
            "items": items,
            "history": history,
        }
    }), 200


# ---------------------------------------------------------------------------
# GET /api/bot/equipment
# ---------------------------------------------------------------------------

@bot_api_bp.route("/equipment", methods=["GET"])
@bot_token_required
def list_equipment():
    """Return active equipment for accessible organizations.

    Query params:
        org_id  -- filter by specific organization
    """
    user = g.bot_user
    org_id = request.args.get("org_id", type=int)

    q = Equipment.query.filter_by(is_active=True)

    if org_id:
        if not user.is_admin and not user.can_access_org(org_id):
            return _json_error("Access denied to this organization", 403)
        q = q.filter(Equipment.organization_id == org_id)
    else:
        if not user.is_admin:
            user_org_ids = user.get_org_ids()
            q = q.filter(Equipment.organization_id.in_(user_org_ids))

    items = q.order_by(Equipment.organization_id, Equipment.name).all()
    results = []
    for eq in items:
        results.append({
            "id": eq.id,
            "name": eq.name,
            "plate": eq.plate or "",
            "category": eq.category or "",
            "organization_id": eq.organization_id,
            "is_active": bool(eq.is_active),
        })

    return jsonify({"ok": True, "equipment": results, "total": len(results)}), 200


# ---------------------------------------------------------------------------
# GET /api/bot/catalog
# ---------------------------------------------------------------------------

@bot_api_bp.route("/catalog", methods=["GET"])
@bot_token_required
def catalog():
    """Return spare parts catalog items.

    Query params:
        q  -- search string (name or part_number, case-insensitive)

    Returns up to 20 items. Returns empty list if catalog is empty (not an error).
    """
    q_str = request.args.get("q", "").strip()

    try:
        query = SparePart.query
        if q_str:
            pattern = "%" + q_str + "%"
            query = query.filter(
                or_(
                    SparePart.name.ilike(pattern),
                    SparePart.part_number.ilike(pattern),
                )
            )
        parts = query.order_by(SparePart.name).limit(20).all()
    except Exception:
        parts = []

    results = []
    for p in parts:
        results.append({
            "id": p.id,
            "name": p.name,
            "part_number": p.part_number or "",
            "unit": p.unit or "",
            "category": p.category or "",
        })

    return jsonify({"ok": True, "catalog": results, "total": len(results)}), 200
