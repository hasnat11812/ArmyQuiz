from flask import Flask, render_template, request, redirect, url_for, flash, session
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
import random, string, json, os
from datetime import datetime, timedelta

app = Flask(__name__)
# Use environment variables for deployment (Render.com sets DATABASE_URL and you
# should set SECRET_KEY in the Render dashboard). Fall back to sensible dev
# defaults for local development.
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', os.environ.get('FLASK_SECRET', 'dev-secret'))
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL', 'sqlite:///quiz.db')
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
    quiz_start_time = db.Column(db.DateTime, nullable=True)
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
    answers_json = db.Column(db.Text)
    started = db.Column(db.DateTime, nullable=True)


class AnswerSheet(db.Model):
        """Stores a per-student, per-room detailed answer sheet for later review.
        details_json contains a list of objects for each question with keys:
            { index, question, options, correct_answer, student_choice, correct }
        """
        id = db.Column(db.Integer, primary_key=True)
        student_id = db.Column(db.Integer, db.ForeignKey('user.id'), index=True)
        room_id = db.Column(db.Integer, db.ForeignKey('room.id'), index=True)
        score = db.Column(db.Integer)
        details_json = db.Column(db.Text)
        auto_submit_reason = db.Column(db.String(50), nullable=True)
        created_at = db.Column(db.DateTime, default=datetime.utcnow)

# ----------------- Helpers -----------------
def generate_code(length=6):
    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=length))

def current_user():
    if 'user_id' in session:
        # Use session.get via the session-aware API to avoid legacy Query.get()
        try:
            return db.session.get(User, session['user_id'])
        except Exception:
            # fallback to previous approach if session.get isn't available
            return User.query.get(session['user_id'])
    return None


# Normalize various incoming question JSON formats into the internal shape:
# [{ 'text': str, 'options': [str,...], 'answer': int }, ...]
def normalize_questions(raw):
    """Accept a parsed JSON object (usually a list). Return normalized list or raise ValueError."""
    if not isinstance(raw, list):
        raise ValueError('Questions JSON must be a list of question objects')

    out = []
    for idx, q in enumerate(raw):
        if not isinstance(q, dict):
            raise ValueError(f'Question at index {idx} is not an object')

        # text can be 'question' or 'text'
        text = q.get('text') or q.get('question') or q.get('q')
        if not text:
            raise ValueError(f'Question at index {idx} is missing text')

        # options: either dict {a:...,b:...} or list
        opts = q.get('options')
        if opts is None:
            raise ValueError(f'Question at index {idx} is missing options')

        if isinstance(opts, dict):
            # sort by key a,b,c,d if possible
            keys = sorted(opts.keys())
            # prefer ordering a,b,c,d if present
            order = [k for k in ['a','b','c','d'] if k in opts]
            if order:
                ordered = [opts[k] for k in order]
            else:
                ordered = [opts[k] for k in keys]
            options_list = ordered
        elif isinstance(opts, list):
            options_list = opts
        else:
            raise ValueError(f'Question at index {idx} has invalid options type')

        # answer: could be index (int) or letter 'a'.. or one of option values
        ans = q.get('answer')
        if isinstance(ans, int):
            answer_index = ans
        elif isinstance(ans, str):
            a = ans.strip().lower()
            # letter a/b/c -> index
            if len(a) == 1 and a.isalpha():
                # map a->0, b->1 ...
                answer_index = ord(a) - ord('a')
            else:
                # maybe the answer is one of the option strings; find index
                try:
                    answer_index = options_list.index(ans)
                except Exception:
                    # if numeric string
                    if a.isdigit():
                        answer_index = int(a)
                    else:
                        raise ValueError(f'Question at index {idx} has unknown answer format')
        else:
            raise ValueError(f'Question at index {idx} missing answer')

        if not (0 <= answer_index < len(options_list)):
            raise ValueError(f'Question at index {idx} has answer index {answer_index} out of range')

        out.append({'text': text, 'options': options_list, 'answer': answer_index})

    return out


def finalize_room_submissions(room):
    """Finalize submissions for all students in a room: auto-submit unanswered quizzes.
    Idempotent: skips students who already submitted answers (answers_json not empty or not "{}").
    """
    if not room or not room.quiz_id:
        return
    quiz = Quiz.query.get(room.quiz_id)
    try:
        questions = json.loads(quiz.questions_json) if quiz and quiz.questions_json else []
    except Exception:
        questions = []

    for s in room.students:
        r = StudentQuizResult.query.filter_by(student_id=s.id, room_id=room.id).first()
        # if no result exists, create one with default answers
        if not r:
            answers = {str(i): -1 for i in range(len(questions))}
            score = 0
            r = StudentQuizResult(student_id=s.id, room_id=room.id, score=score, answers_json=json.dumps(answers), started=None)
            db.session.add(r)
        else:
            try:
                existing = r.answers_json and r.answers_json != "{}"
            except Exception:
                existing = False
            if existing:
                # already submitted, skip
                continue
            # auto-submit with default -1 answers
            answers = {str(i): -1 for i in range(len(questions))}
            score = 0
            r.score = score
            r.answers_json = json.dumps(answers)
        # create/update AnswerSheet as in normal submission
        details = []
        for i, q in enumerate(questions):
            student_choice = -1
            correct = False
            details.append({
                'index': i,
                'question': q.get('text') or q.get('question') or q.get('q') or '',
                'options': q.get('options', []),
                'correct_answer': q.get('answer'),
                'student_choice': student_choice,
                'correct': correct
            })
        sheet = AnswerSheet.query.filter_by(student_id=s.id, room_id=room.id).first()
        if not sheet:
            sheet = AnswerSheet(student_id=s.id, room_id=room.id, score=score, details_json=json.dumps(details), auto_submit_reason='auto')
            db.session.add(sheet)
        else:
            sheet.score = score
            sheet.details_json = json.dumps(details)
            sheet.created_at = datetime.utcnow()
            sheet.auto_submit_reason = sheet.auto_submit_reason or 'auto'
    db.session.commit()


# Inject commonly used template variables (user and a static file version for cache-busting)
@app.context_processor
def inject_globals():
    user = current_user()
    try:
        path = os.path.join(app.root_path, 'static', 'styles.css')
        static_version = int(os.path.getmtime(path))
    except Exception:
        static_version = 0
    return dict(user=user, static_version=static_version)

# ---------------- Routes -----------------
@app.route('/')
def index():
    return render_template("index.html", user=current_user())

# --------- Authentication ---------
@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        name = request.form['name']
        email = request.form['email']
        password = generate_password_hash(request.form['password'])
        role = request.form['role']
        roll = request.form.get('roll', None)

        # If role is student, roll is required. For teachers, roll is optional.
        if role == 'student' and (not roll or not roll.strip()):
            flash('Roll number is required for students.', 'error')
            return redirect(url_for('register'))

        # Check if email already exists
        if User.query.filter_by(email=email).first():
            flash("Email already exists!")
            return redirect(url_for('register'))

        # Create new user
        # Normalize empty roll to None for DB clarity
        rval = roll.strip() if roll and roll.strip() else None
        u = User(name=name, email=email, password=password, role=role, roll=rval)
        db.session.add(u)
        db.session.commit()
        flash("Registered successfully!")

        # Automatically log in and redirect based on role
        session['user_id'] = u.id
        if u.role == 'teacher':
            return redirect(url_for('create_room'))
        else:  # student
            return redirect(url_for('join_room'))

    return render_template("register.html", user=current_user())



@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form['email']
        password = request.form['password']
        u = User.query.filter_by(email=email).first()

        if u and check_password_hash(u.password, password):
            session['user_id'] = u.id
            flash("Logged in successfully!")

            # Role-based redirect
            if u.role == 'teacher':
                # If teacher has rooms, go to first room; otherwise create room
                room = Room.query.filter_by(teacher_id=u.id).first()
                if room:
                    return redirect(url_for('teacher_room', room_id=room.id))
                else:
                    return redirect(url_for('create_room'))
            else:  # student
                return redirect(url_for('join_room'))

        flash("Invalid credentials!")
        return redirect(url_for('login'))

    return render_template("login.html", user=current_user())

@app.route('/logout')
def logout():
    session.pop('user_id', None)
    flash("Logged out.", "success")
    return redirect(url_for('index'))

# --------- Room Management ---------
@app.route('/create_room', methods=['GET','POST'])
def create_room():
    user = current_user()
    if not user or user.role != 'teacher':
        flash("Only teachers can create rooms.", "error")
        return redirect(url_for('index'))
    if request.method == 'POST':
        # Use .get to avoid KeyError/BadRequestKeyError when a key is missing.
        name = (request.form.get('name') or '').strip()
        code = (request.form.get('code') or '').strip()

        # Basic validation
        if not name:
            flash("Room name is required.", "error")
            return render_template('create_room.html', user=user)

        # If a custom code was provided, check uniqueness; otherwise generate one.
        if not code:
            code = generate_code()
        else:
            # ensure uppercase alphanumeric short code
            code = ''.join(ch for ch in code.upper() if ch.isalnum())[:10]
            if Room.query.filter_by(code=code).first():
                flash("That room code is already taken. Please choose another code or leave blank to auto-generate.", "error")
                return render_template('create_room.html', user=user)

        r = Room(name=name, code=code, teacher_id=user.id)
        db.session.add(r)
        db.session.commit()
        flash("Room created!", "success")
        return redirect(url_for('teacher_room', room_id=r.id))
    return render_template("create_room.html", user=user)

@app.route('/room/<int:room_id>')
def teacher_room(room_id):
    user = current_user()
    room = Room.query.get_or_404(room_id)
    if not user:
        flash("Login required.", "error")
        return redirect(url_for('index'))
    if user.role == 'teacher' and room.teacher_id == user.id:
        return render_template("teacher_room.html", user=user, room=room)
    elif user.role == 'student' and user in room.students:
        return render_template("student_room.html", user=user, room=room, quiz=room.quiz_id)
    else:
        flash("Access denied!", "error")
        return redirect(url_for('index'))

@app.route('/join_room', methods=['GET','POST'])
def join_room():
    user = current_user()
    if not user or user.role != 'student':
        flash("Only students can join rooms.", "error")
        return redirect(url_for('index'))
    if request.method == 'POST':
        # Use the 'room_code' field name from the template and .get() to avoid KeyErrors
        code = (request.form.get('room_code') or '').strip()
        if not code:
            flash('Room code is required.', 'error')
            return redirect(url_for('join_room'))

        room = Room.query.filter_by(code=code, is_active=True).first()
        if not room:
            flash("Invalid or closed room.", "error")
            return redirect(url_for('join_room'))
        if user not in room.students:
            room.students.append(user)
            db.session.commit()
        flash(f"Joined room {room.name}", "success")
        return redirect(url_for('teacher_room', room_id=room.id))
    return render_template("join_room.html", user=user)

@app.route('/close_room/<int:room_id>')
def close_room(room_id):
    user = current_user()
    room = Room.query.get_or_404(room_id)
    if not user or user.role != 'teacher' or room.teacher_id != user.id:
        flash("Access denied!", "error")
        return redirect(url_for('index'))
    room.is_active = False
    db.session.commit()
    flash(f"Room '{room.name}' closed successfully!", "success")
    # If the request came from the dashboard, return there; otherwise, default to teacher_dashboard
    return redirect(url_for('teacher_dashboard'))


# Close room via POST (safer for dashboard actions)
@app.route('/close_room_post/<int:room_id>', methods=['POST'])
def close_room_post(room_id):
    user = current_user()
    room = Room.query.get_or_404(room_id)
    if not user or user.role != 'teacher' or room.teacher_id != user.id:
        flash("Access denied!", "error")
        return redirect(url_for('index'))
    room.is_active = False
    db.session.commit()
    flash(f"Room '{room.name}' closed successfully!", "success")
    # finalize any pending submissions
    try:
        finalize_room_submissions(room)
    except Exception:
        pass
    return redirect(url_for('teacher_dashboard'))

# --------- Teacher Dashboard ---------
@app.route('/teacher_dashboard')
def teacher_dashboard():
    user = current_user()
    if not user or user.role != 'teacher':
        flash("Access denied!", "error")
        return redirect(url_for('index'))

    # Gather teacher's rooms and some counts
    rooms = Room.query.filter_by(teacher_id=user.id).all()
    rooms_count = len(rooms)
    quizzes_count = Quiz.query.count()
    students_count = sum(len(r.students) for r in rooms)
    active_rooms = sum(1 for r in rooms if r.is_active)

    # Build a students list summary for the dashboard (recent or all)
    students = []
    for r in rooms:
        for s in r.students:
            res = StudentQuizResult.query.filter_by(student_id=s.id, room_id=r.id).first()
            students.append({'name': s.name, 'roll': s.roll, 'score': res.score if res else 0, 'room': r.name})

    return render_template('teacher_dashboard.html', user=user, rooms=rooms, rooms_count=rooms_count,
                           quizzes_count=quizzes_count, students_count=students_count, active_rooms=active_rooms,
                           students=students)


@app.route('/view_quizzes')
def view_quizzes():
    user = current_user()
    if not user or user.role != 'teacher':
        flash('Access denied!', 'error')
        return redirect(url_for('index'))
    quizzes = Quiz.query.order_by(Quiz.id.desc()).all()
    return render_template('view_quizzes.html', user=user, quizzes=quizzes)


@app.route('/view_results')
def view_results():
    user = current_user()
    if not user or user.role != 'teacher':
        flash('Access denied!', 'error')
        return redirect(url_for('index'))
    # show teacher's rooms and link to reports
    rooms = Room.query.filter_by(teacher_id=user.id).order_by(Room.id.desc()).all()
    return render_template('view_results.html', user=user, rooms=rooms)

@app.route('/teacher_dashboard_json/<int:room_id>')
def teacher_dashboard_json(room_id):
    user = current_user()
    room = Room.query.get_or_404(room_id)
    if not user or user.role != 'teacher' or room.teacher_id != user.id:
        return {"error": "Access denied"}, 403

    students = []
    for s in room.students:
        r = StudentQuizResult.query.filter_by(student_id=s.id, room_id=room.id).first()
        students.append({
            'name': s.name,
            'roll': s.roll,
            'started': r.started.strftime("%H:%M:%S") if r and r.started else "-",
            'submitted': "Yes" if r and r.answers_json and r.answers_json != "{}" else "No",
            'score': r.score if r and r.answers_json and r.answers_json != "{}" else "-"
        })
    return {"students": students}


# --------- Student Dashboard ---------
@app.route('/student_dashboard')
def student_dashboard():
    user = current_user()
    if not user or user.role != 'student':
        flash('Access denied!', 'error')
        return redirect(url_for('index'))

    # Gather rooms student has joined
    joined_rooms = user.rooms if user else []

    return render_template('student_dashboard.html', user=user, rooms=joined_rooms)


# Student results overview (all rooms)
@app.route('/student_results_overview')
def student_results_overview():
    user = current_user()
    if not user or user.role != 'student':
        flash('Access denied!', 'error')
        return redirect(url_for('index'))

    results = StudentQuizResult.query.filter_by(student_id=user.id).all()
    # Enrich with room and quiz titles
    enriched = []
    for r in results:
        room = Room.query.get(r.room_id)
        quiz = Quiz.query.get(room.quiz_id) if room else None
        try:
            total = len(json.loads(quiz.questions_json)) if quiz and quiz.questions_json else 0
        except Exception:
            total = 0
        enriched.append({
            'room_name': room.name if room else 'Unknown',
            'room_id': room.id if room else None,
            'quiz_title': quiz.title if quiz else 'N/A',
            'score': r.score,
            'total': total,
            'submitted': bool(r.answers_json and r.answers_json != "{}"),
            'started': r.started
        })

    return render_template('student_results_overview.html', user=user, results=enriched)

# --------- Quiz Management ---------
@app.route('/create_quiz/<int:room_id>', methods=['GET','POST'])
def create_quiz(room_id):
    user = current_user()
    room = Room.query.get_or_404(room_id)
    if not user or user.role != 'teacher' or room.teacher_id != user.id:
        flash("Access denied!", "error")
        return redirect(url_for('index'))
    if request.method == 'POST':
        title = request.form['title']
        questions_raw = request.form['questions']
        try:
            parsed = json.loads(questions_raw)
            normalized = normalize_questions(parsed)
        except Exception as e:
            flash(f'Invalid questions JSON: {e}', 'error')
            return render_template('create_quiz.html', user=user, room=room)

        q = Quiz(title=title, questions_json=json.dumps(normalized))
        db.session.add(q)
        db.session.commit()
        room.quiz_id = q.id
        db.session.commit()
        flash("Quiz uploaded successfully!", "success")
        return redirect(url_for('teacher_room', room_id=room.id))
    return render_template("create_quiz.html", user=user, room=room)

@app.route('/start_quiz/<int:room_id>')
def start_quiz(room_id):
    user = current_user()
    room = Room.query.get_or_404(room_id)
    if not user or user.role != 'teacher' or room.teacher_id != user.id:
        flash("Access denied!", "error")
        return redirect(url_for('index'))
    if not room.quiz_id:
        flash("Upload a quiz first!", "error")
        return redirect(url_for('teacher_room', room_id=room.id))
    # default duration from quiz or 5 minutes
    quiz = Quiz.query.get(room.quiz_id)
    minutes = int(request.args.get('minutes', quiz.duration if quiz else 5)) if request.args else (quiz.duration if quiz else 5)
    if quiz:
        quiz.duration = minutes
    room.quiz_start_time = datetime.utcnow()
    db.session.commit()
    flash(f"Quiz started for students for {minutes} minutes!", "success")
    return redirect(url_for('teacher_room', room_id=room.id))


# Start quiz via POST (for dashboard actions)
@app.route('/start_quiz_post/<int:room_id>', methods=['POST'])
def start_quiz_post(room_id):
    user = current_user()
    room = Room.query.get_or_404(room_id)
    if not user or user.role != 'teacher' or room.teacher_id != user.id:
        flash("Access denied!", "error")
        return redirect(url_for('index'))
    if not room.quiz_id:
        flash("No quiz uploaded for this room.", "error")
        return redirect(url_for('teacher_dashboard'))
    quiz = Quiz.query.get(room.quiz_id)
    try:
        minutes = int(request.form.get('minutes', quiz.duration if quiz else 5))
    except Exception:
        minutes = quiz.duration if quiz else 5
    # store duration on the quiz so students and reporting can use it
    if quiz:
        quiz.duration = minutes
    room.quiz_start_time = datetime.utcnow()
    db.session.commit()
    flash(f"Quiz started for room '{room.name}' for {minutes} minutes", "success")
    return redirect(url_for('teacher_dashboard'))


@app.route('/extend_quiz_post/<int:room_id>', methods=['POST'])
def extend_quiz_post(room_id):
    """Extend the remaining time of a running quiz by minutes provided in form 'minutes'."""
    user = current_user()
    room = Room.query.get_or_404(room_id)
    if not user or user.role != 'teacher' or room.teacher_id != user.id:
        flash("Access denied!", "error")
        return redirect(url_for('index'))
    if not room.quiz_start_time:
        flash("Quiz is not running.", "error")
        return redirect(url_for('teacher_dashboard'))
    try:
        minutes = int(request.form.get('minutes', 5))
    except Exception:
        minutes = 5
    # extend by subtracting from start_time so remaining increases (we store start_time)
    room.quiz_start_time = room.quiz_start_time - timedelta(minutes=minutes)
    db.session.commit()
    flash(f"Extended quiz by {minutes} minutes for room '{room.name}'", "success")
    return redirect(url_for('teacher_dashboard'))

@app.route('/start_quiz_student/<int:room_id>', methods=['GET','POST'])
def start_quiz_student(room_id):
    user = current_user()
    room = Room.query.get_or_404(room_id)
    if not user or user.role != 'student' or user not in room.students:
        flash("Access denied!", "error")
        return redirect(url_for('index'))

    quiz = Quiz.query.get(room.quiz_id)
    if not quiz or not room.quiz_start_time:
        flash("Quiz has not started yet.", "error")
        return redirect(url_for('teacher_room', room_id=room.id))

    questions = json.loads(quiz.questions_json)

    # record start time
    r = StudentQuizResult.query.filter_by(student_id=user.id, room_id=room.id).first()
    if not r:
        r = StudentQuizResult(student_id=user.id, room_id=room.id, score=0, answers_json="{}", started=datetime.utcnow())
        db.session.add(r)
    elif not r.started:
        r.started = datetime.utcnow()
    db.session.commit()

    if request.method == 'POST':
        answers = {str(i): int(request.form.get(f'q{i}', -1)) for i in range(len(questions))}
        score = sum(1 for i, q in enumerate(questions) if answers[str(i)] == q['answer'])
        r.score = score
        r.answers_json = json.dumps(answers)

        # Build detailed sheet per question for storage and later reporting
        details = []
        for i, q in enumerate(questions):
            student_choice = answers.get(str(i), -1)
            correct = (student_choice == q['answer'])
            details.append({
                'index': i,
                'question': q.get('text') or q.get('question') or q.get('q') or '',
                'options': q.get('options', []),
                'correct_answer': q.get('answer'),
                'student_choice': student_choice,
                'correct': correct
            })

        # Upsert AnswerSheet for this student/room
        reason = None
        try:
            reason = request.form.get('auto_submit_reason')
        except Exception:
            reason = None

        sheet = AnswerSheet.query.filter_by(student_id=user.id, room_id=room.id).first()
        if not sheet:
            sheet = AnswerSheet(student_id=user.id, room_id=room.id, score=score, details_json=json.dumps(details), auto_submit_reason=reason)
            db.session.add(sheet)
        else:
            sheet.score = score
            sheet.details_json = json.dumps(details)
            sheet.created_at = datetime.utcnow()
            if reason:
                sheet.auto_submit_reason = reason

        db.session.commit()
        flash(f"Quiz submitted! Score: {score}/{len(questions)}", "success")
        return redirect(url_for('student_result', room_id=room.id))

    # Calculate remaining time
    elapsed = (datetime.utcnow() - room.quiz_start_time).total_seconds()
    remaining = max(0, quiz.duration*60 - elapsed)
    if remaining <= 0:
        # finalize submissions for the room and redirect to results
        try:
            finalize_room_submissions(room)
        except Exception:
            pass
        flash("Time's up! Quiz ended.", "error")
        return redirect(url_for('student_result', room_id=room.id))

    return render_template('take_quiz.html', user=user, room=room, quiz=quiz,
                           questions=questions, remaining=remaining)

@app.route('/student_result/<int:room_id>')
def student_result(room_id):
    user = current_user()
    room = Room.query.get_or_404(room_id)
    r = StudentQuizResult.query.filter_by(student_id=user.id, room_id=room.id).first()
    score = r.score if r else 0
    total = 0
    quiz = Quiz.query.get(room.quiz_id)
    if quiz:
        try:
            questions = json.loads(quiz.questions_json)
            total = len(questions)
        except Exception:
            total = 0
    # allow quick link to the detailed sheet if present
    sheet = AnswerSheet.query.filter_by(student_id=user.id, room_id=room.id).first()
    return render_template("student_result.html", user=user, score=score, total=total, sheet=sheet, room=room)


@app.route('/room/<int:room_id>/student/<int:student_id>')
def student_sheet(room_id, student_id):
    user = current_user()
    room = Room.query.get_or_404(room_id)
    student = User.query.get_or_404(student_id)
    # permissions: teacher of room or the student themself
    if not user or (user.role != 'teacher' and user.id != student.id):
        flash('Access denied', 'error')
        return redirect(url_for('index'))
    sheet = AnswerSheet.query.filter_by(student_id=student.id, room_id=room.id).first()
    if not sheet:
        flash('No sheet found for this student.', 'error')
        return redirect(url_for('teacher_room', room_id=room.id))
    try:
        details = json.loads(sheet.details_json)
    except Exception:
        details = []
    # prepare qlist similar to old templates
    qlist = []
    for item in details:
        qlist.append({
            'index': item.get('index', 0)+1,
            'question': item.get('question',''),
            'options': item.get('options', []),
            'correct_answer': item.get('correct_answer', -1),
            'student_choice': item.get('student_choice', -1),
            'correct': item.get('correct', False)
        })
    back_url = url_for('teacher_room', room_id=room.id)
    return render_template('teacher_student_sheet.html', room=room, student=student, qlist=qlist, score=sheet.score, total=len(qlist), back_url=back_url)


@app.route('/room/<int:room_id>/student/<int:student_id>/print')
def student_sheet_print(room_id, student_id):
    # reuse printable view
    user = current_user()
    room = Room.query.get_or_404(room_id)
    student = User.query.get_or_404(student_id)
    if not user or (user.role != 'teacher' and user.id != student.id):
        flash('Access denied', 'error')
        return redirect(url_for('index'))
    sheet = AnswerSheet.query.filter_by(student_id=student.id, room_id=room.id).first()
    if not sheet:
        flash('No sheet found for this student.', 'error')
        return redirect(url_for('teacher_room', room_id=room.id))
    try:
        details = json.loads(sheet.details_json)
    except Exception:
        details = []
    qlist = []
    for item in details:
        qlist.append({
            'index': item.get('index', 0)+1,
            'question': item.get('question',''),
            'options': item.get('options', []),
            'correct_answer': item.get('correct_answer', -1),
            'student_choice': item.get('student_choice', -1),
            'correct': item.get('correct', False)
        })
    return render_template('teacher_student_sheet_print.html', room=room, student=student, qlist=qlist, score=sheet.score, total=len(qlist))

@app.route('/room_report/<int:room_id>')
def room_report(room_id):
    user = current_user()
    room = Room.query.get_or_404(room_id)
    if not user or user.role != 'teacher' or room.teacher_id != user.id:
        flash("Access denied!", "error")
        return redirect(url_for('index'))
    students = []
    for s in room.students:
        r = StudentQuizResult.query.filter_by(student_id=s.id, room_id=room.id).first()
        # check if a detailed sheet exists for this student in this room
        sheet = AnswerSheet.query.filter_by(student_id=s.id, room_id=room.id).first()
        students.append({
            'id': s.id,
            'name': s.name,
            'roll': s.roll,
            'score': r.score if r else 0,
            'has_sheet': bool(sheet),
            'created_at': sheet.created_at if sheet else None,
            'auto_submit_reason': sheet.auto_submit_reason if sheet else None
        })
    return render_template("report.html", user=user, room=room, students=students)


# ----------------- DB Init -----------------
with app.app_context():
    db.create_all()

# ----------------- Run -----------------
if __name__ == "__main__":
    app.run(debug=True)
