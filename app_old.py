from flask import Flask, render_template, request, redirect, url_for, flash, session, send_file
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.exc import OperationalError
from werkzeug.security import generate_password_hash, check_password_hash
import warnings

# Quiet the SQLAlchemy LegacyAPIWarning about Query.get() deprecation (not an error)
# We match the warning text to avoid silencing other warnings.
warnings.filterwarnings(
    "ignore",
    message=r".*Query.get\(\) method is considered legacy.*"
)
import random, string, io
from datetime import datetime, timedelta
import json

app = Flask(__name__)
app.secret_key = "secret123"
app.config['SQLALCHEMY_DATABASE_URI'] = "sqlite:///quiz.db"
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

# ---------------- Database Models ----------------
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80))
    email = db.Column(db.String(120), unique=True)
    password = db.Column(db.String(200))
    role = db.Column(db.String(20))  # teacher / student
    roll = db.Column(db.String(20))  # student only

class Room(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80))
    code = db.Column(db.String(10), unique=True)
    teacher_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    quiz_id = db.Column(db.Integer, db.ForeignKey('quiz.id'))
    is_active = db.Column(db.Boolean, default=True)
    allow_download = db.Column(db.Boolean, default=False)
    quiz_start_time = db.Column(db.DateTime, nullable=True)  # store when teacher starts quiz
    students = db.relationship("User", secondary="room_students", backref="rooms")

room_students = db.Table('room_students',
    db.Column('room_id', db.Integer, db.ForeignKey('room.id')),
    db.Column('student_id', db.Integer, db.ForeignKey('user.id'))
)

class Quiz(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(100))
    questions_json = db.Column(db.Text)
    duration = db.Column(db.Integer, default=5)  # in minutes

class StudentQuizResult(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    room_id = db.Column(db.Integer, db.ForeignKey('room.id'))
    score = db.Column(db.Integer)
    answers_json = db.Column(db.Text)  # store student answers
    started = db.Column(db.DateTime, nullable=True)  # track when student starts

# ----------------- Helpers -----------------
def generate_code(length=6):
    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=length))

def current_user():
    if 'user_id' in session:
        uid = session['user_id']
        try:
            # preferred: Session.get() (avoids Query.get() deprecation)
            return db.session.get(User, uid)
        except Exception:
            # fallback for older SQLAlchemy versions
            return User.query.get(uid)
    return None


# Make current_user available to all templates as a convenient variable
@app.context_processor
def inject_current_user():
    return {'current_user': current_user()}


# ---- Quiz helpers ----
def load_quiz_questions(quiz):
    if not quiz:
        return []
    try:
        return json.loads(quiz.questions_json) if quiz.questions_json else []
    except Exception:
        return []


def calculate_score(questions, answers):
    score = 0
    for i, q in enumerate(questions):
        try:
            if str(i) in answers and int(answers[str(i)]) == int(q.get('answer')):
                score += 1
        except Exception:
            pass
    return score


def prepare_question_list(questions, answers):
    qlist = []
    for i, q in enumerate(questions):
        student_choice = None
        try:
            if str(i) in answers:
                val = answers.get(str(i))
                if val is not None and val != '':
                    student_choice = int(val)
        except Exception:
            student_choice = None
        correct = (student_choice is not None and q.get('answer') is not None and int(student_choice) == int(q.get('answer')))
        qlist.append({
            'index': i+1,
            'question': q.get('question'),
            'options': q.get('options', []),
            'correct_answer': int(q.get('answer')) if q.get('answer') is not None else None,
            'student_choice': student_choice,
            'correct': correct
        })
    return qlist

# ---------------- Routes -----------------
@app.route('/close_room/<int:room_id>')
def close_room(room_id):
    user = current_user()
    room = Room.query.get_or_404(room_id)
    if not user or user.role != 'teacher' or room.teacher_id != user.id:
        flash("Access denied!")
        return redirect(url_for('index'))

    room.is_active = False  # deactivate the room
    db.session.commit()
    flash(f"Room '{room.name}' has been closed.")
    return redirect(url_for('teacher_dashboard', room_id=room.id))
@app.route('/teacher_dashboard/<int:room_id>')
def teacher_dashboard(room_id):
    user = current_user()
    room = Room.query.get_or_404(room_id)
    if user.role != 'teacher' or room.teacher_id != user.id:
        flash("Access denied!")
        return redirect(url_for('index'))
    # support sorting by score via ?sort=asc or ?sort=desc and pagination via ?page=
    sort = request.args.get('sort', None)
    try:
        page = int(request.args.get('page', 1))
        if page < 1:
            page = 1
    except Exception:
        page = 1
    per_page = 10
    # batch-fetch results to avoid N+1 queries
    students = []
    student_ids = [s.id for s in room.students]
    results = {}
    if student_ids:
        rows = StudentQuizResult.query.filter(StudentQuizResult.student_id.in_(student_ids), StudentQuizResult.room_id == room.id).all()
        for row in rows:
            results[row.student_id] = row

    for s in room.students:
        r = results.get(s.id)
        numeric_score = None
        if r and r.answers_json and r.answers_json != "{}":
            try:
                numeric_score = int(r.score) if r.score is not None else None
            except Exception:
                numeric_score = None
        students.append({
            'id': s.id,
            'name': s.name,
            'roll': s.roll,
            'started': r.started.strftime("%H:%M:%S") if r and r.started else "-",
            'submitted': "Yes" if r and r.answers_json and r.answers_json != "{}" else "No",
            'score': numeric_score if numeric_score is not None else '-',
            'score_val': numeric_score if numeric_score is not None else -9999
        })

    if sort == 'asc':
        students = sorted(students, key=lambda x: x['score_val'])
    elif sort == 'desc':
        students = sorted(students, key=lambda x: x['score_val'], reverse=True)

    total_students = len(students)
    total_pages = max(1, (total_students + per_page - 1) // per_page)
    # clamp page
    if page > total_pages:
        page = total_pages
    start = (page - 1) * per_page
    end = start + per_page
    students_page = students[start:end]

    return render_template("teacher_dashboard.html", room=room, students=students_page, sort=sort, page=page, total_pages=total_pages)


@app.route('/teacher_dashboard')
def teacher_dashboard_index():
    user = current_user()
    app.logger.info(f"[route] teacher_dashboard_index called; current_user -> {getattr(user,'id',None)}:{getattr(user,'role',None)}")
    if not user or user.role != 'teacher':
        flash("Access denied", 'danger')
        return redirect(url_for('index'))
    room = Room.query.filter_by(teacher_id=user.id).first()
    app.logger.info(f"[route] teacher_dashboard_index: found room={getattr(room,'id',None)}")
    if room:
        return redirect(url_for('teacher_dashboard', room_id=room.id))
    # No room found — render the teacher dashboard view with a placeholder so
    # the teacher still lands on their dashboard page and can create a room.
    flash("You don't have any rooms yet. Create one.", 'info')
    placeholder_room = {'id': None, 'name': 'No rooms yet', 'code': ''}
    students_page = []
    return render_template('teacher_dashboard.html', room=placeholder_room, students=students_page, sort=None, page=1, total_pages=1)


@app.route('/student_sheet/<int:room_id>/<int:student_id>')
def student_sheet(room_id, student_id):
    user = current_user()
    room = Room.query.get_or_404(room_id)
    if not user or user.role != 'teacher' or room.teacher_id != user.id:
        flash("Access denied!")
        return redirect(url_for('index'))

    student = db.session.get(User, student_id) or User.query.get_or_404(student_id)
    # load quiz and student's answers
    quiz = db.session.get(Quiz, room.quiz_id) if room.quiz_id else None
    if not quiz:
        flash("No quiz uploaded for this room.")
        return redirect(url_for('teacher_dashboard', room_id=room.id))

    questions = load_quiz_questions(quiz)

    r = StudentQuizResult.query.filter_by(student_id=student.id, room_id=room.id).first()
    answers = {}
    if r and r.answers_json and r.answers_json != "{}":
        try:
            answers = json.loads(r.answers_json)
        except Exception:
            answers = {}

    score = calculate_score(questions, answers)
    qlist = prepare_question_list(questions, answers)

    back_url = url_for('teacher_dashboard', room_id=room.id)
    return render_template('teacher_student_sheet.html', room=room, student=student, qlist=qlist, score=score, total=len(questions), back_url=back_url)


@app.route('/student_sheet_print/<int:room_id>/<int:student_id>')
def student_sheet_print(room_id, student_id):
    # Render a print-friendly version (no navigation), same permission checks
    user = current_user()
    room = Room.query.get_or_404(room_id)
    if not user or user.role != 'teacher' or room.teacher_id != user.id:
        flash("Access denied!")
        return redirect(url_for('index'))
    student = db.session.get(User, student_id) or User.query.get_or_404(student_id)
    quiz = db.session.get(Quiz, room.quiz_id) if room.quiz_id else None
    questions = load_quiz_questions(quiz)
    r = StudentQuizResult.query.filter_by(student_id=student.id, room_id=room.id).first()
    answers = {}
    if r and r.answers_json and r.answers_json != "{}":
        try:
            answers = json.loads(r.answers_json)
        except Exception:
            answers = {}
    score = calculate_score(questions, answers)
    qlist = prepare_question_list(questions, answers)
    return render_template('teacher_student_sheet_print.html', room=room, student=student, qlist=qlist, score=score, total=len(questions))





@app.route('/student_sheet_pdf/<int:room_id>/<int:student_id>')
def student_sheet_pdf(room_id, student_id):
    # Render the print template into PDF if allowed (or teacher viewing)
    user = current_user()
    room = Room.query.get_or_404(room_id)
    student = User.query.get_or_404(student_id)

    # permission: either teacher of room or the student themselves (no teacher permission required)
    if user is None:
        flash('Login required')
        return redirect(url_for('login'))
    if user.role == 'teacher' and room.teacher_id == user.id:
        allowed = True
    elif user.role == 'student' and user.id == student.id:
        allowed = True
    else:
        allowed = False
    if not allowed:
        flash('Not permitted to download this sheet.')
        return redirect(url_for('student_sheet', room_id=room.id, student_id=student.id))

    # reuse existing generator logic from student_sheet_print view by rendering HTML
    import json
    quiz = db.session.get(Quiz, room.quiz_id) if room.quiz_id else None
    questions = load_quiz_questions(quiz)
    r = StudentQuizResult.query.filter_by(student_id=student.id, room_id=room.id).first()
    answers = {}
    if r and r.answers_json and r.answers_json != "{}":
        try:
            answers = json.loads(r.answers_json)
        except Exception:
            answers = {}
    score = calculate_score(questions, answers)
    qlist = prepare_question_list(questions, answers)

    html = render_template('teacher_student_sheet_print.html', room=room, student=student, qlist=qlist, score=score, total=len(questions))

    # Try to generate PDF using WeasyPrint if available
    try:
        from weasyprint import HTML, CSS
        pdf_io = io.BytesIO()
        HTML(string=html, base_url=request.host_url).write_pdf(pdf_io, stylesheets=[CSS(string='@page { size: A4; margin: 20mm }')])
        pdf_io.seek(0)
        filename = f"{student.name}_sheet_room_{room.id}.pdf"
        return send_file(pdf_io, mimetype='application/pdf', as_attachment=True, download_name=filename)
    except Exception as e:
        # graceful fallback: open the print page in a new tab
        flash('PDF generation not available on server. Opening print-friendly page instead.')
        return redirect(url_for('student_sheet_print', room_id=room.id, student_id=student.id))

@app.route('/teacher_dashboard_json/<int:room_id>')
def teacher_dashboard_json(room_id):
    user = current_user()
    room = Room.query.get_or_404(room_id)
    if user.role != 'teacher' or room.teacher_id != user.id:
        return {"error": "Access denied"}, 403

    students = []
    # batch fetch to avoid N+1
    student_ids = [s.id for s in room.students]
    results = {}
    if student_ids:
        rows = StudentQuizResult.query.filter(StudentQuizResult.student_id.in_(student_ids), StudentQuizResult.room_id == room.id).all()
        for row in rows:
            results[row.student_id] = row
    for s in room.students:
        r = results.get(s.id)
        students.append({
            'name': s.name,
            'roll': s.roll,
            'started': r.started.strftime("%H:%M:%S") if r and r.started else "-",
            'submitted': "Yes" if r and r.answers_json and r.answers_json != "{}" else "No",
            'score': r.score if r and r.answers_json and r.answers_json != "{}" else "-"
        })
    return {"students": students}

@app.route('/')
def index():
    return render_template("index.html", user=current_user())

@app.route('/dashboard')
def dashboard():
    user = current_user()
    if not user:
        return redirect(url_for('login'))
    if user.role == 'teacher':
        room = Room.query.filter_by(teacher_id=user.id).first()
        if room:
            return redirect(url_for('teacher_room', room_id=room.id))
    elif user.role == 'student':
        return redirect(url_for('student_room'))
    elif user.is_admin:
        return redirect(url_for('admin_registrations'))
    return redirect(url_for('index'))

# --------- Registration/Login ---------
@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        name = request.form['name']
        email = request.form['email']
        password = generate_password_hash(request.form['password'])
        role = request.form['role']
        roll = request.form.get('roll', None)
        if User.query.filter_by(email=email).first():
            flash("Email already exists!")
            return redirect(url_for('register'))
        u = User(name=name, email=email, password=password, role=role, roll=roll)
        db.session.add(u)
        db.session.commit()
        flash("Registered successfully!")
        return redirect(url_for('dashboard'))

    return render_template("register.html", user=current_user())


@app.route('/login', methods=['GET','POST'])
def login():
    if request.method == 'POST':
        email = request.form['email']
        password = request.form['password']
        u = User.query.filter_by(email=email).first()
        if u and check_password_hash(u.password, password):
            session['user_id'] = u.id
            flash("Logged in successfully!")
            return redirect(url_for('dashboard'))
        flash("Invalid credentials!")
    return render_template("login.html", user=current_user())

@app.route('/logout')
def logout():
    session.pop('user_id', None)
    flash("Logged out.")
    return redirect(url_for('index'))

# --------- Room Management ---------
@app.route('/create_room', methods=['GET','POST'])
def create_room():
    user = current_user()
    if not user or user.role != 'teacher':
        flash("Only teachers can create rooms.")
        return redirect(url_for('index'))
    if request.method == 'POST':
        name = request.form['name']
        code = request.form.get('code') or generate_code()
        r = Room(name=name, code=code, teacher_id=user.id)
        db.session.add(r)
        db.session.commit()
        flash("Room created!")
        return redirect(url_for('teacher_room', room_id=r.id))
    return render_template("create_room.html", user=user)

@app.route('/room/<int:room_id>')
def teacher_room(room_id):
    user = current_user()
    room = Room.query.get_or_404(room_id)
    if user.role == 'teacher' and room.teacher_id == user.id:
        return render_template("teacher_room.html", user=user, room=room)
    elif user.role == 'student' and user in room.students:
        return render_template("student_room.html", user=user, room=room, quiz=room.quiz_id)
    else:
        flash("Access denied!")
        return redirect(url_for('index'))

@app.route('/join_room', methods=['GET','POST'])
def join_room():
    """
    Student-facing join route. Accepts POST with 'join_code' (preferred) or falls back to
    'room_code' / 'code'. Ensures only students can join and uses the existing
    room_students association table to record membership.
    """
    user = current_user()
    if not user:
        flash('Login required')
        return redirect(url_for('login'))
    if user.role != 'student':
        flash('Only students can join rooms.')
        return redirect(url_for('index'))

    # POST: attempt to join by code
    if request.method == 'POST':
        join_code = request.form.get('join_code') or request.form.get('room_code') or request.form.get('code')
        # optional roll field
        roll = request.form.get('student_roll') or request.form.get('roll')
        if not join_code:
            flash('Please provide a join code.')
            return redirect(url_for('student_dashboard'))

        room = Room.query.filter_by(code=join_code, is_active=True).first()
        if not room:
            flash('Invalid join code. Please check and try again.')
            return redirect(url_for('student_dashboard'))

        # update student's roll if provided
        if roll:
            user.roll = roll

        # check existing membership using relationship
        if user in room.students:
            flash('You already joined this room.', 'info')
            return redirect(url_for('student_dashboard'))

        # Register participation
        room.students.append(user)
        db.session.commit()

        flash(f"Joined room '{room.name}' successfully!")
        return redirect(url_for('student_dashboard'))

    # GET fallback: render join form
    return render_template('join_room.html', user=user)


@app.route('/student_dashboard', methods=['GET','POST'])
def student_dashboard():
    user = current_user()
    if not user or user.role != 'student':
        flash('Only students can access the student dashboard.')
        return redirect(url_for('index'))

    if request.method == 'POST':
        # forward to join_room logic via POST
        return join_room()

    # Prepare joined_rooms list with simple status flags
    # batch fetch results for user's rooms
    joined_rooms = []
    room_ids = [r.id for r in user.rooms]
    results_map = {}
    if room_ids:
        rows = StudentQuizResult.query.filter(StudentQuizResult.student_id == user.id, StudentQuizResult.room_id.in_(room_ids)).all()
        for row in rows:
            results_map[row.room_id] = row

    for r in user.rooms:
        quiz_started = bool(r.quiz_start_time)
        sqr = results_map.get(r.id)
        quiz_finished = bool(sqr and sqr.answers_json and sqr.answers_json != "{}")
        joined_rooms.append({
            'id': r.id,
            'name': r.name,
            'quiz_started': quiz_started,
            'quiz_finished': quiz_finished
        })

    return render_template('student_dashboard.html', user=user, joined_rooms=joined_rooms)

# --------- Quiz ---------
@app.route('/start_quiz_student/<int:room_id>', methods=['GET','POST'])
def start_quiz_student(room_id):
    user = current_user()
    room = Room.query.get_or_404(room_id)
    if user.role != 'student' or user not in room.students:
        flash("Access denied!")
        return redirect(url_for('index'))

    quiz = Quiz.query.get(room.quiz_id)
    if not quiz or not room.quiz_start_time:
        flash("Quiz has not started yet.")
        return redirect(url_for('teacher_room', room_id=room.id))

    import json
    questions = json.loads(quiz.questions_json)

    from datetime import datetime
    if request.method == 'GET':
        # record start time if not already recorded
        r = StudentQuizResult.query.filter_by(student_id=user.id, room_id=room.id).first()
        if not r:
            r = StudentQuizResult(student_id=user.id, room_id=room.id, score=0, answers_json="{}", started=datetime.utcnow())
            db.session.add(r)
            db.session.commit()
        else:
            if not r.started:
                r.started = datetime.utcnow()
                db.session.commit()

    if request.method == 'POST':
        answers = {}
        for i in range(len(questions)):
            val = request.form.get(f'q{i}', None)
            try:
                answers[str(i)] = int(val) if val is not None and val != '' else -1
            except Exception:
                # non-numeric input -> treat as unanswered
                answers[str(i)] = -1
        score = sum(1 for i, q in enumerate(questions) if answers[str(i)] == q['answer'])

        # Save result
        r = StudentQuizResult.query.filter_by(student_id=user.id, room_id=room.id).first()
        if not r:
            r = StudentQuizResult(student_id=user.id, room_id=room.id, score=score, answers_json=json.dumps(answers), started=datetime.utcnow())
            db.session.add(r)
        else:
            r.score = score
            r.answers_json = json.dumps(answers)
        db.session.commit()

        flash(f"Quiz submitted! Score: {score}/{len(questions)}")
        return redirect(url_for('student_result', room_id=room.id))

    # Calculate remaining time
    import datetime as dt
    elapsed = (dt.datetime.utcnow() - room.quiz_start_time).total_seconds()
    remaining = max(0, quiz.duration*60 - elapsed)  # in seconds
    if remaining <= 0:
        flash("Time's up! Quiz already ended.")
        return redirect(url_for('student_result', room_id=room.id))

    return render_template('take_quiz.html', user=user, room=room, quiz=quiz,
                           questions=questions, remaining=remaining)
@app.route('/create_quiz/<int:room_id>', methods=['GET','POST'])
def create_quiz(room_id):
    user = current_user()
    room = Room.query.get_or_404(room_id)
    if user.role != 'teacher' or room.teacher_id != user.id:
        flash("Access denied!")
        return redirect(url_for('index'))
    if request.method == 'POST':
        title = request.form['title']
        questions = request.form['questions']
        q = Quiz(title=title, questions_json=questions)
        db.session.add(q)
        db.session.commit()
        room.quiz_id = q.id
        db.session.commit()
        flash("Quiz uploaded successfully!")
        return redirect(url_for('teacher_room', room_id=room.id))
    return render_template("create_quiz.html", user=user, room=room)

@app.route('/start_quiz/<int:room_id>')
def start_quiz(room_id):
    user = current_user()
    room = Room.query.get_or_404(room_id)
    if user.role != 'teacher' or room.teacher_id != user.id:
        flash("Access denied!")
        return redirect(url_for('index'))

    if not room.quiz_id:
        flash("Upload a quiz first!")
        return redirect(url_for('teacher_room', room_id=room.id))

    from datetime import datetime
    room.quiz_start_time = datetime.utcnow()
    db.session.commit()
    flash("Quiz started for students!")
    return redirect(url_for('teacher_room', room_id=room.id))

@app.route('/room_report/<int:room_id>')
def room_report(room_id):
    user = current_user()
    room = Room.query.get_or_404(room_id)
    if user.role != 'teacher' or room.teacher_id != user.id:
        flash("Access denied!")
        return redirect(url_for('index'))
    students = []
    for s in room.students:
        r = StudentQuizResult.query.filter_by(student_id=s.id, room_id=room.id).first()
        score = r.score if r else 0
        students.append({'name': s.name, 'roll': s.roll, 'score': score})
    return render_template("report.html", user=user, room=room, students=students)

@app.route('/student_result/<int:room_id>')
def student_result(room_id):
    user = current_user()
    room = Room.query.get_or_404(room_id)
    r = StudentQuizResult.query.filter_by(student_id=user.id, room_id=room.id).first()
    score = r.score if r else 0
    total = 0
    quiz = Quiz.query.get(room.quiz_id)
    if quiz:
        import json
        try:
            questions = json.loads(quiz.questions_json)
            total = len(questions)
        except Exception:
            total = 0
    return render_template("student_result.html", user=user, score=score, total=total)

# ----------------- DB Init -----------------
with app.app_context():
    # Ensure tables exist. db.create_all() is idempotent and will create missing tables.
    try:
        db.create_all()
    except Exception:
        # If db.create_all() fails for any reason, continue to a best-effort migration below.
        pass

    # Safe SQLite migration helper: if the `room` table doesn't exist yet, create all tables.
    # If the table exists but the 'allow_download' column is missing, add it.
    try:
        import sqlite3
        db_path = 'quiz.db'
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        cur.execute("PRAGMA table_info('room')")
        res = cur.fetchall()
        # If there are no rows, the table doesn't exist; run create_all() to create it.
        if not res:
            try:
                db.create_all()
                # print('Migration: room table missing — created tables via db.create_all()')
            except Exception as _e:
                print('Migration: failed to create tables:', _e)
        else:
            cols = [r[1] for r in res]
            if 'allow_download' not in cols:
                try:
                    cur.execute("ALTER TABLE room ADD COLUMN allow_download BOOLEAN DEFAULT 0")
                    conn.commit()
                    print('Migrated: added allow_download column to room table')
                except Exception as _e:
                    print('Migration: failed to add allow_download column:', _e)
        conn.close()
    except Exception as e:
        # Don't print raw DB errors here — we want to keep startup logs clean.
        print('Migration check skipped (SQLite helper unable to run)')


# Provide a user-friendly handler for database OperationalError so debug logs don't show the
# long SQL trace to end-users. This catches errors like "no such column: room.allow_download"
@app.errorhandler(OperationalError)
def handle_db_operational_error(err):
    # Silently ignore DB OperationalErrors (like missing columns)
    return redirect(url_for('index'))

# ----------------- Run -----------------
if __name__ == "__main__":
    app.run(debug=True)
