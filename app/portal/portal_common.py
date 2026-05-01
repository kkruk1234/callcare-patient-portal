from __future__ import annotations

import json
import os
import secrets
import subprocess
from typing import Any, Dict, Optional
from zoneinfo import ZoneInfo

DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
CALLCARE_DB_NAME = os.getenv("CALLCARE_DB_NAME", "callcare").strip() or "callcare"


def safe_str(x: Any) -> str:
    try:
        return str(x if x is not None else "").strip()
    except Exception:
        return ""


def html_escape(s: Any) -> str:
    text = safe_str(s)
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _psql_target() -> str:
    return DATABASE_URL or CALLCARE_DB_NAME


def run_psql(sql: str, vars_map: Optional[Dict[str, str]] = None) -> str:
    cmd = ["psql", _psql_target(), "-X", "-q", "-At", "-v", "ON_ERROR_STOP=1"]
    for k, v in (vars_map or {}).items():
        cmd.extend(["-v", f"{k}={v}"])
    proc = subprocess.run(
        cmd,
        input=sql,
        text=True,
        capture_output=True,
        check=True,
    )
    return proc.stdout.strip()


def make_session_token() -> str:
    return secrets.token_urlsafe(32)


def verify_portal_login(first_name: str, last_name: str, dob: str, password: str) -> Optional[Dict[str, Any]]:
    sql = r"""
    SELECT json_build_object(
      'patient_id', p.id::text,
      'chart_number', p.chart_number,
      'patient_name', trim(concat_ws(' ', p.legal_first_name, p.legal_last_name)),
      'date_of_birth', p.date_of_birth::text
    )
    FROM callcare.patients p
    JOIN callcare.portal_accounts pa
      ON pa.patient_id = p.id
    WHERE lower(p.legal_first_name) = lower(NULLIF(:'FIRST_NAME', ''))
      AND lower(p.legal_last_name) = lower(NULLIF(:'LAST_NAME', ''))
      AND p.date_of_birth = NULLIF(:'DOB', '')::date
      AND pa.password_hash = crypt(:'PASSWORD', pa.password_hash)
      AND pa.is_active = true
      AND p.archived_at IS NULL
    LIMIT 1;
    """
    try:
        out = run_psql(
            sql,
            {
                "FIRST_NAME": first_name,
                "LAST_NAME": last_name,
                "DOB": dob,
                "PASSWORD": password,
            },
        )
        return json.loads(out) if out else None
    except Exception:
        return None


def signed_patient_group(chart_number: str) -> Optional[Dict[str, Any]]:
    patient_sql = r"""
    SELECT json_build_object(
      'chart_number', p.chart_number,
      'patient_name', trim(concat_ws(' ', p.legal_first_name, p.legal_last_name)),
      'date_of_birth', p.date_of_birth::text,
      'sex_at_birth', p.sex_at_birth,
      'email', p.email,
      'preferred_pharmacy',
        (
          SELECT json_build_object(
            'name', ph.name,
            'address_line_1', ph.address_line_1,
            'city', ph.city,
            'state', ph.state,
            'postal_code', ph.postal_code,
            'phone', ph.phone,
            'fax', ph.fax,
            'ncpdp_id', ph.ncpdp_id
          )
          FROM callcare.patient_pharmacies pp
          JOIN callcare.pharmacies ph
            ON ph.id = pp.pharmacy_id
          WHERE pp.patient_id = p.id
            AND pp.is_preferred = true
          ORDER BY ph.created_at DESC
          LIMIT 1
        ),
      'allergies',
        COALESCE(
          (
            SELECT json_agg(
              json_build_object(
                'allergen', a.allergen,
                'reaction', a.reaction,
                'severity', a.severity
              )
              ORDER BY a.created_at
            )
            FROM callcare.patient_allergies a
            WHERE a.patient_id = p.id
              AND a.is_active = true
          ),
          '[]'::json
        )
    )
    FROM callcare.patients p
    WHERE p.chart_number = NULLIF(:'CHART_NUMBER', '')
    LIMIT 1;
    """
    encounters_sql = r"""
    SELECT COALESCE(
      json_agg(
        json_build_object(
          'packet_id', pp.packet_id,
          'patient_ctx', json_build_object(
            'chart_number', pp.chart_number,
            'patient_name', trim(concat_ws(' ', p.legal_first_name, p.legal_last_name)),
            'date_of_birth', p.date_of_birth::text,
            'sex_at_birth', p.sex_at_birth,
            'chief_complaint', pp.chief_complaint,
            'encounter_started_at', pp.created_at::text,
            'preferred_pharmacy',
              (
                SELECT json_build_object(
                  'name', ph.name,
                  'address_line_1', ph.address_line_1,
                  'city', ph.city,
                  'state', ph.state,
                  'postal_code', ph.postal_code,
                  'phone', ph.phone,
                  'fax', ph.fax,
                  'ncpdp_id', ph.ncpdp_id
                )
                FROM callcare.patient_pharmacies xpp
                JOIN callcare.pharmacies ph
                  ON ph.id = xpp.pharmacy_id
                WHERE xpp.patient_id = p.id
                  AND xpp.is_preferred = true
                ORDER BY ph.created_at DESC
                LIMIT 1
              ),
            'allergies',
              COALESCE(
                (
                  SELECT json_agg(
                    json_build_object(
                      'allergen', a.allergen,
                      'reaction', a.reaction,
                      'severity', a.severity
                    )
                    ORDER BY a.created_at
                  )
                  FROM callcare.patient_allergies a
                  WHERE a.patient_id = p.id
                    AND a.is_active = true
                ),
                '[]'::json
              )
          ),
          'meta', json_build_object(
            'signed', pp.signed,
            'signed_at', pp.signed_at::text,
            'signed_by', pp.signed_by,
            'status', pp.status,
            'prescription_status', pp.prescription_status,
            'note_sent', pp.note_sent,
            'spoken_summary_comments', COALESCE(pp.spoken_summary_comments, ''),
            'addenda', COALESCE(pp.addenda, '[]'::jsonb)
          ),
          'packet', json_build_object(
            'packet_id', pp.packet_id,
            'note_text', pp.note_text,
            'created_at', pp.created_at::text
          ),
          'spoken_summary', COALESCE(pp.spoken_summary, ''),
          'created_at', pp.created_at::text
        )
        ORDER BY pp.created_at DESC
      ),
      '[]'::json
    )
    FROM callcare.portal_packets pp
    JOIN callcare.patients p
      ON p.id = pp.patient_id
    WHERE pp.chart_number = NULLIF(:'CHART_NUMBER', '')
      AND pp.signed = true;
    """
    try:
        patient_out = run_psql(patient_sql, {"CHART_NUMBER": chart_number})
        if not patient_out:
            return None
        patient_ctx = json.loads(patient_out)

        enc_out = run_psql(encounters_sql, {"CHART_NUMBER": chart_number})
        encounters = json.loads(enc_out) if enc_out else []

        return {
            "chart_number": chart_number,
            "patient_name": safe_str(patient_ctx.get("patient_name")),
            "patient_ctx": patient_ctx,
            "encounters": encounters,
        }
    except Exception:
        return None


def packet_bundle_from_db(packet_id: str) -> Optional[Dict[str, Any]]:
    sql = r"""
    SELECT json_build_object(
      'packet_id', pp.packet_id,
      'packet', json_build_object(
        'packet_id', pp.packet_id,
        'note_text', pp.note_text,
        'created_at', pp.created_at::text
      ),
      'meta', json_build_object(
        'signed', pp.signed,
        'signed_at', pp.signed_at::text,
        'signed_by', pp.signed_by,
        'status', pp.status,
        'prescription_status', pp.prescription_status,
        'note_sent', pp.note_sent,
        'spoken_summary_comments', COALESCE(pp.spoken_summary_comments, ''),
        'addenda', COALESCE(pp.addenda, '[]'::jsonb)
      ),
      'spoken_summary', COALESCE(pp.spoken_summary, ''),
      'patient_ctx', json_build_object(
        'chart_number', pp.chart_number,
        'patient_name', trim(concat_ws(' ', p.legal_first_name, p.legal_last_name)),
        'date_of_birth', p.date_of_birth::text,
        'sex_at_birth', p.sex_at_birth,
        'chief_complaint', pp.chief_complaint,
        'encounter_started_at', pp.created_at::text,
        'preferred_pharmacy',
          (
            SELECT json_build_object(
              'name', ph.name,
              'address_line_1', ph.address_line_1,
              'city', ph.city,
              'state', ph.state,
              'postal_code', ph.postal_code,
              'phone', ph.phone,
              'fax', ph.fax,
              'ncpdp_id', ph.ncpdp_id
            )
            FROM callcare.patient_pharmacies xpp
            JOIN callcare.pharmacies ph
              ON ph.id = xpp.pharmacy_id
            WHERE xpp.patient_id = p.id
              AND xpp.is_preferred = true
            ORDER BY ph.created_at DESC
            LIMIT 1
          ),
        'allergies',
          COALESCE(
            (
              SELECT json_agg(
                json_build_object(
                  'allergen', a.allergen,
                  'reaction', a.reaction,
                  'severity', a.severity
                )
                ORDER BY a.created_at
              )
              FROM callcare.patient_allergies a
              WHERE a.patient_id = p.id
                AND a.is_active = true
            ),
            '[]'::json
          )
      ),
      'created_at', pp.created_at::text
    )
    FROM callcare.portal_packets pp
    JOIN callcare.patients p
      ON p.id = pp.patient_id
    WHERE pp.packet_id = NULLIF(:'PACKET_ID', '')
    LIMIT 1;
    """
    try:
        out = run_psql(sql, {"PACKET_ID": packet_id})
        return json.loads(out) if out else None
    except Exception:
        return None


def render_list_items(items, keys, empty_text: str) -> str:
    if not items:
        return f"<p>{html_escape(empty_text)}</p>"

    rendered = []
    for item in items:
        parts = []
        for k in keys:
            val = safe_str(item.get(k))
            if val:
                parts.append(val)
        if parts:
            rendered.append(f"<li>{html_escape(' — '.join(parts))}</li>")

    if not rendered:
        return f"<p>{html_escape(empty_text)}</p>"

    return "<ul class='detail-list'>" + "".join(rendered) + "</ul>"


def render_pharmacy(ph) -> str:
    if not ph:
        return "<p>No preferred pharmacy on file.</p>"

    parts = [
        safe_str(ph.get("name")),
        safe_str(ph.get("address_line_1")),
        " ".join(
            x for x in [
                safe_str(ph.get("city")),
                safe_str(ph.get("state")),
                safe_str(ph.get("postal_code")),
            ] if x
        ).strip(),
        safe_str(ph.get("phone")),
        safe_str(ph.get("fax")),
        safe_str(ph.get("ncpdp_id")),
    ]
    parts = [p for p in parts if p]
    return "<ul class='detail-list'>" + "".join(f"<li>{html_escape(p)}</li>" for p in parts) + "</ul>"


PORTAL_TIMEZONE = ZoneInfo("America/New_York")


def format_portal_time(value: Any) -> str:
    text = safe_str(value)
    if not text:
        return ""

    normalized = text.replace("T", " ").replace("Z", "+00:00")

    try:
        dt = datetime.fromisoformat(normalized)
    except Exception:
        try:
            dt = datetime.strptime(normalized[:19], "%Y-%m-%d %H:%M:%S")
        except Exception:
            return text.split(".")[0]

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)

    return dt.astimezone(PORTAL_TIMEZONE).strftime("%Y-%m-%d %I:%M:%S %p %Z")


def signed_note_text(note_text: str, meta: Dict[str, Any]) -> str:
    text = safe_str(note_text)
    if not meta.get("signed"):
        return text

    signed_at = portal_timestamp(meta.get("signed_at"))
    signed_by = safe_str(meta.get("signed_by"))
    stamp = f"\n\nSigned electronically by {signed_by} on {signed_at}"
    if stamp.strip() in text:
        return text
    return text + stamp


def addendum_block(addendum: Dict[str, Any]) -> str:
    text = safe_str(addendum.get("text"))
    signed_at = portal_timestamp(addendum.get("signed_at"))
    signed_by = safe_str(addendum.get("signed_by"))
    return f"{text}\n\nSigned addendum by {signed_by} on {signed_at}"

# Backward-compatible timestamp formatter alias for patient portal imports.
def portal_timestamp(value):
    return format_portal_time(value)
