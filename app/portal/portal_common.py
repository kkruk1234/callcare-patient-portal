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

# FINAL_CALLCARE_PATIENT_TIMESTAMP_OVERRIDE_START
def portal_timestamp(value):
    from datetime import datetime, timezone
    from zoneinfo import ZoneInfo

    text = safe_str(value)
    if not text:
        return ""

    normalized = text.strip().replace("T", " ").replace("Z", "+00:00")

    if len(normalized) >= 19:
        normalized = normalized[:19] + normalized[19:]

    try:
        dt = datetime.fromisoformat(normalized)
    except Exception:
        try:
            dt = datetime.strptime(normalized[:19], "%Y-%m-%d %H:%M:%S")
        except Exception:
            return text.split(".")[0]

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)

    return dt.astimezone(ZoneInfo("America/New_York")).strftime("%Y-%m-%d %I:%M:%S %p %Z")


def signed_note_text(note_text, meta):
    text = safe_str(note_text)
    if not meta.get("signed"):
        return text

    signed_at = portal_timestamp(meta.get("signed_at"))
    signed_by = safe_str(meta.get("signed_by")) or signature_line()

    import re
    text = re.sub(r"\n\nSigned electronically by .*? on [^\n]+\s*$", "", text)

    return text + f"\n\nSigned electronically by {signed_by} on {signed_at}"


def addendum_block(addendum):
    text = safe_str(addendum.get("text"))
    signed_at = portal_timestamp(addendum.get("signed_at"))
    signed_by = safe_str(addendum.get("signed_by")) or signature_line()

    import re
    text = re.sub(r"\n\nSigned addendum by .*? on [^\n]+\s*$", "", text)

    return f"{text}\n\nSigned addendum by {signed_by} on {signed_at}"
# FINAL_CALLCARE_PATIENT_TIMESTAMP_OVERRIDE_END



def patient_profile_bundle(chart_number: str) -> Dict[str, Any]:
    sql = r"""
    SELECT json_build_object(
      'patient_id', p.id::text,
      'chart_number', p.chart_number,
      'patient_name', trim(concat_ws(' ', p.legal_first_name, p.legal_last_name)),
      'preferred_name', p.preferred_name,
      'date_of_birth', p.date_of_birth::text,
      'sex_at_birth', p.sex_at_birth,
      'gender_identity', p.gender_identity,
      'phone_number', p.phone_number,
      'email', p.email,
      'address',
        COALESCE((
          SELECT json_build_object(
            'address_line_1', a.address_line_1,
            'address_line_2', a.address_line_2,
            'city', a.city,
            'state', a.state,
            'postal_code', a.postal_code,
            'county_name', a.county_name
          )
          FROM callcare.patient_addresses a
          WHERE a.patient_id = p.id
            AND a.is_current = true
          ORDER BY a.updated_at DESC
          LIMIT 1
        ), '{}'::json),
      'vitals',
        COALESCE((
          SELECT json_build_object(
            'height_feet', v.height_feet::text,
            'height_inches', v.height_inches::text,
            'weight_lbs', v.weight_lbs::text
          )
          FROM callcare.patient_vitals v
          WHERE v.patient_id = p.id
          ORDER BY v.updated_at DESC, v.created_at DESC
          LIMIT 1
        ), '{}'::json),
      'social',
        COALESCE((
          SELECT json_build_object(
            'tobacco_status', sh.tobacco_status,
            'alcohol_use', sh.alcohol_use,
            'drug_use', sh.drug_use,
            'exercise_level', sh.exercise_level,
            'occupation', sh.occupation,
            'sexually_active', sh.sexually_active,
            'sexual_partners_count', sh.sexual_partners_count,
            'uses_protection', sh.uses_protection,
            'protection_type', sh.protection_type
          )
          FROM callcare.patient_social_history_structured sh
          WHERE sh.patient_id = p.id
          LIMIT 1
        ), '{}'::json)
    )
    FROM callcare.patients p
    WHERE p.chart_number = NULLIF(:'CHART_NUMBER', '')
      AND p.archived_at IS NULL
    LIMIT 1;
    """
    out = run_psql(sql, {"CHART_NUMBER": chart_number})
    return json.loads(out) if out else {}


def save_patient_profile(chart_number: str, form: Dict[str, Any], actor_type: str = "patient") -> None:
    patient_id = run_psql(
        "SELECT id::text FROM callcare.patients WHERE chart_number = NULLIF(:'CHART_NUMBER', '') AND archived_at IS NULL LIMIT 1;",
        {"CHART_NUMBER": safe_str(chart_number)},
    )

    if not patient_id:
        return

    values = {
        "PATIENT_ID": safe_str(patient_id),
        "ACTOR_TYPE": safe_str(actor_type) or "patient",
        "PREFERRED_NAME": safe_str(form.get("preferred_name")),
        "SEX_AT_BIRTH": safe_str(form.get("sex_at_birth")),
        "GENDER_IDENTITY": safe_str(form.get("gender_identity")),
        "PHONE_NUMBER": safe_str(form.get("phone_number")),
        "EMAIL": safe_str(form.get("email")),
        "ADDRESS_LINE_1": safe_str(form.get("address_line_1")),
        "ADDRESS_LINE_2": safe_str(form.get("address_line_2")),
        "CITY": safe_str(form.get("city")),
        "STATE": safe_str(form.get("state")) or "GA",
        "POSTAL_CODE": safe_str(form.get("postal_code")),
        "COUNTY_NAME": safe_str(form.get("county_name")),
        "HEIGHT_FEET": safe_str(form.get("height_feet")),
        "HEIGHT_INCHES": safe_str(form.get("height_inches")),
        "WEIGHT_LBS": safe_str(form.get("weight_lbs")),
        "TOBACCO_STATUS": safe_str(form.get("tobacco_status")),
        "ALCOHOL_USE": safe_str(form.get("alcohol_use")),
        "RECREATIONAL_DRUG_USE": safe_str(form.get("drug_use")) or safe_str(form.get("recreational_drug_use")),
        "EXERCISE_LEVEL": safe_str(form.get("exercise_level")),
        "OCCUPATION": safe_str(form.get("occupation")),
        "SEXUALLY_ACTIVE": safe_str(form.get("sexually_active")),
        "SEXUAL_PARTNERS_COUNT": safe_str(form.get("sexual_partners_count")),
        "USES_PROTECTION": safe_str(form.get("uses_protection")),
        "PROTECTION_TYPE": safe_str(form.get("protection_type")),
    }

    sql = r"""
    UPDATE callcare.patients
    SET preferred_name = NULLIF(:'PREFERRED_NAME', ''),
        sex_at_birth = NULLIF(:'SEX_AT_BIRTH', ''),
        gender_identity = NULLIF(:'GENDER_IDENTITY', ''),
        phone_number = NULLIF(:'PHONE_NUMBER', ''),
        email = NULLIF(:'EMAIL', ''),
        updated_at = now()
    WHERE id = NULLIF(:'PATIENT_ID', '')::uuid;

    UPDATE callcare.patient_addresses
    SET is_current = false,
        updated_at = now()
    WHERE patient_id = NULLIF(:'PATIENT_ID', '')::uuid
      AND is_current = true;

    INSERT INTO callcare.patient_addresses (
      id,
      patient_id,
      address_line_1,
      address_line_2,
      city,
      state,
      postal_code,
      county_name,
      is_current,
      is_mailing_address,
      rural_eligible,
      created_at,
      updated_at
    )
    VALUES (
      gen_random_uuid(),
      NULLIF(:'PATIENT_ID', '')::uuid,
      COALESCE(NULLIF(:'ADDRESS_LINE_1', ''), 'Not provided'),
      NULLIF(:'ADDRESS_LINE_2', ''),
      COALESCE(NULLIF(:'CITY', ''), 'Not provided'),
      COALESCE(NULLIF(:'STATE', ''), 'GA'),
      COALESCE(NULLIF(:'POSTAL_CODE', ''), '00000'),
      NULLIF(:'COUNTY_NAME', ''),
      true,
      true,
      false,
      now(),
      now()
    );

    INSERT INTO callcare.patient_vitals (
      id,
      patient_id,
      height_feet,
      height_inches,
      weight_lbs,
      height_cm,
      weight_kg,
      bmi,
      source,
      created_at,
      updated_at
    )
    VALUES (
      gen_random_uuid(),
      NULLIF(:'PATIENT_ID', '')::uuid,
      NULLIF(:'HEIGHT_FEET', '')::integer,
      NULLIF(:'HEIGHT_INCHES', '')::integer,
      NULLIF(:'WEIGHT_LBS', '')::numeric,
      CASE
        WHEN NULLIF(:'HEIGHT_FEET', '') IS NOT NULL OR NULLIF(:'HEIGHT_INCHES', '') IS NOT NULL
        THEN round(((COALESCE(NULLIF(:'HEIGHT_FEET', '')::numeric, 0) * 12 + COALESCE(NULLIF(:'HEIGHT_INCHES', '')::numeric, 0)) * 2.54), 1)
        ELSE NULL
      END,
      CASE
        WHEN NULLIF(:'WEIGHT_LBS', '') IS NOT NULL
        THEN round((NULLIF(:'WEIGHT_LBS', '')::numeric * 0.45359237), 1)
        ELSE NULL
      END,
      CASE
        WHEN (COALESCE(NULLIF(:'HEIGHT_FEET', '')::numeric, 0) * 12 + COALESCE(NULLIF(:'HEIGHT_INCHES', '')::numeric, 0)) > 0
         AND NULLIF(:'WEIGHT_LBS', '') IS NOT NULL
        THEN round((NULLIF(:'WEIGHT_LBS', '')::numeric / ((COALESCE(NULLIF(:'HEIGHT_FEET', '')::numeric, 0) * 12 + COALESCE(NULLIF(:'HEIGHT_INCHES', '')::numeric, 0)) * (COALESCE(NULLIF(:'HEIGHT_FEET', '')::numeric, 0) * 12 + COALESCE(NULLIF(:'HEIGHT_INCHES', '')::numeric, 0))) * 703), 1)
        ELSE NULL
      END,
      'patient_portal',
      now(),
      now()
    );

    INSERT INTO callcare.patient_social_history_structured (
      patient_id,
      tobacco_status,
      alcohol_use,
      drug_use,
      recreational_drug_use,
      exercise_level,
      occupation,
      sexually_active,
      sexual_partners_count,
      uses_protection,
      protection_type,
      created_at,
      updated_at
    )
    VALUES (
      NULLIF(:'PATIENT_ID', '')::uuid,
      NULLIF(:'TOBACCO_STATUS', ''),
      NULLIF(:'ALCOHOL_USE', ''),
      NULLIF(:'RECREATIONAL_DRUG_USE', ''),
      NULLIF(:'RECREATIONAL_DRUG_USE', ''),
      NULLIF(:'EXERCISE_LEVEL', ''),
      NULLIF(:'OCCUPATION', ''),
      NULLIF(:'SEXUALLY_ACTIVE', ''),
      NULLIF(:'SEXUAL_PARTNERS_COUNT', ''),
      NULLIF(:'USES_PROTECTION', ''),
      NULLIF(:'PROTECTION_TYPE', ''),
      now(),
      now()
    )
    ON CONFLICT (patient_id) DO UPDATE
    SET tobacco_status = EXCLUDED.tobacco_status,
        alcohol_use = EXCLUDED.alcohol_use,
        drug_use = EXCLUDED.drug_use,
        recreational_drug_use = EXCLUDED.recreational_drug_use,
        exercise_level = EXCLUDED.exercise_level,
        occupation = EXCLUDED.occupation,
        sexually_active = EXCLUDED.sexually_active,
        sexual_partners_count = EXCLUDED.sexual_partners_count,
        uses_protection = EXCLUDED.uses_protection,
        protection_type = EXCLUDED.protection_type,
        updated_at = now();

    INSERT INTO callcare.audit_events (
      id,
      actor_type,
      actor_id,
      patient_id,
      encounter_id,
      event_type,
      event_json,
      created_at
    )
    VALUES (
      gen_random_uuid(),
      :'ACTOR_TYPE',
      NULL,
      NULLIF(:'PATIENT_ID', '')::uuid,
      NULL,
      'patient_profile_updated',
      jsonb_build_object(
        'source', 'patient_portal',
        'changed_by', :'ACTOR_TYPE',
        'submitted_fields', jsonb_build_object(
          'preferred_name', :'PREFERRED_NAME',
          'sex_at_birth', :'SEX_AT_BIRTH',
          'gender_identity', :'GENDER_IDENTITY',
          'phone_number', :'PHONE_NUMBER',
          'email', :'EMAIL',
          'address_line_1', :'ADDRESS_LINE_1',
          'address_line_2', :'ADDRESS_LINE_2',
          'city', :'CITY',
          'state', :'STATE',
          'postal_code', :'POSTAL_CODE',
          'county_name', :'COUNTY_NAME',
          'height_feet', :'HEIGHT_FEET',
          'height_inches', :'HEIGHT_INCHES',
          'weight_lbs', :'WEIGHT_LBS',
          'tobacco_status', :'TOBACCO_STATUS',
          'alcohol_use', :'ALCOHOL_USE',
          'recreational_drug_use', :'RECREATIONAL_DRUG_USE',
          'exercise_level', :'EXERCISE_LEVEL',
          'occupation', :'OCCUPATION',
          'sexually_active', :'SEXUALLY_ACTIVE',
          'sexual_partners_count', :'SEXUAL_PARTNERS_COUNT',
          'uses_protection', :'USES_PROTECTION',
          'protection_type', :'PROTECTION_TYPE'
        )
      ),
      now()
    );
    """

    run_psql(sql, values)


COMMON_HISTORY_CONDITIONS = ['Hypertension', 'Diabetes', 'High Cholesterol', 'Coronary Artery Disease', 'Heart Failure', 'Atrial Fibrillation', 'Stroke', 'COPD', 'Asthma', 'Sleep Apnea', 'GERD', 'Peptic Ulcer Disease', 'Irritable Bowel Syndrome', 'Crohn Disease', 'Ulcerative Colitis', 'Chronic Kidney Disease', 'Kidney Stones', 'Migraines', 'Seizure Disorder', 'Depression', 'Anxiety', 'Bipolar Disorder', 'PTSD', 'ADHD', 'Hypothyroidism', 'Hyperthyroidism', 'Obesity', 'Osteoarthritis', 'Rheumatoid Arthritis', 'Fibromyalgia', 'Osteoporosis', 'Chronic Back Pain', 'Anemia', 'Cancer', 'Breast Cancer', 'Colon Cancer', 'Prostate Cancer', 'Skin Cancer', 'Liver Disease', 'Hepatitis', 'HIV', 'Peripheral Neuropathy', 'Dementia', 'Parkinson Disease', 'Glaucoma', 'Macular Degeneration', 'Seasonal Allergies', 'Eczema', 'Psoriasis', 'Gout']


def patient_history_bundle(chart_number: str) -> Dict[str, Any]:
    sql = r"""
    SELECT json_build_object(
      'patient_id', p.id::text,
      'conditions',
        COALESCE((
          SELECT json_agg(
            json_build_object(
              'condition_name', condition_name,
              'current_flag', current_flag,
              'past_flag', past_flag,
              'family_history_flag', family_history_flag,
              'notes', notes
            )
            ORDER BY condition_name
          )
          FROM callcare.patient_conditions c
          WHERE c.patient_id = p.id
            AND c.archived_at IS NULL
        ), '[]'::json)
    )
    FROM callcare.patients p
    WHERE p.chart_number = NULLIF(:'CHART_NUMBER', '')
    LIMIT 1;
    """
    out = run_psql(sql, {"CHART_NUMBER": chart_number})
    return json.loads(out) if out else {}


def save_patient_history(chart_number: str, form: Dict[str, Any], actor_type: str = "patient") -> None:
    bundle = patient_history_bundle(chart_number)
    patient_id = safe_str(bundle.get("patient_id"))

    if not patient_id:
        return

    existing = {
        safe_str(x.get("condition_name")).lower(): x
        for x in (bundle.get("conditions") or [])
    }

    rows = []

    for cond in COMMON_HISTORY_CONDITIONS:
        key = cond.lower().replace(" ", "_")

        current_flag = safe_str(form.get(f"{key}_current")).lower() == "on"
        past_flag = safe_str(form.get(f"{key}_past")).lower() == "on"
        family_flag = safe_str(form.get(f"{key}_family")).lower() == "on"

        if current_flag or past_flag or family_flag:
            rows.append({
                "condition_name": cond,
                "current_flag": current_flag,
                "past_flag": past_flag,
                "family_history_flag": family_flag,
                "notes": "",
            })

    other_text = safe_str(form.get("other_conditions"))

    if other_text:
        for line in other_text.splitlines():
            line = safe_str(line)
            if not line:
                continue

            rows.append({
                "condition_name": line,
                "current_flag": True,
                "past_flag": False,
                "family_history_flag": False,
                "notes": "other_condition_writein",
            })

    sql_delete = r"""
    UPDATE callcare.patient_conditions
    SET archived_at = now()
    WHERE patient_id = NULLIF(:'PATIENT_ID', '')::uuid
      AND archived_at IS NULL;
    """

    run_psql(sql_delete, {"PATIENT_ID": patient_id})

    for row in rows:
        sql_insert = r"""
        INSERT INTO callcare.patient_conditions (
          id,
          patient_id,
          condition_name,
          current_flag,
          past_flag,
          family_history_flag,
          notes,
          source,
          verification_status,
          created_at,
          updated_at
        )
        VALUES (
          gen_random_uuid(),
          NULLIF(:'PATIENT_ID', '')::uuid,
          :'CONDITION_NAME',
          CASE WHEN :'CURRENT_FLAG' = 'true' THEN true ELSE false END,
          CASE WHEN :'PAST_FLAG' = 'true' THEN true ELSE false END,
          CASE WHEN :'FAMILY_FLAG' = 'true' THEN true ELSE false END,
          NULLIF(:'NOTES', ''),
          'patient_portal',
          'patient_reported',
          now(),
          now()
        );
        """

        run_psql(
            sql_insert,
            {
                "PATIENT_ID": patient_id,
                "CONDITION_NAME": row["condition_name"],
                "CURRENT_FLAG": str(row["current_flag"]).lower(),
                "PAST_FLAG": str(row["past_flag"]).lower(),
                "FAMILY_FLAG": str(row["family_history_flag"]).lower(),
                "NOTES": row["notes"],
            },
        )

    audit_sql = r"""
    INSERT INTO callcare.audit_events (
      id,
      actor_type,
      actor_id,
      patient_id,
      encounter_id,
      event_type,
      event_json,
      created_at
    )
    VALUES (
      gen_random_uuid(),
      :'ACTOR_TYPE',
      NULL,
      NULLIF(:'PATIENT_ID', '')::uuid,
      NULL,
      'patient_history_updated',
      jsonb_build_object(
        'condition_count', :'COUNT',
        'source', 'patient_portal'
      ),
      now()
    );
    """

    run_psql(
        audit_sql,
        {
            "ACTOR_TYPE": actor_type,
            "PATIENT_ID": patient_id,
            "COUNT": str(len(rows)),
        },
    )
