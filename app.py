import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import gspread
from google.oauth2.service_account import Credentials
import psycopg2
from psycopg2.extras import RealDictCursor
import json

st.set_page_config(page_title="Student Progression & Portfolio DB", layout="wide")

# --- DATABASE CONNECTION ---
# --- DATABASE CONNECTION ---
def get_db_connection():
    return psycopg2.connect(
        dbname=st.secrets["postgres"]["dbname"],
        user=st.secrets["postgres"]["user"],
        password=st.secrets["postgres"]["password"],
        host=st.secrets["postgres"]["host"],
        port=st.secrets["postgres"]["port"],
        sslmode="require"
    )
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
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    
    cur.execute("SELECT * FROM students WHERE full_name ILIKE %s;", (f"%{search_query}%",))
    students = cur.fetchall()
    
    if not students:
        st.warning("No students found matching that name.")
    else:
        student = students[0]
        st.success(f"Loaded Profile: **{student['full_name']}** (ID: `{student['student_id']}`)")
        
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Secondary (10th)", f"{student['secondary_percentage']}%")
        m2.metric("Higher Sec (12th)", f"{student['higher_secondary_percentage']}%")
        m3.metric("Admission Year", student['admission_year'])
        m4.metric("Contact Email", student['email'])

        # Fetch Academic Progression
        cur.execute("""
            SELECT semester_num, academic_year, sgpa, cgpa, is_drop_year, drop_reason
            FROM semester_progress
            WHERE student_id = %s
            ORDER BY semester_num ASC;
        """, (student['student_id'],))
        prog_records = cur.fetchall()
        df_prog = pd.DataFrame(prog_records)

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
            cur.execute("""
                SELECT sp.semester_num, c.course_code, c.course_name, c.course_type, 
                       sm.internal_marks, sm.external_marks, sm.total_marks, sm.grade_point
                FROM semester_marks sm
                JOIN semester_progress sp ON sm.progress_id = sp.progress_id
                JOIN courses c ON sm.course_code = c.course_code
                WHERE sp.student_id = %s
                ORDER BY sp.semester_num, c.course_type DESC;
            """, (student['student_id'],))
            marks_data = cur.fetchall()
            st.dataframe(pd.DataFrame(marks_data), use_container_width=True)

        with tab_seminars:
            cur.execute("""
                SELECT p.title, p.organization_issuer, p.event_date, pc.sub_type AS scope, p.status
                FROM student_portfolios p
                JOIN portfolio_categories pc ON p.category_id = pc.category_id
                WHERE p.student_id = %s AND pc.main_type = 'Seminar';
            """, (student['student_id'],))
            sem_data = cur.fetchall()
            df_sem = pd.DataFrame(sem_data)
            if not df_sem.empty:
                col_i, col_e = st.columns(2)
                with col_i:
                    st.write("**Internal Seminars**")
                    st.dataframe(df_sem[df_sem['scope'] == 'Internal'], use_container_width=True)
                with col_e:
                    st.write("**External Seminars**")
                    st.dataframe(df_sem[df_sem['scope'] == 'External'], use_container_width=True)
            else:
                st.info("No seminar records found.")

        with tab_activities:
            cur.execute("""
                SELECT p.title, p.organization_issuer, p.event_date, pc.sub_type, p.status
                FROM student_portfolios p
                JOIN portfolio_categories pc ON p.category_id = pc.category_id
                WHERE p.student_id = %s AND pc.main_type = 'Activity';
            """, (student['student_id'],))
            act_data = cur.fetchall()
            st.dataframe(pd.DataFrame(act_data), use_container_width=True)

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
                    cur.execute("""
                        INSERT INTO semester_progress 
                        (student_id, semester_num, academic_year, sgpa, cgpa, is_drop_year, drop_reason)
                        VALUES (%s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (student_id, semester_num) 
                        DO UPDATE SET sgpa = EXCLUDED.sgpa, cgpa = EXCLUDED.cgpa;
                    """, (student['student_id'], f_sem, f_year, f_sgpa, f_cgpa, f_drop, f_reason))
                    conn.commit()
                    
                    sync_success = sync_to_google_sheet(
                        student['student_id'], student['full_name'], f_sgpa, f_cgpa, f_sem
                    )
                    if sync_success:
                        st.success("Successfully written to Database and synchronized with Google Sheets!")
                        st.rerun()
                except Exception as ex:
                    conn.rollback()
                    st.error(f"Database Error: {ex}")

    cur.close()
    conn.close()
