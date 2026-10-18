import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import gspread
from google.oauth2.service_account import Credentials
from sqlalchemy import create_engine, text
import json

st.set_page_config(page_title="Student Progression & Portfolio DB", layout="wide")

# --- BULLETPROOF DATABASE CONNECTION ENGINE ---
@st.cache_resource
def get_db_engine():
    raw_url = st.secrets["postgres"]["url"].strip()
    # Drop any driver suffix or old-style prefix, then force psycopg2
    for prefix in ("postgresql+psycopg://", "postgresql+psycopg2://", "postgres://", "postgresql://"):
        if raw_url.startswith(prefix):
            raw_url = raw_url[len(prefix):]
            break
    raw_url = "postgresql+psycopg2://" + raw_url
    # channel_binding can cause connection failures with psycopg2 on some setups
    raw_url = raw_url.replace("&channel_binding=require", "").replace("channel_binding=require&", "")
    return create_engine(raw_url, pool_pre_ping=True, pool_recycle=300
    )

def run_query(query_str, params=None):
    engine = get_db_engine()
    with engine.connect() as conn:
        return pd.read_sql(text(query_str), conn, params=params)

def execute_dml(dml_str, params=None):
    engine = get_db_engine()
    with engine.begin() as conn:
        conn.execute(text(dml_str), params or {})

# --- GOOGLE SHEETS SYNC LAYER ---
def sync_to_google_sheet(student_id, student_name, sgpa, cgpa, sem):
    try:
        scopes = [
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive"
        ]
        creds_dict = json.loads(st.secrets["gcp_service_account"]["json_key"])
        creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
        client = gspread.authorize(creds)
        
        sheet = client.open_by_key(st.secrets["google_sheets"]["sheet_id"]).sheet1
        sheet.append_row([student_id, student_name, int(sem), float(sgpa), float(cgpa), "SYNCED"])
        return True
    except Exception as e:
        st.error(f"Spreadsheet Sync Failed: {e}")
        return False

# --- UI HEADER & SEARCH ---
st.title("🎓 Student Progression & Portfolio Intelligence System")
st.markdown("---")

search_col, _ = st.columns([2, 1])
with search_col:
    search_query = st.text_input("🔍 Search Student by Full Name:")

if search_query:
    try:
        students_df = run_query(
            "SELECT * FROM students WHERE full_name ILIKE :q",
            {"q": f"%{search_query}%"}
        )
    except Exception as err:
        st.error(f"Database Connection Error: {err}")
        st.stop()

    if students_df.empty:
        st.warning("No students found matching that name.")
    else:
        student = students_df.iloc[0]
        st.success(f"Loaded Profile: **{student['full_name']}** (ID: `{student['student_id']}`)")
        
        # Profile Summary Metrics
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Secondary (10th)", f"{student['secondary_percentage']}%")
        m2.metric("Higher Sec (12th)", f"{student['higher_secondary_percentage']}%")
        m3.metric("Admission Year", int(student['admission_year']))
        m4.metric("Contact Email", student['email'])

        # Fetch Academic Progression
        df_prog = run_query(
            """
            SELECT semester_num, academic_year, sgpa, cgpa, is_drop_year, drop_reason
            FROM semester_progress
            WHERE student_id = :sid
            ORDER BY semester_num ASC
            """,
            {"sid": student['student_id']}
        )

        st.subheader("📈 Academic Progression & Gap Year Analysis")
        
        if not df_prog.empty:
            fig = go.Figure()
            
            # Normal Semesters
            normal_sems = df_prog[~df_prog['is_drop_year']]
            fig.add_trace(go.Scatter(
                x=normal_sems['semester_num'], 
                y=normal_sems['sgpa'],
                mode='lines+markers',
                name='SGPA Curve',
                line=dict(color='#2563EB', width=3),
                marker=dict(size=8)
            ))
            
            # Highlight Drop / Gap Years
            drop_sems = df_prog[df_prog['is_drop_year']]
            if not drop_sems.empty:
                fig.add_trace(go.Scatter(
                    x=drop_sems['semester_num'],
                    y=drop_sems['sgpa'],
                    mode='markers',
                    name='Gap/Drop Year',
                    marker=dict(color='#DC2626', size=14, symbol='x'),
                    text=drop_sems['drop_reason'],
                    hoverinfo='text+x+y'
                ))
            
            fig.update_layout(
                xaxis_title="Semester Number",
                yaxis_title="SGPA (Scale 0-10)",
                yaxis=dict(range=[0, 10]),
                template="plotly_white",
                height=380
            )
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.info("No progression history found.")

        # --- PORTFOLIO & COURSE SEGREGATION TABS ---
        st.subheader("📁 Segregated Records & Portfolios")
        tab_courses, tab_seminars, tab_activities = st.tabs([
            "📚 Semester Subjects & Labs",
            "🎤 Seminars (Internal vs External)", 
            "🏆 Activities (Technical / Co / Extra)"
        ])

        with tab_courses:
            marks_df = run_query(
                """
                SELECT sp.semester_num, c.course_code, c.course_name, c.course_type, 
                       sm.internal_marks, sm.external_marks, sm.total_marks, sm.grade_point
                FROM semester_marks sm
                JOIN semester_progress sp ON sm.progress_id = sp.progress_id
                JOIN courses c ON sm.course_code = c.course_code
                WHERE sp.student_id = :sid
                ORDER BY sp.semester_num, c.course_type DESC
                """,
                {"sid": student['student_id']}
            )
            st.dataframe(marks_df, use_container_width=True)

        with tab_seminars:
            sem_df = run_query(
                """
                SELECT p.title, p.organization_issuer, p.event_date, pc.sub_type AS scope, p.status
                FROM student_portfolios p
                JOIN portfolio_categories pc ON p.category_id = pc.category_id
                WHERE p.student_id = :sid AND pc.main_type = 'Seminar'
                """,
                {"sid": student['student_id']}
            )
            if not sem_df.empty:
                col_i, col_e = st.columns(2)
                with col_i:
                    st.write("**Internal Seminars**")
                    st.dataframe(sem_df[sem_df['scope'] == 'Internal'], use_container_width=True)
                with col_e:
                    st.write("**External Seminars**")
                    st.dataframe(sem_df[sem_df['scope'] == 'External'], use_container_width=True)
            else:
                st.info("No seminar records found.")

        with tab_activities:
            act_df = run_query(
                """
                SELECT p.title, p.organization_issuer, p.event_date, pc.sub_type, p.status
                FROM student_portfolios p
                JOIN portfolio_categories pc ON p.category_id = pc.category_id
                WHERE p.student_id = :sid AND pc.main_type = 'Activity'
                """,
                {"sid": student['student_id']}
            )
            st.dataframe(act_df, use_container_width=True)

        # --- CRUD & GOOGLE SHEETS SYNC SECTION ---
        st.markdown("---")
        st.subheader("➕ Record Semester Result & Trigger Spreadsheet Sync")
        with st.form("add_sem_result"):
            f_sem = st.number_input("Semester", min_value=1, max_value=8, step=1)
            f_year = st.text_input("Academic Year (e.g. 2024-2025)")
            f_sgpa = st.number_input("SGPA", min_value=0.0, max_value=10.0, step=0.01)
            f_cgpa = st.number_input("CGPA", min_value=0.0, max_value=10.0, step=0.01)
            f_drop = st.checkbox("Is Drop / Gap Year?")
            f_reason = st.text_input("Drop Reason (if applicable)")
            
            submit_btn = st.form_submit_button("Commit to DB & Push to Google Sheets")
            if submit_btn:
                try:
                    execute_dml(
                        """
                        INSERT INTO semester_progress 
                        (student_id, semester_num, academic_year, sgpa, cgpa, is_drop_year, drop_reason)
                        VALUES (:sid, :sem, :ay, :sgpa, :cgpa, :drop, :reason)
                        ON CONFLICT (student_id, semester_num) 
                        DO UPDATE SET sgpa = EXCLUDED.sgpa, cgpa = EXCLUDED.cgpa
                        """,
                        {
                            "sid": student['student_id'],
                            "sem": int(f_sem),
                            "ay": f_year,
                            "sgpa": float(f_sgpa),
                            "cgpa": float(f_cgpa),
                            "drop": f_drop,
                            "reason": f_reason
                        }
                    )
                    
                    sync_success = sync_to_google_sheet(
                        student['student_id'], student['full_name'], f_sgpa, f_cgpa, f_sem
                    )
                    if sync_success:
                        st.success("Successfully written to Database and synchronized with Google Sheets!")
                        st.rerun()
                except Exception as ex:
                    st.error(f"Database Error: {ex}")
