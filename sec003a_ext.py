"""TASK-SEC-003A extension: personal users, forced password change, audit log."""

from datetime import datetime, timedelta
import json

from flask import request
from flask_login import current_user
from sqlalchemy import text

SENSITIVE_KEYS = {'password', 'new_password', 'confirm_password', 'current_password', 'token', 'secret', 'password_hash'}


def _now_iso():
    return datetime.utcnow().replace(microsecond=0).isoformat()


def _safe_json(data):
    if data is None:
        return None

    def clean(obj):
        if isinstance(obj, dict):
            cleaned = {}
            for k, v in obj.items():
                k_lower = str(k).lower()
                if k_lower in SENSITIVE_KEYS or 'password' in k_lower or 'token' in k_lower or 'secret' in k_lower:
                    cleaned[k] = '***'
                else:
                    cleaned[k] = clean(v)
            return cleaned
        if isinstance(obj, (list, tuple)):
            return [clean(x) for x in obj]
        return obj

    return json.dumps(clean(data), ensure_ascii=False, default=str)


def table_columns(db, table_name):
    rows = db.session.execute(text(f"PRAGMA table_info({table_name})")).mappings().all()
    return {r['name'] for r in rows}


def get_user_extra(db, user_id):
    cols = table_columns(db, 'users')
    wanted = [c for c in [
        'must_change_password', 'password_changed_at', 'last_login_ip',
        'failed_login_count', 'locked_until', 'updated_at', 'created_by_id'
    ] if c in cols]
    if not wanted:
        return {}
    sql = 'SELECT ' + ', '.join(wanted) + ' FROM users WHERE id = :id'
    row = db.session.execute(text(sql), {'id': user_id}).mappings().first()
    return dict(row) if row else {}


def set_user_extra(db, user_id, **values):
    cols = table_columns(db, 'users')
    values = {k: v for k, v in values.items() if k in cols}
    if not values:
        return
    assignments = ', '.join([f'{k} = :{k}' for k in values])
    params = dict(values)
    params['id'] = user_id
    db.session.execute(text(f'UPDATE users SET {assignments} WHERE id = :id'), params)


def must_change_password(db, user_id):
    return bool(get_user_extra(db, user_id).get('must_change_password'))


def is_locked(db, user_id):
    locked_until = get_user_extra(db, user_id).get('locked_until')
    if not locked_until:
        return False
    try:
        return datetime.fromisoformat(str(locked_until)) > datetime.utcnow()
    except Exception:
        return False


def record_failed_login(db, user):
    if not user:
        return
    extra = get_user_extra(db, user.id)
    count = int(extra.get('failed_login_count') or 0) + 1
    values = {'failed_login_count': count, 'updated_at': _now_iso()}
    if count >= 5:
        values['locked_until'] = (datetime.utcnow() + timedelta(minutes=15)).replace(microsecond=0).isoformat()
    set_user_extra(db, user.id, **values)


def record_successful_login(db, user):
    set_user_extra(
        db, user.id,
        failed_login_count=0,
        locked_until=None,
        last_login_ip=request.remote_addr or '',
        updated_at=_now_iso(),
    )


def clear_must_change_password(db, user_id):
    set_user_extra(
        db, user_id,
        must_change_password=0,
        password_changed_at=_now_iso(),
        updated_at=_now_iso(),
    )


def require_temp_password_change(db, user_id):
    set_user_extra(db, user_id, must_change_password=1, updated_at=_now_iso())


def password_is_strong_enough(username, password):
    if not password or len(password) < 8:
        return False, 'Парол камида 8 белгидан иборат бўлиши керак'
    if username and username.lower() in password.lower():
        return False, 'Парол логинни ўз ичига олмаслиги керак'
    return True, ''


def log_audit(db, action, entity_type='', entity_id=None, entity_label='', module='',
              before=None, after=None, changes=None, status='ok', description='', actor_user=None):
    if not table_columns(db, 'audit_logs'):
        return

    actor = actor_user
    if actor is None:
        try:
            actor = current_user if current_user.is_authenticated else None
        except Exception:
            actor = None

    data = {
        'created_at': _now_iso(),
        'user_id': getattr(actor, 'id', None),
        'username_snapshot': getattr(actor, 'username', '') if actor else '',
        'full_name_snapshot': getattr(actor, 'full_name', '') if actor else '',
        'role_snapshot': getattr(actor, 'role', '') if actor else '',
        'action': action,
        'entity_type': entity_type,
        'entity_id': entity_id,
        'entity_label': entity_label or '',
        'module': module or '',
        'route': request.path if request else '',
        'method': request.method if request else '',
        'ip_address': request.remote_addr if request else '',
        'user_agent': (request.headers.get('User-Agent', '')[:500] if request else ''),
        'before_json': _safe_json(before),
        'after_json': _safe_json(after),
        'changes_json': _safe_json(changes),
        'status': status,
        'description': description or '',
    }
    cols = table_columns(db, 'audit_logs')
    data = {k: v for k, v in data.items() if k in cols}
    names = ', '.join(data.keys())
    vals = ', '.join(':' + k for k in data.keys())
    db.session.execute(text(f'INSERT INTO audit_logs ({names}) VALUES ({vals})'), data)


def model_snapshot(obj, fields):
    if obj is None:
        return None
    data = {}
    for field in fields:
        try:
            data[field] = getattr(obj, field)
        except Exception:
            data[field] = None
    return data


def diff_dict(before, after):
    before = before or {}
    after = after or {}
    changes = {}
    for key in sorted(set(before.keys()) | set(after.keys())):
        if before.get(key) != after.get(key):
            changes[key] = {'before': before.get(key), 'after': after.get(key)}
    return changes


def audit_action_name(base, object_id):
    return base + ('_updated' if object_id else '_created')
