"""SkillBridge - Industry Skill Alignment Platform (Flask + SQLite backend).
All role requirements and demand categories are ILLUSTRATIVE SAMPLE DATA, not live job-market research."""
import os
import re
import sqlite3
from datetime import date

from flask import Flask, g, jsonify, request, send_from_directory
from werkzeug.exceptions import HTTPException

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATABASE_PATH = os.environ.get("DATABASE_PATH", os.path.join(BASE_DIR, "data", "skillbridge.db"))
LEVELS = {"Beginner": 1, "Intermediate": 2, "Advanced": 3}
LEVEL_NAMES = {0: "Missing", 1: "Beginner", 2: "Intermediate", 3: "Advanced"}
PRIORITIES = ("High", "Medium", "Low")
SCORING = ("Missing=0, Beginner=1, Intermediate=2, Advanced=3. Earned = min(user score, required score). "
           "Readiness % = total earned / total required x 100.")

app = Flask(__name__)
app.json.sort_keys = False

SCHEMA = """
CREATE TABLE IF NOT EXISTS career_roles(
  id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL UNIQUE, description TEXT NOT NULL,
  demand TEXT NOT NULL CHECK(demand IN ('High','Medium','Emerging')), learning_focus TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS role_required_skills(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  role_id INTEGER NOT NULL REFERENCES career_roles(id) ON DELETE CASCADE,
  skill_name TEXT NOT NULL COLLATE NOCASE, category TEXT NOT NULL,
  required_level INTEGER NOT NULL CHECK(required_level BETWEEN 1 AND 3),
  recommendation TEXT NOT NULL, activity_type TEXT NOT NULL, UNIQUE(role_id, skill_name));
CREATE TABLE IF NOT EXISTS user_skills(
  id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE COLLATE NOCASE, category TEXT NOT NULL,
  level TEXT NOT NULL CHECK(level IN ('Beginner','Intermediate','Advanced')),
  notes TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS learning_activities(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  role_id INTEGER REFERENCES career_roles(id) ON DELETE SET NULL,
  skill_name TEXT NOT NULL, title TEXT NOT NULL, activity_type TEXT NOT NULL, duration TEXT NOT NULL,
  priority TEXT NOT NULL CHECK(priority IN ('High','Medium','Low')),
  current_level INTEGER NOT NULL DEFAULT 0 CHECK(current_level BETWEEN 0 AND 3),
  target_level INTEGER NOT NULL CHECK(target_level BETWEEN 1 AND 3),
  completed INTEGER NOT NULL DEFAULT 0 CHECK(completed IN (0,1)),
  completed_at TEXT, created_at TEXT NOT NULL);
"""

# (title, description, demand, learning focus, [(skill, category, required level 1-3, recommendation, activity type)])
ROLES = [
    ("Python Developer", "Builds backend services, scripts and automation using Python.", "High",
     "Python fundamentals, REST APIs and databases", [
         ("Python", "Programming", 3, "Complete Python exercises, then build a command-line tool", "Practice"),
         ("REST APIs", "Backend", 2, "Build a small REST API with Flask", "Project"),
         ("SQL", "Databases", 2, "Practise SQL queries on a sample database", "Practice"),
         ("Git", "Tools", 2, "Practise branching and pull requests in a sample repository", "Practice"),
         ("Testing", "Quality", 2, "Write unit tests for a small Python module", "Practice")]),
    ("Web Developer", "Creates responsive, interactive websites and web applications.", "High",
     "Responsive layouts, JavaScript and API integration", [
         ("HTML", "Web", 3, "Build a semantic, accessible multi-section page", "Project"),
         ("CSS", "Web", 2, "Create a responsive web page with flexbox and grid", "Project"),
         ("JavaScript", "Programming", 2, "Add interactivity and Fetch API calls to a page", "Project"),
         ("Git", "Tools", 2, "Practise branching and pull requests in a sample repository", "Practice"),
         ("REST APIs", "Backend", 1, "Consume a public sample API from a web page", "Practice")]),
    ("Data Analyst", "Turns raw data into insights using queries, spreadsheets and charts.", "Medium",
     "SQL, data cleaning and visualisation", [
         ("SQL", "Databases", 3, "Practise joins, grouping and window queries", "Practice"),
         ("Python", "Programming", 2, "Clean a CSV dataset with Python", "Practice"),
         ("Excel", "Data", 2, "Build a pivot-table summary sheet", "Practice"),
         ("Data Visualization", "Data", 2, "Create a small dashboard from a sample dataset", "Project"),
         ("Statistics", "Data", 2, "Work through descriptive-statistics exercises", "Course")]),
    ("Software Tester", "Verifies software quality through manual and automated testing.", "Medium",
     "Test design, automation and API testing", [
         ("Testing", "Quality", 3, "Write test cases for a sample application", "Practice"),
         ("REST APIs", "Backend", 2, "Test API endpoints with curl or Postman", "Practice"),
         ("Python", "Programming", 1, "Complete a beginner Python exercise set", "Practice"),
         ("SQL", "Databases", 1, "Practise basic SELECT queries", "Practice"),
         ("Git", "Tools", 1, "Learn commit, push and pull basics", "Practice")]),
    ("DevOps Engineer", "Automates build, deployment and infrastructure operations.", "Emerging",
     "Containers, orchestration and CI/CD", [
         ("Linux", "Tools", 2, "Practise shell commands and file permissions", "Practice"),
         ("Docker", "DevOps", 3, "Create a Docker image and deploy a simple application", "Project"),
         ("Kubernetes", "DevOps", 2, "Deploy a sample app to a local Kubernetes cluster", "Project"),
         ("Git", "Tools", 2, "Practise branching and pull requests in a sample repository", "Practice"),
         ("CI/CD", "DevOps", 2, "Create a pipeline that tests and builds a sample app", "Project"),
         ("Python", "Programming", 1, "Write a small automation script", "Practice")]),
]


class ApiError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.message, self.status = message, status


def today():
    return date.today().isoformat()


def connect():
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def db():
    if "db" not in g:
        g.db = connect()
    return g.db


@app.teardown_appcontext
def close_db(_exc):
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()


def rows(sql, args=()):
    return [dict(r) for r in db().execute(sql, args).fetchall()]


def row(sql, args=()):
    r = db().execute(sql, args).fetchone()
    return dict(r) if r else None


def add_activity(conn, role_id, skill, title, atype, duration, priority, cur, target, done=0):
    t = today()
    return conn.execute(
        "INSERT INTO learning_activities(role_id,skill_name,title,activity_type,duration,priority,"
        "current_level,target_level,completed,completed_at,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        (role_id, skill, title, atype, duration, priority, cur, target, done, t if done else None, t)).lastrowid


def analyze(conn, role_id):
    """Skill-gap analysis following the documented scoring rule."""
    role = conn.execute("SELECT id, title, demand FROM career_roles WHERE id=?", (role_id,)).fetchone()
    if not role:
        return None
    mine = {r["name"].lower(): r["level"] for r in conn.execute("SELECT name, level FROM user_skills")}
    items, earned, total = [], 0, 0
    for q in conn.execute("SELECT * FROM role_required_skills WHERE role_id=? ORDER BY required_level DESC, skill_name",
                          (role_id,)):
        level = mine.get(q["skill_name"].lower())
        cur, need = (LEVELS[level] if level else 0), q["required_level"]
        status = "Missing" if level is None else ("Matched" if cur >= need else "Needs Improvement")
        gap = need - cur if status != "Matched" else 0
        priority = None if gap <= 0 else ("High" if gap >= 2 else ("Medium" if status == "Missing" else "Low"))
        earned += min(cur, need)
        total += need
        items.append({"name": q["skill_name"], "category": q["category"], "status": status,
                      "current_score": cur, "current_level": LEVEL_NAMES[cur],
                      "required_score": need, "required_level": LEVEL_NAMES[need], "earned": min(cur, need),
                      "recommendation": q["recommendation"], "activity_type": q["activity_type"],
                      "duration": f"{max(gap, 0) * 6} hours", "priority": priority})
    count = lambda s: sum(1 for i in items if i["status"] == s)
    return {"role": dict(role), "recorded_skills": len(mine),
            "readiness": round(earned / total * 100) if total else 0,
            "earned_points": earned, "total_points": total, "scoring": SCORING,
            "summary": {"matched": count("Matched"), "needs_improvement": count("Needs Improvement"),
                        "missing": count("Missing")},
            "skills": items, "data_notice": "Illustrative sample requirements, not live market data."}


def init_db():
    os.makedirs(os.path.dirname(os.path.abspath(DATABASE_PATH)), exist_ok=True)
    conn = connect()
    try:
        conn.executescript(SCHEMA)
        for title, desc, demand, focus, skills in ROLES:  # roles/requirements inserted only if absent
            if not conn.execute("SELECT 1 FROM career_roles WHERE title=?", (title,)).fetchone():
                rid = conn.execute("INSERT INTO career_roles(title,description,demand,learning_focus) VALUES(?,?,?,?)",
                                   (title, desc, demand, focus)).lastrowid
                conn.executemany("INSERT OR IGNORE INTO role_required_skills(role_id,skill_name,category,required_level,"
                                 "recommendation,activity_type) VALUES(?,?,?,?,?,?)",
                                 [(rid, *s) for s in skills])
        if conn.execute("PRAGMA user_version").fetchone()[0] == 0:  # fresh database: add sample user data once
            t = today()
            conn.executemany("INSERT INTO user_skills(name,category,level,notes,created_at,updated_at) VALUES(?,?,?,?,?,?)",
                             [(n, c, lv, nt, t, t) for n, c, lv, nt in [
                                 ("Python", "Programming", "Intermediate", "Used in coursework projects"),
                                 ("HTML", "Web", "Advanced", ""), ("CSS", "Web", "Intermediate", ""),
                                 ("Git", "Tools", "Beginner", "Basic commits only"),
                                 ("SQL", "Databases", "Beginner", "Simple SELECT queries")]])
            py = conn.execute("SELECT id FROM career_roles WHERE title='Python Developer'").fetchone()["id"]
            web = conn.execute("SELECT id FROM career_roles WHERE title='Web Developer'").fetchone()["id"]
            for i in analyze(conn, py)["skills"]:
                if i["status"] != "Matched":
                    add_activity(conn, py, i["name"], i["recommendation"], i["activity_type"], i["duration"],
                                 i["priority"], i["current_score"], i["required_score"])
            add_activity(conn, web, "CSS", "Create a responsive web page with flexbox and grid", "Project",
                         "6 hours", "Medium", 1, 2, done=1)
            conn.execute("PRAGMA user_version = 1")
        conn.commit()
    finally:
        conn.close()


# ---------- validation helpers ----------
def body():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise ApiError("Request body must be a valid JSON object.")
    return data


def text(data, key, maxlen, required=True, default=""):
    v = data.get(key, default)
    v = default if v is None else v
    if not isinstance(v, str):
        raise ApiError(f"'{key}' must be a string.")
    v = re.sub(r"[\x00-\x08\x0b-\x1f]", "", v).strip()
    if required and not v:
        raise ApiError(f"'{key}' is required.")
    if len(v) > maxlen:
        raise ApiError(f"'{key}' must be at most {maxlen} characters.")
    return v


def integer(data, key, lo, hi, default=None):
    v = data.get(key, default)
    if isinstance(v, bool) or not isinstance(v, int) or not lo <= v <= hi:
        raise ApiError(f"'{key}' must be an integer between {lo} and {hi}.")
    return v


def skill_fields(d):
    level = text(d, "level", 20)
    if level not in LEVELS:
        raise ApiError("'level' must be Beginner, Intermediate or Advanced.")
    return text(d, "name", 60), text(d, "category", 40), level, text(d, "notes", 300, required=False)


def activity_row(r):
    r["completed"] = bool(r["completed"])
    return r


ACT_SQL = ("SELECT a.*, r.title AS role_title FROM learning_activities a "
           "LEFT JOIN career_roles r ON r.id = a.role_id ")
PRIORITY_ORDER = "CASE a.priority WHEN 'High' THEN 0 WHEN 'Medium' THEN 1 ELSE 2 END"


# ---------- error handlers ----------
@app.errorhandler(ApiError)
def handle_api_error(e):
    return jsonify(error=e.message), e.status


@app.errorhandler(HTTPException)
def handle_http_error(e):
    return jsonify(error=e.description), e.code


@app.errorhandler(sqlite3.Error)
def handle_db_error(e):
    app.logger.error("Database error: %s", e)
    return jsonify(error="A database error occurred. Please try again."), 500


@app.errorhandler(Exception)
def handle_unexpected(e):
    app.logger.exception("Unexpected error: %s", e)
    return jsonify(error="An unexpected server error occurred."), 500


# ---------- routes ----------
@app.get("/")
def index():
    return send_from_directory(BASE_DIR, "index.html")


@app.get("/api/health")
def health():
    try:
        db().execute("SELECT 1").fetchone()
        return jsonify(status="healthy", database="connected", date=today())
    except sqlite3.Error:
        return jsonify(status="unhealthy", database="unavailable"), 503


@app.get("/api/dashboard")
def dashboard():
    role_id = request.args.get("role_id", type=int)
    a = analyze(db(), role_id) if role_id else None
    counts = row("SELECT (SELECT COUNT(*) FROM career_roles) AS roles, (SELECT COUNT(*) FROM user_skills) AS skills, "
                 "(SELECT COUNT(*) FROM learning_activities WHERE completed=1) AS completed, "
                 "(SELECT COUNT(*) FROM learning_activities) AS activities")
    gaps = a["summary"] if a else None
    counts["skills_to_improve"] = gaps["needs_improvement"] + gaps["missing"] if gaps else 0
    return jsonify(
        stats=counts, role=a["role"] if a else None, readiness=a["readiness"] if a else None, gaps=gaps,
        recommended=[activity_row(r) for r in rows(
            ACT_SQL + f"WHERE a.completed=0 ORDER BY {PRIORITY_ORDER}, a.id LIMIT 4")],
        recent=[activity_row(r) for r in rows(
            ACT_SQL + "WHERE a.completed=1 ORDER BY a.completed_at DESC, a.id DESC LIMIT 5")],
        data_notice="Role requirements are illustrative sample data.")


@app.get("/api/roles")
def list_roles():
    out = rows("SELECT r.*, COUNT(s.id) AS skill_count, COALESCE(GROUP_CONCAT(s.skill_name,'|'),'') AS names "
               "FROM career_roles r LEFT JOIN role_required_skills s ON s.role_id=r.id GROUP BY r.id ORDER BY r.id")
    for r in out:
        r["skill_names"] = r.pop("names").split("|") if r["names"] else []
    return jsonify(out)


@app.get("/api/roles/<int:role_id>")
def get_role(role_id):
    role = row("SELECT * FROM career_roles WHERE id=?", (role_id,))
    if not role:
        raise ApiError("Career role not found.", 404)
    role["required_skills"] = rows("SELECT id, skill_name, category, required_level, recommendation, activity_type "
                                   "FROM role_required_skills WHERE role_id=? ORDER BY required_level DESC, skill_name",
                                   (role_id,))
    for s in role["required_skills"]:
        s["required_level_name"] = LEVEL_NAMES[s["required_level"]]
    role["data_notice"] = "Illustrative sample requirements, not live market data."
    return jsonify(role)


@app.get("/api/skills")
def list_skills():
    return jsonify(rows("SELECT * FROM user_skills ORDER BY name COLLATE NOCASE"))


@app.post("/api/skills")
def create_skill():
    name, cat, level, notes = skill_fields(body())
    t = today()
    try:
        cur = db().execute("INSERT INTO user_skills(name,category,level,notes,created_at,updated_at) VALUES(?,?,?,?,?,?)",
                           (name, cat, level, notes, t, t))
        db().commit()
    except sqlite3.IntegrityError:
        raise ApiError(f"A skill named '{name}' is already recorded.", 409)
    return jsonify(row("SELECT * FROM user_skills WHERE id=?", (cur.lastrowid,))), 201


@app.put("/api/skills/<int:skill_id>")
def update_skill(skill_id):
    if not row("SELECT id FROM user_skills WHERE id=?", (skill_id,)):
        raise ApiError("Skill not found.", 404)
    name, cat, level, notes = skill_fields(body())
    try:
        db().execute("UPDATE user_skills SET name=?, category=?, level=?, notes=?, updated_at=? WHERE id=?",
                     (name, cat, level, notes, today(), skill_id))
        db().commit()
    except sqlite3.IntegrityError:
        raise ApiError(f"A skill named '{name}' is already recorded.", 409)
    return jsonify(row("SELECT * FROM user_skills WHERE id=?", (skill_id,)))


@app.delete("/api/skills/<int:skill_id>")
def delete_skill(skill_id):
    cur = db().execute("DELETE FROM user_skills WHERE id=?", (skill_id,))
    db().commit()
    if cur.rowcount == 0:
        raise ApiError("Skill not found.", 404)
    return jsonify(message="Skill deleted.", id=skill_id)


@app.get("/api/analysis/<int:role_id>")
def analysis(role_id):
    result = analyze(db(), role_id)
    if not result:
        raise ApiError("Career role not found.", 404)
    return jsonify(result)


@app.get("/api/roadmap")
def list_roadmap():
    return jsonify([activity_row(r) for r in rows(
        ACT_SQL + f"ORDER BY a.completed, {PRIORITY_ORDER}, a.id")])


@app.post("/api/roadmap")
def create_activity():
    d = body()
    role_id = d.get("role_id")
    if role_id is not None and (isinstance(role_id, bool) or not isinstance(role_id, int)
                                or not row("SELECT id FROM career_roles WHERE id=?", (role_id,))):
        raise ApiError("'role_id' does not match an existing role.")
    priority = text(d, "priority", 10, default="Medium")
    if priority not in PRIORITIES:
        raise ApiError("'priority' must be High, Medium or Low.")
    new_id = add_activity(db(), role_id, text(d, "skill_name", 60), text(d, "title", 200),
                          text(d, "activity_type", 40, default="Practice"), text(d, "duration", 40, default="6 hours"),
                          priority, integer(d, "current_level", 0, 3, 0), integer(d, "target_level", 1, 3))
    db().commit()
    return jsonify(activity_row(row(ACT_SQL + "WHERE a.id=?", (new_id,)))), 201


@app.patch("/api/roadmap/<int:act_id>")
def patch_activity(act_id):
    if not row("SELECT id FROM learning_activities WHERE id=?", (act_id,)):
        raise ApiError("Learning activity not found.", 404)
    d = body()
    sets, args = [], []
    if "completed" in d:
        if not isinstance(d["completed"], bool):
            raise ApiError("'completed' must be true or false.")
        sets += ["completed=?", "completed_at=?"]
        args += [int(d["completed"]), today() if d["completed"] else None]
    for key, maxlen in (("title", 200), ("activity_type", 40), ("duration", 40), ("priority", 10)):
        if key in d:
            val = text(d, key, maxlen)
            if key == "priority" and val not in PRIORITIES:
                raise ApiError("'priority' must be High, Medium or Low.")
            sets.append(f"{key}=?")  # column names come from the fixed whitelist above
            args.append(val)
    if not sets:
        raise ApiError("No updatable fields provided.")
    db().execute(f"UPDATE learning_activities SET {', '.join(sets)} WHERE id=?", (*args, act_id))
    db().commit()
    return jsonify(activity_row(row(ACT_SQL + "WHERE a.id=?", (act_id,))))


@app.delete("/api/roadmap/<int:act_id>")
def delete_activity(act_id):
    cur = db().execute("DELETE FROM learning_activities WHERE id=?", (act_id,))
    db().commit()
    if cur.rowcount == 0:
        raise ApiError("Learning activity not found.", 404)
    return jsonify(message="Learning activity deleted.", id=act_id)


init_db()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
