from flask import Flask, request, render_template, redirect, url_for, session
from werkzeug.utils import secure_filename
from datetime import datetime, timedelta
import csv
import pandas as pd
import os
import pytz
from collections import defaultdict

app = Flask(__name__)

app.secret_key = 'secret_key_everett-7714'
# 🔐 Valid PINs
VALID_PINS = {
    "Tony": "1234",
    "Hector": "5678",
    "Dad": "1111",
    "Louis": "2222",
    "Admin": "0308",
    "Daniel": "3333",
}

# ✅ List of projects
PROJECTS = [
    "Garage Conversion", "Garage Conversion; electrical", "Bathroom Addition",
    "Bathroom Addition:Electrical", "Wall Division",
    "Complete Painting Exterior", "Garage: Drain/Hole for Water",
    "Garage: Slope Concrete (In & Out)",
    "Garage: Move Sensor + Add Side Latch", "Garage: Seal Cut Door Channel",
    "Garage: Close Gaps at Front", "Garage: Patch Concrete by Back Door",
    "Garage: Install Vent System", "Garage: Add Plug Spacers",
    "Garage:Fascia front ", "Kitchen: Install Trims",
    "Kitchen: Side Board on Cabinet", "Kitchen: Paint Skylight Area",
    "Front Door: Patch on Door", "Home Depot Run", "Dump Run"
]
COMPLETED_PROJECTS = [
    "Garage: Install Back Door", "Garage: Roof Leak (New & Old)",
    "Garage: Fix Outside Light Switches"
    # Add more as needed
]
PROJECTS = [
    project for project in PROJECTS if project not in COMPLETED_PROJECTS
]
# ✅ Allowed IP address (replace with your actual Wi-Fi IP)
ALLOWED_IP = "172.56.108.248"

# ✅ Timezone
pacific = pytz.timezone('US/Pacific')


# ✅ Weekly summary function
def get_weekly_summary():
    if not os.path.exists('timelogs.csv') or os.path.getsize(
            'timelogs.csv') == 0:
        return []

    try:
        df = pd.read_csv(
            'timelogs.csv',
            names=['user', 'action', 'timestamp', 'tasks', 'photo', 'project'])

        # Convert to datetime first
        df['timestamp'] = pd.to_datetime(df['timestamp'], errors='coerce')
        df = df.dropna(subset=['timestamp'])

        # Store raw timestamp for delete matching
        df['raw_timestamp'] = df['timestamp'].dt.strftime('%Y-%m-%d %H:%M:%S')

        # Filter by current week
        df['week'] = df['timestamp'].dt.isocalendar().week
        current_week = datetime.now(pacific).isocalendar().week
        df = df[df['week'] == current_week]

        # Format for display
        df['timestamp'] = df['timestamp'].dt.strftime('%Y-%m-%d %I:%M %p')

        # Sort and return
        df = df.sort_values(by=['user', 'timestamp'])
        return df.to_dict(orient='records')

    except Exception as e:
        print(f"Error in get_weekly_summary: {e}")
        return []


# ✅ Total hours calculator
def get_total_hours():
    if not os.path.exists('timelogs.csv') or os.path.getsize(
            'timelogs.csv') == 0:
        return {}

    try:
        df = pd.read_csv(
            'timelogs.csv',
            names=['user', 'action', 'timestamp', 'tasks', 'photo', 'project'])
        df['timestamp'] = pd.to_datetime(df['timestamp'], errors='coerce')
        df = df.dropna(subset=['timestamp'])

        df['week'] = df['timestamp'].dt.isocalendar().week
        current_week = datetime.now(pacific).isocalendar().week
        df = df[df['week'] == current_week]

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

            # ✅ If still clocked in, count time up to now
            if clocked_in:
                total += datetime.now(pacific) - clocked_in

            total_hours[user] = round(total.total_seconds() / 3600, 2)
        return total_hours
    except Exception as e:
        print(f"Error in get_total_hours: {e}")
        return {}


# ✅ IP restriction middleware
@app.before_request
def restrict_by_ip():
    forwarded_for = request.headers.get('X-Forwarded-For', '')
    ip = forwarded_for.split(',')[0] if forwarded_for else request.remote_addr
    print(f"Detected IP: {ip}")

    if request.endpoint == 'static':
        return

    if request.endpoint in ['home', 'clock', 'dashboard']:
        if ip == ALLOWED_IP:
            return
        else:
            return """
            <html>
            <head>
              <meta name="viewport" content="width=device-width, initial-scale=1.0">
              <link rel="stylesheet" href="/static/style.css">
            </head>
            <body class="access-denied">
              <div class="access-wrapper">
                <h3>⛔ Access Denied</h3>
                <p>You are not connected to the job site Wi-Fi.</p>
                <a href="/">Back to Home</a>
              </div>
            </body>
            </html>
            """, 403


# ✅ Routes
@app.route('/')
def home():
    ip = request.headers.get('X-Forwarded-For', request.remote_addr)
    return render_template('index.html', ip=ip, projects=PROJECTS)


@app.route('/dashboard', methods=['GET', 'POST'])
def dashboard():
    limit = request.args.get('limit', default=10, type=int)
    pacific = pytz.timezone('America/Los_Angeles')
    today_date = datetime.now(pacific).strftime(
        '%B %d, %Y')  # Example: October 6, 2025
    selected_week = request.args.get('week')  # format: YYYY-MM-DD

    today = datetime.now(pacific)

    today = datetime.now(pacific)

    if selected_week:
        # selected_week is naive, so we localize it
        start_of_week = pacific.localize(
            datetime.strptime(selected_week, "%Y-%m-%d"))
    else:
        weekday = today.weekday()  # Monday = 0, Sunday = 6
        days_since_sunday = (weekday + 1) % 7
        start_of_week = today - timedelta(days=days_since_sunday)

    # end_of_week is always 6 days after start
    end_of_week = start_of_week + timedelta(days=6)

    selected_date = request.args.get('date')
    selected_user = request.args.get('user')
    selected_project = request.args.get('project', '')

    # Normalize "All" filters
    if selected_user == "All":
        selected_user = None
    if selected_project == "All":
        selected_project = None

    # Handle login
    if request.method == 'POST':
        pin = request.form.get('pin')
        for name, valid_pin in VALID_PINS.items():
            if pin == valid_pin:
                session['user'] = name
                break
        else:
            return render_template("403.html"), 403

    logged_in_user = session.get('user')
    is_admin = logged_in_user == "Admin"

    # Load full data
    df_full = pd.read_csv(
        'timelogs.csv',
        names=['user', 'action', 'timestamp', 'tasks', 'photo', 'project'])
    df_full['timestamp'] = pd.to_datetime(df_full['timestamp'],
                                          errors='coerce')
    df_full['project'] = df_full['project'].fillna("-").astype(str)
    df_full['user'] = df_full['user'].astype(str).str.strip().str.title()
    df_full = df_full.dropna(subset=['timestamp'])
    df_full['timestamp'] = df_full['timestamp'].dt.tz_localize(
        'US/Pacific', ambiguous='NaT', nonexistent='NaT')
    df_full['user'] = df_full['user'].astype(str).str.strip().str.title()
    # Filter by week only
    weekly_df = df_full[(df_full['timestamp'] >= start_of_week)
                        & (df_full['timestamp'] <= end_of_week)]

    # Build sessions strictly within week range
    sessions = []
    weekly_summary = []
    for contractor, group in weekly_df.groupby('user'):
        if selected_user and selected_user != "Admin" and contractor != selected_user:
            continue

        group = group.sort_values('timestamp')
        in_time = None
        for _, row in group.iterrows():
            if selected_project and row['project'] != selected_project:
                continue
            if row['action'] == 'in':
                if start_of_week <= row['timestamp'] <= end_of_week:
                    in_time = row['timestamp']
                else:
                    in_time = None
            elif row['action'] == 'out' and in_time:
                if start_of_week <= row['timestamp'] <= end_of_week:
                    duration = row['timestamp'] - in_time
                    sessions.append({
                        'date': in_time.date(),
                        'duration': duration.total_seconds() / 60,
                        'contractor': contractor
                    })
                in_time = None

    # Daily summary
    daily_minutes = defaultdict(float)
    for entry in sessions:
        key = (entry['date'], entry['contractor'])
        daily_minutes[key] += entry['duration']

    daily_summary = []
    for (date, contractor), minutes in sorted(daily_minutes.items(),
                                              reverse=True):
        hours = int(minutes // 60)
        mins = int(minutes % 60)
        daily_summary.append({
            'date': date,
            'contractor': contractor,
            'formatted': f"{hours}h {mins}m"
        })

    # Weekly summary
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
    # Grand total for all contractors
    grand_total_minutes = sum(weekly_totals.values())
    grand_total_hours = round(grand_total_minutes / 60, 2)
    remaining_hours = round(80 - grand_total_hours, 2)

    # Apply filters to dashboard entries
    df = weekly_df.copy()
    if selected_user and selected_user != "Admin":
        df = df[df['user'] == selected_user]
    if selected_project:
        df = df[df['project'] == selected_project]

    entries = df.to_dict(orient='records')[:limit]
    users = sorted(
        set(row['user'] for row in weekly_df.to_dict(orient='records')))
    photo_folder = os.path.join('static', 'photos')
    photos = set(
        os.listdir(photo_folder)) if os.path.exists(photo_folder) else set()

    return render_template('dashboard.html',
                           data=get_weekly_summary(),
                           users=users,
                           selected_user=selected_user,
                           total_hours=get_total_hours(),
                           is_admin=is_admin,
                           photos=photos,
                           entries=entries,
                           projects=PROJECTS,
                           completed_projects=COMPLETED_PROJECTS,
                           selected_project=selected_project,
                           selected_date=selected_date,
                           daily_summary=daily_summary,
                           grand_total_hours=grand_total_hours,
                           weekly_summary=weekly_summary,
                           limit=limit,
                           remaining_hours=remaining_hours,
                           start_of_week=start_of_week.date(),
                           today_date=today_date)


@app.route('/delete', methods=['POST'])
def delete_entry():
    logged_in_user = session.get('user')
    if logged_in_user != "Admin":
        return "⛔ Unauthorized. Only Admin can delete entries.", 403

    try:
        with open('timelogs.csv', 'r') as f:
            rows = list(csv.reader(f))

        timestamp = request.form.get('timestamp')
        # Remove rows that match the timestamp exactly
        updated_rows = [
            row for row in rows if row[2].strip() != timestamp.strip()
        ]

        with open('timelogs.csv', 'w', newline='') as f:
            writer = csv.writer(f)
            writer.writerows(updated_rows)

        return redirect(url_for('dashboard'))
    except Exception as e:
        return f"Error deleting entry: {e}", 500


@app.route('/clock', methods=['POST'])
def clock():
    user = request.form['user'].strip().title()
    pin = request.form['pin']
    action = request.form['action']
    tasks = request.form['tasks']
    project = request.form.get('project')

    if VALID_PINS.get(user) != pin:
        return """ 
        <html>
<head>
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <link rel="stylesheet" href="/static/style.css">
</head>
<body class="pin-error">
  <main class="pin-error-wrapper">
    <h3>⛔ Invalid PIN</h3>
    <p>Clock-in/out failed. Please check your PIN and try again.</p>
    <a href="/">Back to Home</a>
  </main>
</body>
</html>
        """, 403

    photo_filename = ''
    photo = request.files.get('photo')
    if photo and photo.filename != '':
        photo_filename = secure_filename(
            f"{user}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jpg")
        photo.save(os.path.join('static/photos', photo_filename))

    timestamp = datetime.now(pacific).strftime('%Y-%m-%d %H:%M:%S')

    try:
        with open('timelogs.csv', 'a', newline='') as f:
            writer = csv.writer(f)
            writer.writerow(
                [user, action, timestamp, tasks, photo_filename, project])
        print(
            f"✅ Saved to CSV: {user}, {action}, {timestamp}, {tasks}, {photo_filename},{project}"
        )
    except Exception as e:
        print(f"❌ CSV write failed: {e}")
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


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
