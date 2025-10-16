from flask import Flask, request, render_template, redirect, url_for, session, make_response
from werkzeug.utils import secure_filename
from datetime import datetime, timedelta
import os
import pytz
import pandas as pd
from collections import defaultdict
import uuid
import firebase_admin
from firebase_admin import credentials, firestore, storage

# ————— Setup Firebase —————

# Path to your Firebase service account JSON key (adjust as deployed on Render)
FIREBASE_KEY_PATH = os.getenv('FIREBASE_KEY_PATH', '/etc/secrets/firebase-key.json')
# Your Firebase project ID
FIREBASE_PROJECT_ID = os.getenv('FIREBASE_PROJECT_ID', 'sean-app-50b58')

# Initialize Firebase app if not yet initialized
if not firebase_admin._apps:
    cred = credentials.Certificate(FIREBASE_KEY_PATH)
    firebase_admin.initialize_app(cred, {
        'projectId': FIREBASE_PROJECT_ID,
        'storageBucket': f'{FIREBASE_PROJECT_ID}.appspot.com',
    })

# Firestore client
db = firestore.client()
# Firebase Storage bucket
bucket = storage.bucket()

# ————— Flask setup —————

app = Flask(__name__)
app.secret_key = 'secret_key_everett-7714'

# Valid PINs
VALID_PINS = {
    "Tony": "1234",
    "Hector": "5678",
    "Dad": "1111",
    "Louis": "2222",
    "Admin": "0308",
    "Daniel": "3333",
}

PROJECTS = [
    "Garage Conversion", "Garage Conversion; electrical", "Bathroom Addition",
    "Bathroom Addition:Electrical", "Wall Division",
    "Complete Painting Exterior", "Garage: Drain/Hole for Water",
    "Garage: Slope Concrete (In & Out)", "Garage: Move Sensor + Add Side Latch",
    "Garage: Seal Cut Door Channel", "Garage: Close Gaps at Front",
    "Garage: Patch Concrete by Back Door", "Garage: Install Vent System",
    "Garage: Add Plug Spacers", "Garage:Fascia front ", "Kitchen: Install Trims",
    "Kitchen: Side Board on Cabinet", "Kitchen: Paint Skylight Area",
    "Front Door: Patch on Door", "Home Depot Run", "Dump Run"
]
COMPLETED_PROJECTS = [
    "Garage: Install Back Door", "Garage: Roof Leak (New & Old)",
    "Garage: Fix Outside Light Switches"
]
PROJECTS = [p for p in PROJECTS if p not in COMPLETED_PROJECTS]

pacific = pytz.timezone('US/Pacific')


# ————— Helper to load timelogs from Firestore —————

def load_timelogs_from_firestore():
    """Load all timelog entries from Firestore as list of dicts."""
    docs = db.collection('timelogs').stream()
    items = []
    for doc in docs:
        data = doc.to_dict()
        # Optionally include the document ID
        data['id'] = doc.id
        items.append(data)
    return items

# ————— Compute weekly summary & total hours from Firestore data —————

def get_weekly_summary():
    try:
        items = load_timelogs_from_firestore()
        if not items:
            return []
        df = pd.DataFrame(items)
        # Parse timestamps
        df['timestamp'] = pd.to_datetime(df['timestamp'], errors='coerce')
        df = df.dropna(subset=['timestamp'])
        if df.empty:
            return []
        # Localize/convert to Pacific
        if df['timestamp'].dt.tz is None or str(df['timestamp'].dtype) == "datetime64[ns]":
            df['timestamp'] = df['timestamp'].dt.tz_localize('US/Pacific')
        else:
            df['timestamp'] = df['timestamp'].dt.tz_convert('US/Pacific')
        # Filter to current week
        df['week'] = df['timestamp'].dt.isocalendar().week
        current_week = datetime.now(pacific).isocalendar().week
        df = df[df['week'] == current_week]
        if df.empty:
            return []
        # Format for display
        df['timestamp'] = df['timestamp'].dt.strftime('%Y-%m-%d %I:%M %p')
        df = df.sort_values(by=['user', 'timestamp'])
        return df.to_dict(orient='records')
    except Exception as e:
        print("Error in get_weekly_summary:", e)
        return []


def get_total_hours():
    try:
        items = load_timelogs_from_firestore()
        if not items:
            return {}
        df = pd.DataFrame(items)
        df['timestamp'] = pd.to_datetime(df['timestamp'], errors='coerce')
        df = df.dropna(subset=['timestamp'])
        if df.empty:
            return {}
        if df['timestamp'].dt.tz is None or str(df['timestamp'].dtype) == "datetime64[ns]":
            df['timestamp'] = df['timestamp'].dt.tz_localize('US/Pacific')
        else:
            df['timestamp'] = df['timestamp'].dt.tz_convert('US/Pacific')
        df['week'] = df['timestamp'].dt.isocalendar().week
        current_week = datetime.now(pacific).isocalendar().week
        df = df[df['week'] == current_week]
        if df.empty:
            return {}
        df = df.sort_values(by=['user', 'timestamp'])
        total_hours = {}
        for user in df['user'].unique():
            user_df = df[df['user'] == user]
            clocked_in = None
            total = pd.Timedelta(0)
            for _, row in user_df.iterrows():
                if row['action'].lower() == 'in':
                    clocked_in = row['timestamp']
                elif row['action'].lower() == 'out' and clocked_in:
                    total += row['timestamp'] - clocked_in
                    clocked_in = None
            if clocked_in:
                total += datetime.now(pacific) - clocked_in
            total_hours[user] = round(total.total_seconds() / 3600, 2)
        return total_hours
    except Exception as e:
        print("Error in get_total_hours:", e)
        return {}


# ————— Routes —————

@app.route('/')
def home():
    ip = request.headers.get('X-Forwarded-For', request.remote_addr)
    return render_template('index.html', ip=ip, projects=PROJECTS)


@app.route('/dashboard', methods=['GET', 'POST'])
def dashboard():
    try:
        pacific = pytz.timezone('America/Los_Angeles')
        today = datetime.now(pacific)
        today_date = today.strftime('%B %d, %Y')
        limit = request.args.get('limit', default=10, type=int)
        selected_week = request.args.get('week')
        selected_date = request.args.get('date')
        selected_user = request.args.get('user')
        selected_project = request.args.get('project', '')

        if selected_user == "All":
            selected_user = None
        if selected_project == "All":
            selected_project = None

        if request.method == 'POST':
            pin = request.form.get('pin')
            for name, valid_pin in VALID_PINS.items():
                if pin == valid_pin:
                    session['user'] = name
                    break
            else:
                return render_template("403.html"), 403

        logged_in_user = session.get('user')
        is_admin = (logged_in_user == "Admin")

        if selected_week:
            start_of_week = pacific.localize(datetime.strptime(selected_week, "%Y-%m-%d"))
        else:
            weekday = today.weekday()
            days_since_sunday = (weekday + 1) % 7
            start_of_week = today - timedelta(days=days_since_sunday)

        end_of_week = start_of_week + timedelta(days=6)

        # Load Firestore timelogs
        items = load_timelogs_from_firestore()
        df_full = pd.DataFrame(items) if items else pd.DataFrame([])

        if df_full.empty:
            entries = []
            users = []
        else:
            df_full['timestamp'] = pd.to_datetime(df_full['timestamp'], errors='coerce')
            df_full = df_full.dropna(subset=['timestamp'])
            if str(df_full['timestamp'].dtype) == "datetime64[ns]":
                df_full['timestamp'] = df_full['timestamp'].dt.tz_localize('US/Pacific', ambiguous='NaT', nonexistent='NaT')
            else:
                df_full['timestamp'] = df_full['timestamp'].dt.tz_convert('US/Pacific')
            df_full['project'] = df_full['project'].fillna("-").astype(str)
            df_full['user'] = df_full['user'].astype(str).str.strip().str.title()
            weekly_df = df_full[(df_full['timestamp'] >= start_of_week) & (df_full['timestamp'] <= end_of_week)]

            sessions = []
            for contractor, group in weekly_df.groupby('user'):
                if selected_user and selected_user != "Admin" and contractor != selected_user:
                    continue
                group = group.sort_values('timestamp')
                in_time = None
                for _, row in group.iterrows():
                    if selected_project and row['project'] != selected_project:
                        continue
                    if row['action'] == 'in':
                        in_time = row['timestamp']
                    elif row['action'] == 'out' and in_time:
                        duration = row['timestamp'] - in_time
                        sessions.append({
                            'date': in_time.date(),
                            'duration': duration.total_seconds() / 60,
                            'contractor': contractor
                        })
                        in_time = None

            daily_minutes = defaultdict(float)
            for entry in sessions:
                key = (entry['date'], entry['contractor'])
                daily_minutes[key] += entry['duration']
            daily_summary = []
            for (date, contractor), minutes in sorted(daily_minutes.items(), reverse=True):
                hours = int(minutes // 60)
                mins = int(minutes % 60)
                daily_summary.append({
                    'date': date,
                    'contractor': contractor,
                    'formatted': f"{hours}h {mins}m"
                })

            weekly_totals = defaultdict(float)
            for entry in sessions:
                weekly_totals[entry['contractor']] += entry['duration']
            weekly_summary = []
            for contractor in VALID_PINS.keys():
                minutes = weekly_totals.get(contractor, 0)
                hours = int(minutes // 60)
                mins = int(minutes % 60)
                weekly_summary.append({
                    'contractor': contractor,
                    'formatted': f"{hours}h {mins}m"
                })

            grand_total_minutes = sum(weekly_totals.values())
            grand_total_hours = round(grand_total_minutes / 60, 2)
            remaining_hours = round(80 - grand_total_hours, 2)

            df = weekly_df.copy()
            if selected_user and selected_user != "Admin":
                df = df[df['user'] == selected_user]
            if selected_project:
                df = df[df['project'] == selected_project]

            df['timestamp'] = df['timestamp'].dt.strftime('%Y-%m-%d %I:%M %p')
            df['raw_timestamp'] = pd.to_datetime(df['timestamp'], errors='coerce').dt.strftime('%Y-%m-%d %H:%M:%S')
            entries = df.to_dict(orient='records')[:limit]
            users = sorted(set(df_full['user']))

        return render_template('dashboard.html',
                               data=entries,
                               users=users,
                               selected_user=selected_user,
                               total_hours=get_total_hours(),
                               is_admin=is_admin,
                               projects=PROJECTS,
                               completed_projects=COMPLETED_PROJECTS,
                               selected_project=selected_project,
                               selected_date=selected_date,
                               daily_summary=daily_summary,
                               grand_total_hours=round(sum(get_total_hours().values()), 2),
                               weekly_summary=weekly_summary,
                               limit=limit,
                               remaining_hours=remaining_hours,
                               start_of_week=start_of_week.date(),
                               today_date=today_date,
                               week_ending=end_of_week.date())
    except Exception as e:
        import traceback
        traceback.print_exc()
        return f"<h3>Dashboard error: {e}</h3>", 500


@app.route('/delete', methods=['POST'])
def delete_entry():
    logged_in_user = session.get('user')
    if logged_in_user != "Admin":
        return "⛔ Unauthorized. Only Admin can delete entries.", 403
    try:
        entry_id = request.form.get('id')
        # Delete from Firestore
        db.collection('timelogs').document(entry_id).delete()
        return redirect(url_for('dashboard'))
    except Exception as e:
        return f"Error deleting entry: {e}", 500


@app.route('/edit/<entry_id>', methods=['GET', 'POST'])
def edit_entry(entry_id):
    logged_in_user = session.get('user')
    if logged_in_user != "Admin":
        return "⛔ Unauthorized. Only Admin can edit entries.", 403

    doc = db.collection('timelogs').document(entry_id).get()
    if not doc.exists:
        return f"<h3>No entry found for ID: {entry_id}</h3>", 404
    entry = doc.to_dict()

    if request.method == 'POST':
        updated_data = {
            'user': request.form['user'].strip().title(),
            'action': request.form['action'],
            'tasks': request.form['tasks'],
            'project': request.form['project'],
            'timestamp': request.form['timestamp'].strip()
        }
        db.collection('timelogs').document(entry_id).update(updated_data)
        return redirect(url_for('dashboard'))

    return render_template('edit.html', entry=entry)


@app.route('/clock', methods=['POST'])
def clock():
    user = request.form['user'].strip().title()
    pin = request.form['pin']
    action = request.form['action']
    tasks = request.form['tasks']
    project = request.form.get('project')

    if VALID_PINS.get(user) != pin:
        return render_template("403.html"), 403

    # Handle photo upload to Firebase Storage
    photo = request.files.get('photo')
    photo_url = ''
    if photo and photo.filename != '':
        photo_filename = secure_filename(f"{user}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jpg")
        blob = bucket.blob(f'photos/{photo_filename}')
        blob.upload_from_file(photo, content_type=photo.content_type)
        # Optionally make public (if your bucket allows public read)
        blob.make_public()
        photo_url = blob.public_url

    timestamp = datetime.now(pacific).strftime('%Y-%m-%d %H:%M:%S')

    # Prepare entry dict
    entry = {
        'user': user,
        'action': action,
        'timestamp': timestamp,
        'tasks': tasks,
        'photo_url': photo_url,
        'project': project
    }

    # Save to Firestore
    try:
        # Using a Firestore-generated ID or your own
        doc_ref = db.collection('timelogs').document(str(uuid.uuid4()))
        doc_ref.set(entry)
    except Exception as e:
        print("Firestore write failed:", e)
        return "<h3>Error saving entry. Please try again.</h3><a href='/'>Back</a>"

    display_time = datetime.now(pacific).strftime('%I:%M %p %Z')
    return f"""
    <html>
    <head>
      <meta name="viewport" content="width=device-width, initial-scale=1.0">
      <link rel="stylesheet" href="/static/style.css">
    </head>
    <body class="clock-confirmation">
      <div class="confirmation-wrapper">
        <h3>{user} clocked {action} at {display_time}</h3>
        <p>Project: {project}</p>
        <p>Tasks: {tasks}</p>
        <a href='/'>Back</a>
      </div>
    </body>
    </html>
    """


@app.route('/export')
def export_db():
    items = load_timelogs_from_firestore()
    if not items:
        return "<h3>No data to export.</h3>"
    df = pd.DataFrame(items)
    response = make_response(df.to_csv(index=False))
    response.headers["Content-Disposition"] = "attachment; filename=export_timelog.csv"
    response.headers["Content-Type"] = "text/csv"
    return response


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
