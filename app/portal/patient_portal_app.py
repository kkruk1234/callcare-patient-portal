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

PHONE_NUMBER_DISPLAY = "(844) 660-6064"
PHONE_NUMBER_TEL = "8446606064"

RURAL_GA_COUNTIES = sorted({
    "Appling","Atkinson","Bacon","Baker","Banks","Barrow","Bartow","Ben Hill","Berrien","Bleckley",
    "Brantley","Brooks","Bryan","Burke","Butts","Calhoun","Camden","Candler","Carroll","Catoosa",
    "Charlton","Chattooga","Chattahoochee","Cherokee","Clarke","Clay","Clinch","Coffee","Colquitt",
    "Cook","Coweta","Crawford","Crisp","Dade","Dawson","Decatur","Dodge","Dooly","Dougherty",
    "Early","Echols","Effingham","Elbert","Emanuel","Evans","Fannin","Fayette","Floyd","Franklin",
    "Gilmer","Glascock","Glynn","Gordon","Grady","Greene","Habersham","Hall","Hancock","Haralson",
    "Harris","Hart","Heard","Henry","Houston","Irwin","Jackson","Jasper","Jeff Davis","Jefferson",
    "Jenkins","Johnson","Jones","Lanier","Lamar","Laurens","Lee","Liberty","Lincoln","Long",
    "Lowndes","Lumpkin","Macon","Madison","Marion","McDuffie","McIntosh","Meriwether","Miller",
    "Mitchell","Monroe","Montgomery","Morgan","Murray","Newton","Oconee","Oglethorpe","Paulding",
    "Peach","Pickens","Pierce","Pike","Polk","Pulaski","Putnam","Quitman","Rabun","Randolph",
    "Rockdale","Schley","Screven","Seminole","Spalding","Stephens","Stewart","Sumter","Talbot",
    "Taliaferro","Tattnall","Taylor","Telfair","Terrell","Thomas","Tift","Toombs","Towns","Treutlen",
    "Troup","Turner","Twiggs","Union","Upson","Walker","Walton","Ware","Warren","Washington",
    "Wayne","Webster","White","Whitfield","Wilkes","Wilkinson","Wilcox","Worth","Wheeler"
})

ALL_GA_COUNTIES = sorted({
    "Appling","Atkinson","Bacon","Baker","Baldwin","Banks","Barrow","Bartow","Ben Hill","Berrien",
    "Bibb","Bleckley","Brantley","Brooks","Bryan","Bulloch","Burke","Butts","Calhoun","Camden",
    "Candler","Carroll","Catoosa","Charlton","Chatham","Chattahoochee","Chattooga","Cherokee",
    "Clarke","Clay","Clayton","Clinch","Cobb","Coffee","Colquitt","Columbia","Cook","Coweta",
    "Crawford","Crisp","Dade","Dawson","Decatur","DeKalb","Dodge","Dooly","Dougherty","Douglas",
    "Early","Echols","Effingham","Elbert","Emanuel","Evans","Fannin","Fayette","Floyd","Forsyth",
    "Franklin","Fulton","Gilmer","Glascock","Glynn","Gordon","Grady","Greene","Gwinnett","Habersham",
    "Hall","Hancock","Haralson","Harris","Hart","Heard","Henry","Houston","Irwin","Jackson",
    "Jasper","Jeff Davis","Jefferson","Jenkins","Johnson","Jones","Lamar","Lanier","Laurens",
    "Lee","Liberty","Lincoln","Long","Lowndes","Lumpkin","McDuffie","McIntosh","Macon","Madison",
    "Marion","Meriwether","Miller","Mitchell","Monroe","Montgomery","Morgan","Murray","Muscogee",
    "Newton","Oconee","Oglethorpe","Paulding","Peach","Pickens","Pierce","Pike","Polk","Pulaski",
    "Putnam","Quitman","Rabun","Randolph","Richmond","Rockdale","Schley","Screven","Seminole",
    "Spalding","Stephens","Stewart","Sumter","Talbot","Taliaferro","Tattnall","Taylor","Telfair",
    "Terrell","Thomas","Tift","Toombs","Towns","Treutlen","Troup","Turner","Twiggs","Union",
    "Upson","Walker","Walton","Ware","Warren","Washington","Wayne","Webster","Wheeler","White",
    "Whitfield","Wilcox","Wilkes","Wilkinson","Worth"
})

ALL_COUNTY_OPTIONS = "".join(
    f"<option value='{html_escape(c)}'>{html_escape(c)}</option>" for c in ALL_GA_COUNTIES
)

def _serializer() -> URLSafeSerializer:
    secret = os.getenv("CALLCARE_PORTAL_SECRET", "").strip() or "callcare-dev-secret"
    return URLSafeSerializer(secret, salt="patient-portal-session")


def encounter_label(text: str) -> str:
    t = safe_str(text).strip().rstrip(".")
    lower = t.lower()
    for prefix in ("i have ", "i'm having ", "im having ", "i am having ", "my "):
        if lower.startswith(prefix):
            t = t[len(prefix):].strip()
            break
    if not t:
        return "Encounter"
    return t[:1].upper() + t[1:]


def shell(title: str, body: str) -> str:
    return f"""
    <html>
      <head>
        <title>{html_escape(title)}</title>
        <meta name="viewport" content="width=device-width, initial-scale=1" />
        <style>
          :root {{
            --ink: #112b27;
            --muted: #274641;
            --card: rgba(255,255,255,0.94);
            --line: rgba(0,0,0,0.08);
            --accent: #1f8f80;
            --accent2: #67b9ae;
            --darklink: #0b0b0b;
            --hero-overlay: rgba(13, 40, 35, 0.52);
          }}
          * {{ box-sizing: border-box; }}
          body {{
            margin: 0;
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Arial, sans-serif;
            color: var(--ink);
            background:
              linear-gradient(rgba(255,255,255,0.12), rgba(255,255,255,0.12)),
              url('https://images.unsplash.com/photo-1506744038136-46273834b3fb?auto=format&fit=crop&w=1800&q=80');
            background-size: cover;
            background-position: center;
            background-attachment: fixed;
          }}
          .wrap {{ max-width: 1240px; margin: 0 auto; padding: 28px; }}
          .hero {{
            position: relative;
            overflow: hidden;
            border-radius: 30px;
            padding: 40px 42px;
            box-shadow: 0 24px 60px rgba(16,38,35,0.22);
            margin-bottom: 24px;
            background:
              linear-gradient(var(--hero-overlay), var(--hero-overlay)),
              url('https://images.unsplash.com/photo-1506744038136-46273834b3fb?auto=format&fit=crop&w=1800&q=80');
            background-size: cover;
            background-position: center;
            color: white;
          }}
          .hero h1 {{
            margin: 0 0 10px 0;
            font-size: 56px;
            line-height: 1;
            letter-spacing: -0.03em;
          }}
          .hero .sub {{
            margin: 0;
            font-size: 19px;
            line-height: 1.55;
            max-width: 860px;
            opacity: 0.98;
          }}
          .phone-wrap {{
            margin-top: 24px;
            display: inline-flex;
            flex-direction: column;
            gap: 8px;
            background: rgba(255,255,255,0.12);
            padding: 18px 22px;
            border-radius: 18px;
            backdrop-filter: blur(4px);
          }}
          .phone-label {{
            font-size: 12px;
            text-transform: uppercase;
            letter-spacing: 0.08em;
            opacity: 0.95;
          }}
          .phone {{
            font-size: 34px;
            font-weight: 900;
            color: white;
            text-decoration: none;
          }}
          .nav-strip {{
            display: flex;
            gap: 14px;
            flex-wrap: wrap;
            margin-bottom: 24px;
          }}
          .nav-pill, .top-pill {{
            display: inline-block;
            padding: 12px 16px;
            border-radius: 999px;
            background: rgba(255,255,255,0.92);
            border: 1px solid var(--line);
            box-shadow: 0 8px 20px rgba(18,60,55,0.08);
            color: var(--darklink) !important;
            text-decoration: none;
            font-weight: 800;
          }}
          .grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
            gap: 22px;
          }}
          .card {{
            background: var(--card);
            backdrop-filter: blur(6px);
            border-radius: 24px;
            padding: 24px;
            border: 1px solid var(--line);
            box-shadow: 0 12px 30px rgba(18,60,55,0.10);
          }}
          .card h2, .card h3 {{ margin-top: 0; }}
          .card p {{ line-height: 1.55; }}
          .card.equal-card {{
            display: flex;
            flex-direction: column;
            min-height: 250px;
          }}
          .card.equal-card .cta,
          .card.equal-card .cta.secondary {{
            margin-top: auto;
            align-self: flex-start;
          }}
          .cta {{
            display: inline-block;
            margin-top: 14px;
            padding: 12px 16px;
            border-radius: 12px;
            background: linear-gradient(135deg, var(--accent), var(--accent2));
            color: white;
            text-decoration: none;
            font-weight: 800;
          }}
          .cta.secondary {{
            background: rgba(255,255,255,0.92);
            color: var(--ink);
            border: 1px solid var(--line);
          }}
          .meta-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
            gap: 14px;
            margin-bottom: 20px;
          }}
          .metric {{
            background: rgba(255,255,255,0.78);
            border-radius: 16px;
            padding: 14px;
            border: 1px solid var(--line);
          }}
          .metric .label {{ font-size: 12px; color: var(--muted); text-transform: uppercase; }}
          .metric .value {{ margin-top: 4px; font-size: 16px; font-weight: 700; }}
          input, select {{
            width: 100%;
            padding: 12px;
            border-radius: 12px;
            border: 1px solid var(--line);
            margin-top: 6px;
            background: rgba(255,255,255,0.97);
          }}
          label {{ display: block; margin-top: 12px; font-weight: 700; }}
          button {{
            border: 0;
            background: linear-gradient(135deg, var(--accent), var(--accent2));
            color: white;
            padding: 12px 16px;
            border-radius: 12px;
            margin-top: 16px;
            font-weight: 800;
            cursor: pointer;
            box-shadow: 0 8px 20px rgba(47,158,143,0.25);
          }}
          table {{ width: 100%; border-collapse: collapse; }}
          th, td {{ padding: 12px; border-bottom: 1px solid var(--line); text-align: left; }}
          th {{ font-size: 12px; text-transform: uppercase; color: var(--muted); }}
          a {{ color: var(--accent); text-decoration: none; }}
          .top-links {{
            display: flex;
            gap: 12px;
            flex-wrap: wrap;
            margin: 6px 0 20px 0;
          }}
          .readonly {{
            border-radius: 14px;
            padding: 14px;
            background: rgba(255,255,255,0.90);
            border: 1px solid var(--line);
            white-space: pre-wrap;
          }}
          .detail-list {{ margin: 0; padding-left: 18px; }}
          .notice {{
            margin-top: 14px;
            padding: 12px 14px;
            border-radius: 12px;
            background: rgba(255,255,255,0.90);
            border: 1px solid var(--line);
            color: var(--ink);
            line-height: 1.55;
          }}
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
async def home(request: Request) -> str:
    sess = _current_session(request)
    portal_link = (
        "<a class='cta' href='/portal/dashboard'>Go to Patient Portal</a>"
        if sess else
        "<a class='cta' href='/portal/login'>Go to Patient Portal</a>"
    )
    return shell(
        "CallCare",
        f"""
        <div class="hero">
          <h1>CallCare</h1>
          <p class="sub">
            Telephone-first medical care for rural Georgia residents. CallCare is designed for people who may face
            barriers to broadband access, transportation, or nearby clinician availability. Patients call one number,
            complete a guided intake, and receive physician-reviewed recommendations with follow-up access through the patient portal.
          </p>
          <div class="phone-wrap">
            <div class="phone-label">CallCare Phone Line</div>
            <a class="phone" href="tel:{PHONE_NUMBER_TEL}">{PHONE_NUMBER_DISPLAY}</a>
          </div>
        </div>

        <div class="nav-strip">
          <a class="nav-pill" href="tel:{PHONE_NUMBER_TEL}">Call Now</a>
          <a class="nav-pill" href="/signup">Sign Up for Service</a>
          <a class="nav-pill" href="/portal/login">Patient Portal</a>
        </div>

        <div class="grid">
          <div class="card equal-card">
            <h2>One number for care</h2>
            <p>
              Patients can call <strong>{PHONE_NUMBER_DISPLAY}</strong> to complete intake, receive physician-reviewed recommendations,
              and learn whether medication or additional follow-up was advised.
            </p>
            <a class="cta" href="tel:{PHONE_NUMBER_TEL}">Call {PHONE_NUMBER_DISPLAY}</a>
          </div>

          <div class="card equal-card">
            <h2>Sign up for service</h2>
            <p>
              Start enrollment by confirming you live in an eligible rural Georgia county before completing your chart setup and service registration.
            </p>
            <a class="cta" href="/signup">Start Sign Up</a>
          </div>

          <div class="card equal-card">
            <h2>Already a patient?</h2>
            <p>
              Use the patient portal to review signed physician notes, pharmacy information, delivery status, and encounter history.
            </p>
            {portal_link}
          </div>
        </div>
        """,
    )


@app.get("/signup", response_class=HTMLResponse)
async def signup_page() -> str:
    return shell(
        "CallCare Sign Up",
        f"""
        <div class="hero">
          <h1>Sign Up for CallCare</h1>
          <p class="sub">First, confirm that you live in an eligible rural Georgia county.</p>
        </div>

        <div class="top-links"><a class="top-pill" href="/">Back to Home</a></div>

        <div class="card" style="max-width:760px;margin-top:20px;">
          <h2 style="margin-top:0;">Eligibility Screen</h2>
          <form method="post" action="/signup">
            <label>Legal First Name</label>
            <input name="first_name" autocomplete="off" />
            <label>Legal Last Name</label>
            <input name="last_name" autocomplete="off" />
            <label>Email</label>
            <input name="email" type="email" autocomplete="off" />
            <label>Georgia County</label>
            <select name="county">
              <option value="">Select your county</option>
              {ALL_COUNTY_OPTIONS}
            </select>
            <button type="submit">Continue</button>
          </form>
        </div>
        """,
    )


@app.post("/signup", response_class=HTMLResponse)
async def signup_submit(
    first_name: str = Form(...),
    last_name: str = Form(...),
    email: str = Form(...),
    county: str = Form(...),
) -> str:
    county_clean = safe_str(county)
    eligible = county_clean in RURAL_GA_COUNTIES

    if eligible:
        message = f"""
        <div class="notice">
          <strong>Eligible county confirmed.</strong><br />
          {html_escape(county_clean)} is currently accepted for CallCare enrollment.
        </div>
        <div style="margin-top:16px;">
          <a class="cta" href="/portal/login">Go to Patient Portal</a>
          <a class="cta secondary" href="tel:{PHONE_NUMBER_TEL}">Call {PHONE_NUMBER_DISPLAY}</a>
        </div>
        """
    else:
        message = f"""
        <div class="notice">
          <strong>Not currently eligible through this screen.</strong><br />
          You selected {html_escape(county_clean or "no county")}. If you believe this is an error, call {PHONE_NUMBER_DISPLAY}.
        </div>
        <div style="margin-top:16px;">
          <a class="cta" href="tel:{PHONE_NUMBER_TEL}">Call {PHONE_NUMBER_DISPLAY}</a>
          <a class="cta secondary" href="/signup">Try Again</a>
        </div>
        """

    return shell(
        "CallCare Eligibility Result",
        f"""
        <div class="hero">
          <h1>CallCare Eligibility Result</h1>
          <p class="sub">{html_escape(first_name)} {html_escape(last_name)}</p>
        </div>

        <div class="top-links"><a class="top-pill" href="/">Back to Home</a></div>

        <div class="card" style="max-width:760px;margin-top:20px;">
          {message}
        </div>
        """,
    )


@app.get("/portal/login", response_class=HTMLResponse)
async def login_page() -> str:
    return shell(
        "CallCare Patient Portal",
        """
        <div class="hero">
          <h1>Patient Portal</h1>
          <p class="sub">Review signed physician notes, medication status, and preferred pharmacy information.</p>
        </div>

        <div class="top-links"><a class="top-pill" href="/">Back to Home</a></div>

        <div class="card" style="max-width:700px;margin:20px auto 0 auto;">
          <h2 style="margin-top:0;">Log In</h2>
          <form method="post" action="/portal/login" autocomplete="off">
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


@app.post("/portal/login")
async def login(
    first_name: str = Form(...),
    last_name: str = Form(...),
    dob: str = Form(...),
    password: str = Form(...),
) -> RedirectResponse:
    verified = verify_portal_login(first_name, last_name, dob, password)
    if not verified:
        return RedirectResponse(url="/portal/login", status_code=303)

    token = _serializer().dumps(
        {
            "chart_number": safe_str(verified.get("chart_number")),
            "patient_name": safe_str(verified.get("patient_name")),
        }
    )

    response = RedirectResponse(url="/portal/dashboard", status_code=303)
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


@app.get("/portal/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request) -> str:
    sess = _require_session(request)
    chart_number = sess["chart_number"]

    group = signed_patient_group(chart_number)
    if not group:
        return shell(
            "CallCare Patient Portal",
            """
            <div class="hero"><h1>Patient Portal</h1><p class="sub">No signed notes are available yet.</p></div>
            <div class="top-links"><a class="top-pill" href="/">Home</a><a class="top-pill" href="/logout">Log out</a></div>
            """,
        )

    patient_ctx = group["patient_ctx"] or {}
    encounters = group["encounters"]

    rows = []
    for enc in encounters:
        enc_ctx = enc.get("patient_ctx") or {}
        rows.append(
            f"<tr>"
            f"<td><a href='/portal/encounter/{html_escape(enc['packet_id'])}'>{html_escape(encounter_label(safe_str(enc_ctx.get('chief_complaint')) or 'Encounter'))}</a></td>"
            f"<td>{html_escape(safe_str(enc_ctx.get('encounter_started_at')) or safe_str(enc.get('created_at')))}</td>"
            f"<td>{html_escape(safe_str((enc.get('meta') or {}).get('prescription_status')))}</td>"
            f"<td>{html_escape(safe_str((enc.get('meta') or {}).get('note_sent')))}</td>"
            f"</tr>"
        )

    return shell(
        "CallCare Patient Portal",
        f"""
        <div class="hero">
          <h1>Patient Portal</h1>
          <p class="sub">Welcome back, {html_escape(patient_ctx.get('patient_name'))}.</p>
        </div>

        <div class="top-links">
          <a class="top-pill" href="/">Home</a>
          <a class="top-pill" href="/logout">Log out</a>
        </div>

        <div class="card" style="margin-top:20px;">
          <div class="meta-grid">
            <div class="metric"><div class="label">Patient</div><div class="value">{html_escape(patient_ctx.get('patient_name'))}</div></div>
            <div class="metric"><div class="label">Chart #</div><div class="value">{html_escape(patient_ctx.get('chart_number'))}</div></div>
            <div class="metric"><div class="label">Date of Birth</div><div class="value">{html_escape(patient_ctx.get('date_of_birth'))}</div></div>
            <div class="metric"><div class="label">Preferred Pharmacy</div><div class="value">{html_escape(safe_str((patient_ctx.get('preferred_pharmacy') or {}).get('name')) or 'On file')}</div></div>
          </div>
        </div>

        <div class="card" style="margin-top:20px;">
          <h2 style="margin-top:0;">Signed Encounters</h2>
          <table>
            <thead>
              <tr>
                <th>Encounter</th>
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


@app.get("/portal/encounter/{packet_id}", response_class=HTMLResponse)
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
          <h1>{html_escape(encounter_label(patient_ctx.get('chief_complaint')))}</h1>
          <p class="sub">Signed physician-reviewed note and treatment information.</p>
        </div>

        <div class="top-links">
          <a class="top-pill" href="/portal/dashboard">Back to Dashboard</a>
          <a class="top-pill" href="/logout">Log out</a>
        </div>

        <div class="card" style="margin-top:20px;">
          <div class="meta-grid">
            <div class="metric"><div class="label">Patient</div><div class="value">{html_escape(patient_ctx.get('patient_name'))}</div></div>
            <div class="metric"><div class="label">Chart #</div><div class="value">{html_escape(patient_ctx.get('chart_number'))}</div></div>
            <div class="metric"><div class="label">Date of Birth</div><div class="value">{html_escape(patient_ctx.get('date_of_birth'))}</div></div>
            <div class="metric"><div class="label">Sex at Birth</div><div class="value">{html_escape(patient_ctx.get('sex_at_birth'))}</div></div>
            <div class="metric"><div class="label">Chief Complaint</div><div class="value">{html_escape(encounter_label(patient_ctx.get('chief_complaint')))}</div></div>
            <div class="metric"><div class="label">Prescription Status</div><div class="value">{html_escape(meta.get('prescription_status'))}</div></div>
          </div>
        </div>

        <div class="card" style="margin-top:20px;">
          <h2 style="margin-top:0;">Preferred Pharmacy</h2>
          {pharmacy_html}
        </div>

        <div class="card" style="margin-top:20px;">
          <h2 style="margin-top:0;">Allergies</h2>
          {allergies_html}
        </div>

        <div class="card" style="margin-top:20px;">
          <h2 style="margin-top:0;">Signed Clinical Note</h2>
          <div class="readonly">{html_escape(signed_note)}</div>
        </div>

        {addenda_html and f"<div class='card' style='margin-top:20px;'>{addenda_html}</div>" or ""}

        <div class="card" style="margin-top:20px;">
          <h2 style="margin-top:0;">Physician Comments on Spoken Summary</h2>
          <div class="readonly">{html_escape(spoken_comments or 'No additional physician comments.')}</div>
        </div>
        """,
    )
