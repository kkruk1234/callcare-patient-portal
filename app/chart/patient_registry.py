from __future__ import annotations

import json
import os
import re
import subprocess
from datetime import datetime
from typing import Any, Dict, Optional


DB_NAME = os.getenv("CALLCARE_DB_NAME", "callcare").strip() or "callcare"


def _run_psql(sql: str, vars_map: Optional[Dict[str, str]] = None) -> str:
    cmd = ["psql", DB_NAME, "-X", "-q", "-At", "-v", "ON_ERROR_STOP=1"]
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


def normalize_dob_text(text: str) -> Optional[str]:
    raw = " ".join(str(text or "").strip().split())
    if not raw:
        return None

    cleaned = raw.strip()
    cleaned = re.sub(r"[.]+$", "", cleaned)
    cleaned = cleaned.replace(",", "")
    cleaned = cleaned.strip()

    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", cleaned):
        return cleaned

    m = re.fullmatch(r"(\d{1,2})-(\d{1,2})-(\d{4})", cleaned)
    if m:
        mm, dd, yyyy = m.groups()
        try:
            return datetime(int(yyyy), int(mm), int(dd)).strftime("%Y-%m-%d")
        except ValueError:
            return None

    m = re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{4})", cleaned)
    if m:
        mm, dd, yyyy = m.groups()
        try:
            return datetime(int(yyyy), int(mm), int(dd)).strftime("%Y-%m-%d")
        except ValueError:
            return None

    m = re.fullmatch(r"(\d{2})(\d{2})(\d{4})", cleaned)
    if m:
        mm, dd, yyyy = m.groups()
        try:
            return datetime(int(yyyy), int(mm), int(dd)).strftime("%Y-%m-%d")
        except ValueError:
            return None

    spoken = cleaned.lower()
    spoken = re.sub(r"(\d)(st|nd|rd|th)\b", r"\1", spoken)

    for fmt in ("%B %d %Y", "%b %d %Y"):
        try:
            return datetime.strptime(spoken.title(), fmt).strftime("%Y-%m-%d")
        except ValueError:
            pass

    return None


def normalize_pin_text(text: str) -> Optional[str]:
    raw = " ".join(str(text or "").strip().split())
    if not raw:
        return None

    cleaned = raw.strip().lower()
    cleaned = re.sub(r"[.!,;:]+$", "", cleaned)

    all_digits = re.sub(r"\D", "", cleaned)
    if 4 <= len(all_digits) <= 10:
        return all_digits

    return None


def _one_unique(sql: str, vars_map: Dict[str, str]) -> Optional[Dict[str, Any]]:
    out = _run_psql(sql, vars_map)
    return json.loads(out) if out else None


def verify_patient_identity(first_name: str, last_name: str, dob_text: str, verbal_pin: str) -> Optional[Dict[str, Any]]:
    dob_iso = normalize_dob_text(dob_text)
    pin_norm = normalize_pin_text(verbal_pin)

    if not dob_iso or not pin_norm:
        return None

    sql_exact = r"""
    WITH matches AS (
      SELECT
        p.id,
        p.chart_number,
        p.legal_first_name,
        p.legal_last_name,
        p.date_of_birth
      FROM callcare.patients p
      JOIN callcare.patient_auth pa
        ON pa.patient_id = p.id
      WHERE lower(p.legal_first_name) = lower(:'FIRST_NAME')
        AND lower(p.legal_last_name) = lower(:'LAST_NAME')
        AND p.date_of_birth = NULLIF(:'DOB_ISO', '')::date
        AND pa.verbal_pin_hash = crypt(:'VERBAL_PIN', pa.verbal_pin_hash)
        AND p.archived_at IS NULL
    ),
    counted AS (
      SELECT *, COUNT(*) OVER () AS match_count
      FROM matches
    )
    SELECT json_build_object(
        'patient_id', id::text,
        'chart_number', chart_number,
        'legal_first_name', legal_first_name,
        'legal_last_name', legal_last_name,
        'date_of_birth', date_of_birth::text
    )
    FROM counted
    WHERE match_count = 1
    LIMIT 1;
    """

    sql_first_dob_pin = r"""
    WITH matches AS (
      SELECT
        p.id,
        p.chart_number,
        p.legal_first_name,
        p.legal_last_name,
        p.date_of_birth
      FROM callcare.patients p
      JOIN callcare.patient_auth pa
        ON pa.patient_id = p.id
      WHERE lower(p.legal_first_name) = lower(:'FIRST_NAME')
        AND p.date_of_birth = NULLIF(:'DOB_ISO', '')::date
        AND pa.verbal_pin_hash = crypt(:'VERBAL_PIN', pa.verbal_pin_hash)
        AND p.archived_at IS NULL
    ),
    counted AS (
      SELECT *, COUNT(*) OVER () AS match_count
      FROM matches
    )
    SELECT json_build_object(
        'patient_id', id::text,
        'chart_number', chart_number,
        'legal_first_name', legal_first_name,
        'legal_last_name', legal_last_name,
        'date_of_birth', date_of_birth::text
    )
    FROM counted
    WHERE match_count = 1
    LIMIT 1;
    """

    sql_dob_pin = r"""
    WITH matches AS (
      SELECT
        p.id,
        p.chart_number,
        p.legal_first_name,
        p.legal_last_name,
        p.date_of_birth
      FROM callcare.patients p
      JOIN callcare.patient_auth pa
        ON pa.patient_id = p.id
      WHERE p.date_of_birth = NULLIF(:'DOB_ISO', '')::date
        AND pa.verbal_pin_hash = crypt(:'VERBAL_PIN', pa.verbal_pin_hash)
        AND p.archived_at IS NULL
    ),
    counted AS (
      SELECT *, COUNT(*) OVER () AS match_count
      FROM matches
    )
    SELECT json_build_object(
        'patient_id', id::text,
        'chart_number', chart_number,
        'legal_first_name', legal_first_name,
        'legal_last_name', legal_last_name,
        'date_of_birth', date_of_birth::text
    )
    FROM counted
    WHERE match_count = 1
    LIMIT 1;
    """

    vars_all = {
        "FIRST_NAME": first_name,
        "LAST_NAME": last_name,
        "DOB_ISO": dob_iso,
        "VERBAL_PIN": pin_norm,
    }

    exact = _one_unique(sql_exact, vars_all)
    if exact:
        return exact

    first_fallback = _one_unique(sql_first_dob_pin, vars_all)
    if first_fallback:
        return first_fallback

    return _one_unique(sql_dob_pin, vars_all)


def create_verified_encounter(patient_id: str, call_sid: str) -> str:
    sql = r"""
    WITH ins AS (
      INSERT INTO callcare.encounters (
        id,
        patient_id,
        encounter_type,
        encounter_status,
        call_sid,
        started_at,
        verification_method,
        verification_success,
        initiated_by
      )
      VALUES (
        gen_random_uuid(),
        NULLIF(:'PATIENT_ID', '')::uuid,
        'telephone_async_review',
        'in_progress',
        NULLIF(:'CALL_SID', ''),
        now(),
        'dob_plus_verbal_pin',
        true,
        'patient_call'
      )
      RETURNING id
    )
    SELECT id::text FROM ins;
    """
    return _run_psql(sql, {"PATIENT_ID": patient_id, "CALL_SID": call_sid})


def update_encounter_identity(encounter_id: str, verified_name: str, verified_dob: str) -> None:
    sql = r"""
    UPDATE callcare.encounters
    SET
      verified_name = NULLIF(:'VERIFIED_NAME', ''),
      verified_dob = NULLIF(:'VERIFIED_DOB', '')::date,
      updated_at = now()
    WHERE id = NULLIF(:'ENCOUNTER_ID', '')::uuid;
    """
    _run_psql(
        sql,
        {
            "ENCOUNTER_ID": encounter_id,
            "VERIFIED_NAME": verified_name,
            "VERIFIED_DOB": verified_dob,
        },
    )


def update_encounter_chief_complaint(encounter_id: str, chief_complaint: str) -> None:
    sql = r"""
    UPDATE callcare.encounters
    SET
      chief_complaint = NULLIF(:'CHIEF_COMPLAINT', ''),
      updated_at = now()
    WHERE id = NULLIF(:'ENCOUNTER_ID', '')::uuid;
    """
    _run_psql(
        sql,
        {
            "ENCOUNTER_ID": encounter_id,
            "CHIEF_COMPLAINT": chief_complaint,
        },
    )


def get_chart_context(patient_id: str) -> Dict[str, Any]:
    sql = r"""
    SELECT json_build_object(
      'patient_id', p.id::text,
      'chart_number', p.chart_number,
      'legal_first_name', p.legal_first_name,
      'legal_last_name', p.legal_last_name,
      'date_of_birth', p.date_of_birth::text,
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
      'medications',
        COALESCE(
          (
            SELECT json_agg(
              json_build_object(
                'medication_name', pm.medication_name,
                'strength', pm.strength,
                'dose_instructions', pm.dose_instructions,
                'route', pm.route,
                'frequency', pm.frequency
              )
              ORDER BY pm.created_at
            )
            FROM callcare.patient_medications pm
            WHERE pm.patient_id = p.id
              AND pm.is_current = true
          ),
          '[]'::json
        ),
      'allergies',
        COALESCE(
          (
            SELECT json_agg(
              json_build_object(
                'allergen', pa.allergen,
                'reaction', pa.reaction,
                'severity', pa.severity
              )
              ORDER BY pa.created_at
            )
            FROM callcare.patient_allergies pa
            WHERE pa.patient_id = p.id
              AND pa.is_active = true
          ),
          '[]'::json
        ),
      'conditions',
        COALESCE(
          (
            SELECT json_agg(
              json_build_object(
                'condition_name', pc.condition_name,
                'status', pc.status
              )
              ORDER BY pc.created_at
            )
            FROM callcare.patient_conditions pc
            WHERE pc.patient_id = p.id
          ),
          '[]'::json
        )
    )
    FROM callcare.patients p
    WHERE p.id = NULLIF(:'PATIENT_ID', '')::uuid
    LIMIT 1;
    """
    out = _run_psql(sql, {"PATIENT_ID": patient_id})
    return json.loads(out) if out else {}
