from flask import Flask, render_template, request, redirect, url_for, flash, session
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
import random, string, json, os
from datetime import datetime, timedelta

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

# ----------------- Helpers -----------------
def generate_code(length=6):
    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=length))

def current_user():
    if 'user_id' in session:
        return User.query.get(session['user_id'])
    return None


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

        # Check if email already exists
        if User.query.filter_by(email=email).first():
            flash("Email already exists!")
            return redirect(url_for('register'))

        # Create new user
        u = User(name=name, email=email, password=password, role=role, roll=roll)
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
        name = request.form['name']
        code = request.form.get('code') or generate_code()
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
        code = request.form['code']
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
    if user.role != 'teacher' or room.teacher_id != user.id:
        flash("Access denied!")
        return redirect(url_for('index'))
    room.is_active = False
    db.session.commit()
    flash(f"Room '{room.name}' closed successfully!")
    return redirect(url_for('teacher_room', room_id=room.id))

# --------- Teacher Dashboard ---------
@app.route('/teacher_dashboard/<int:room_id>')
def teacher_dashboard(room_id):
    user = current_user()
    room = Room.query.get_or_404(room_id)
    if not user or user.role != 'teacher' or room.teacher_id != user.id:
        flash("Access denied!", "error")
        return redirect(url_for('index'))

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
    return render_template("teacher_dashboard.html", room=room, students=students)

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
        questions = request.form['questions']
        q = Quiz(title=title, questions_json=questions)
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
    room.quiz_start_time = datetime.utcnow()
    db.session.commit()
    flash("Quiz started for students!", "success")
    return redirect(url_for('teacher_room', room_id=room.id))

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
        db.session.commit()
        flash(f"Quiz submitted! Score: {score}/{len(questions)}", "success")
        return redirect(url_for('student_result', room_id=room.id))

    # Calculate remaining time
    elapsed = (datetime.utcnow() - room.quiz_start_time).total_seconds()
    remaining = max(0, quiz.duration*60 - elapsed)
    if remaining <= 0:
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
    return render_template("student_result.html", user=user, score=score, total=total)

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
        students.append({'name': s.name, 'roll': s.roll, 'score': r.score if r else 0})
    return render_template("report.html", user=user, room=room, students=students)


# ----------------- DB Init -----------------
with app.app_context():
    db.create_all()

# ----------------- Run -----------------
if __name__ == "__main__":
    app.run(debug=True)
