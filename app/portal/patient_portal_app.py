from __future__ import annotations

import os
from typing import Optional

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse
from itsdangerous import BadSignature, URLSafeSerializer

from app.portal.portal_common import (
    addendum_block,
    html_escape,
    packet_bundle_from_db,
    render_list_items,
    render_pharmacy,
    safe_str,
    signed_note_text,
    signed_patient_group,
    verify_portal_login,
)

app = FastAPI(title="CallCare Patient Portal")


def _serializer() -> URLSafeSerializer:
    secret = os.getenv("CALLCARE_PORTAL_SECRET", "").strip() or "callcare-dev-secret"
    return URLSafeSerializer(secret, salt="patient-portal-session")


def shell(title: str, body: str) -> str:
    return f"""
    <html>
      <head>
        <title>{html_escape(title)}</title>
        <style>
          :root {{
            --ink: #12332e;
            --muted: #5c7d78;
            --card: rgba(255,255,255,0.92);
            --line: rgba(0,0,0,0.08);
            --accent: #2f9e8f;
            --accent2: #7cc7be;
          }}
          * {{ box-sizing: border-box; }}
          body {{
            margin: 0;
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Arial, sans-serif;
            color: var(--ink);
            background:
              linear-gradient(rgba(255,255,255,0.18), rgba(255,255,255,0.18)),
              url('https://images.unsplash.com/photo-1506744038136-46273834b3fb?auto=format&fit=crop&w=1800&q=80');
            background-size: cover;
            background-position: center;
            background-attachment: fixed;
          }}
          .wrap {{ max-width: 1180px; margin: 0 auto; padding: 28px; }}
          .hero {{
            background: linear-gradient(135deg, rgba(47,158,143,0.95), rgba(124,199,190,0.9));
            color: white;
            border-radius: 26px;
            padding: 28px 32px;
            box-shadow: 0 20px 50px rgba(18, 60, 55, 0.18);
            margin-bottom: 24px;
          }}
          .hero h1 {{ margin: 0 0 6px 0; font-size: 34px; }}
          .hero p {{ margin: 0; font-size: 16px; opacity: 0.95; }}
          .card {{
            background: var(--card);
            backdrop-filter: blur(6px);
            border-radius: 22px;
            padding: 22px;
            border: 1px solid var(--line);
            box-shadow: 0 10px 30px rgba(18, 60, 55, 0.08);
          }}
          .meta-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
            gap: 14px;
            margin-bottom: 20px;
          }}
          .metric {{
            background: rgba(255,255,255,0.7);
            border-radius: 16px;
            padding: 14px;
            border: 1px solid var(--line);
          }}
          .metric .label {{ font-size: 12px; color: var(--muted); text-transform: uppercase; }}
          .metric .value {{ margin-top: 4px; font-size: 16px; font-weight: 600; }}
          input {{
            width: 100%;
            padding: 12px;
            border-radius: 12px;
            border: 1px solid var(--line);
            margin-top: 6px;
            background: rgba(255,255,255,0.9);
          }}
          label {{ display: block; margin-top: 12px; font-weight: 600; }}
          button {{
            border: 0;
            background: linear-gradient(135deg, var(--accent), var(--accent2));
            color: white;
            padding: 12px 16px;
            border-radius: 12px;
            margin-top: 16px;
            font-weight: 600;
            cursor: pointer;
            box-shadow: 0 8px 20px rgba(47,158,143,0.25);
          }}
          table {{ width: 100%; border-collapse: collapse; }}
          th, td {{ padding: 12px; border-bottom: 1px solid var(--line); }}
          th {{ font-size: 12px; text-transform: uppercase; color: var(--muted); }}
          a {{ color: var(--accent); text-decoration: none; }}
          .readonly {{
            border-radius: 14px;
            padding: 14px;
            background: rgba(255,255,255,0.85);
            border: 1px solid var(--line);
            white-space: pre-wrap;
          }}
          .detail-list {{ margin: 0; padding-left: 18px; }}
        </style>
      </head>
      <body>
        <div class="wrap">
          {body}
        </div>
      </body>
    </html>
    """


def _current_session(request: Request) -> Optional[dict]:
    token = request.cookies.get("callcare_patient_session", "")
    if not token:
        return None
    try:
        data = _serializer().loads(token)
        if not isinstance(data, dict):
            return None
        return data
    except BadSignature:
        return None


def _require_session(request: Request) -> dict:
    sess = _current_session(request)
    if not sess:
        raise HTTPException(status_code=401, detail="Not logged in")
    return sess


@app.get("/healthz")
async def healthz() -> PlainTextResponse:
    return PlainTextResponse("ok")


@app.get("/", response_class=HTMLResponse)
async def login_page() -> str:
    return shell(
        "CallCare Patient Portal",
        """
        <div class="hero">
          <h1>CallCare Patient Portal</h1>
          <p>Review your signed physician note, medication status, and preferred pharmacy.</p>
        </div>

        <div class="card" style="max-width:700px;margin:0 auto;">
          <h2 style="margin-top:0;">Log In</h2>
          <p>Please use your name, date of birth, and portal password.</p>
          <form method="post" action="/login" autocomplete="off">
            <label>First Name</label>
            <input name="first_name" autocomplete="off" autocapitalize="words" spellcheck="false" />
            <label>Last Name</label>
            <input name="last_name" autocomplete="off" autocapitalize="words" spellcheck="false" />
            <label>Date of Birth (YYYY-MM-DD)</label>
            <input name="dob" autocomplete="off" inputmode="numeric" spellcheck="false" />
            <label>Password</label>
            <input name="password" type="password" autocomplete="new-password" spellcheck="false" />
            <button type="submit">Log In</button>
          </form>
        </div>
        """,
    )


@app.post("/login")
async def login(
    first_name: str = Form(...),
    last_name: str = Form(...),
    dob: str = Form(...),
    password: str = Form(...),
) -> RedirectResponse:
    verified = verify_portal_login(first_name, last_name, dob, password)
    if not verified:
        return RedirectResponse(url="/", status_code=303)

    token = _serializer().dumps(
        {
            "chart_number": safe_str(verified.get("chart_number")),
            "patient_name": safe_str(verified.get("patient_name")),
        }
    )

    response = RedirectResponse(url="/dashboard", status_code=303)
    response.set_cookie(
        "callcare_patient_session",
        token,
        httponly=True,
        samesite="lax",
        path="/",
        secure=True,
    )
    return response


@app.get("/logout")
async def logout() -> RedirectResponse:
    response = RedirectResponse(url="/", status_code=303)
    response.delete_cookie("callcare_patient_session", path="/")
    return response


@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request) -> str:
    sess = _require_session(request)
    chart_number = sess["chart_number"]

    group = signed_patient_group(chart_number)
    if not group:
        return shell(
            "CallCare Patient Portal",
            """
            <div class="hero"><h1>CallCare Patient Portal</h1><p>No signed notes are available yet.</p></div>
            <p><a href="/logout">Log out</a></p>
            """,
        )

    patient_ctx = group["patient_ctx"] or {}
    encounters = group["encounters"]

    rows = []
    for enc in encounters:
        enc_ctx = enc.get("patient_ctx") or {}
        rows.append(
            f"<tr>"
            f"<td><a href='/encounter/{html_escape(enc['packet_id'])}'>{html_escape(safe_str(enc_ctx.get('chief_complaint')) or 'Encounter')}</a></td>"
            f"<td>{html_escape(safe_str(enc_ctx.get('encounter_started_at')) or safe_str(enc.get('created_at')))}</td>"
            f"<td>{html_escape(safe_str((enc.get('meta') or {}).get('prescription_status')))}</td>"
            f"<td>{html_escape(safe_str((enc.get('meta') or {}).get('note_sent')))}</td>"
            f"</tr>"
        )

    return shell(
        "CallCare Patient Portal",
        f"""
        <div class="hero">
          <h1>CallCare Patient Portal</h1>
          <p>Welcome back, {html_escape(patient_ctx.get('patient_name'))}.</p>
        </div>

        <p><a href="/logout">Log out</a></p>

        <div class="card">
          <div class="meta-grid">
            <div class="metric"><div class="label">Patient</div><div class="value">{html_escape(patient_ctx.get('patient_name'))}</div></div>
            <div class="metric"><div class="label">Chart #</div><div class="value">{html_escape(patient_ctx.get('chart_number'))}</div></div>
            <div class="metric"><div class="label">Date of Birth</div><div class="value">{html_escape(patient_ctx.get('date_of_birth'))}</div></div>
            <div class="metric"><div class="label">Preferred Pharmacy</div><div class="value">{html_escape(safe_str((patient_ctx.get('preferred_pharmacy') or {}).get('name')) or 'On file')}</div></div>
          </div>
        </div>

        <div class="card">
          <h2 style="margin-top:0;">Signed Encounters</h2>
          <table>
            <thead>
              <tr>
                <th>Chief Complaint</th>
                <th>Date / Time</th>
                <th>Prescription Status</th>
                <th>Delivery Status</th>
              </tr>
            </thead>
            <tbody>
              {''.join(rows)}
            </tbody>
          </table>
        </div>
        """,
    )


@app.get("/encounter/{packet_id}", response_class=HTMLResponse)
async def encounter_detail(packet_id: str, request: Request) -> str:
    sess = _require_session(request)
    chart_number = sess["chart_number"]

    bundle = packet_bundle_from_db(packet_id)
    if not bundle:
        raise HTTPException(status_code=404, detail="Encounter bundle not found")

    patient_ctx = bundle.get("patient_ctx") or {}
    if safe_str(patient_ctx.get("chart_number")) != chart_number:
        raise HTTPException(status_code=403, detail="Forbidden")

    meta = bundle["meta"]
    if not meta.get("signed"):
        raise HTTPException(status_code=403, detail="Only signed notes are viewable")

    packet = bundle["packet"]
    note_text = safe_str(packet.get("note_text"))
    signed_note = signed_note_text(note_text, meta)
    spoken_comments = safe_str(meta.get("spoken_summary_comments"))
    addenda = meta.get("addenda") or []

    pharmacy_html = render_pharmacy(patient_ctx.get("preferred_pharmacy") or {})
    allergies_html = render_list_items(
        patient_ctx.get("allergies") or [],
        ["allergen", "reaction", "severity"],
        "No allergy data on file.",
    )

    addenda_html = ""
    if addenda:
        addenda_html += "<h2>Addenda</h2>"
        for idx, add in enumerate(addenda, 1):
            addenda_html += f"<div class='readonly' style='margin-bottom:12px;'><strong>Addendum {idx}</strong>\n\n{html_escape(addendum_block(add))}</div>"

    return shell(
        "CallCare Patient Encounter",
        f"""
        <div class="hero">
          <h1>Encounter Details</h1>
          <p>Signed physician-reviewed note and treatment information.</p>
        </div>

        <p><a href="/dashboard">← Back to dashboard</a> | <a href="/logout">Log out</a></p>

        <div class="card">
          <div class="meta-grid">
            <div class="metric"><div class="label">Patient</div><div class="value">{html_escape(patient_ctx.get('patient_name'))}</div></div>
            <div class="metric"><div class="label">Chart #</div><div class="value">{html_escape(patient_ctx.get('chart_number'))}</div></div>
            <div class="metric"><div class="label">Date of Birth</div><div class="value">{html_escape(patient_ctx.get('date_of_birth'))}</div></div>
            <div class="metric"><div class="label">Sex at Birth</div><div class="value">{html_escape(patient_ctx.get('sex_at_birth'))}</div></div>
            <div class="metric"><div class="label">Chief Complaint</div><div class="value">{html_escape(patient_ctx.get('chief_complaint'))}</div></div>
            <div class="metric"><div class="label">Prescription Status</div><div class="value">{html_escape(meta.get('prescription_status'))}</div></div>
          </div>
        </div>

        <div class="card">
          <h2 style="margin-top:0;">Preferred Pharmacy</h2>
          {pharmacy_html}
        </div>

        <div class="card">
          <h2 style="margin-top:0;">Allergies</h2>
          {allergies_html}
        </div>

        <div class="card">
          <h2 style="margin-top:0;">Signed Clinical Note</h2>
          <div class="readonly">{html_escape(signed_note)}</div>
        </div>

        {addenda_html and f"<div class='card'>{addenda_html}</div>" or ""}

        <div class="card">
          <h2 style="margin-top:0;">Physician Comments on Spoken Summary</h2>
          <div class="readonly">{html_escape(spoken_comments or 'No additional physician comments.')}</div>
        </div>
        """,
    )
