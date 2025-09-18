import os
import json
import mysql.connector
from dotenv import load_dotenv
from flask import Flask, render_template, request, redirect, url_for, session, flash
from werkzeug.security import generate_password_hash, check_password_hash
import google.genai as genai
import markdown
from markupsafe import Markup


def format_markup(text):
    if not text:
        return ""
    html = markdown.markdown(text, extensions=["fenced_code", "tables"])
    return Markup(html)


# Load environment variables
load_dotenv()
api_key = os.getenv("GOOGLE_API_KEY")

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "supersecret")

# Database connection
db = mysql.connector.connect(
    host="localhost",
    user=os.getenv("DB_USER", "root"),
    password=os.getenv("DB_PASSWORD", ""),
    database=os.getenv("DB_NAME", "career_db"),
)
cursor = db.cursor(dictionary=True)

# Google GenAI
client = genai.Client(api_key=api_key)
model_name = "gemini-2.5-flash-preview-05-20"


@app.route("/")
def home():
    if "user_id" in session:
        return redirect(url_for("get_started"))
    return redirect(url_for("landing"))


# ================= Landing ================= #
@app.route("/landing")
def landing():
    return render_template("landing.html")


# ================= User Auth ================= #
@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        full_name = request.form["full_name"]
        email = request.form["email"]
        password = request.form["password"]

        hashed_pw = generate_password_hash(password)

        cursor.execute(
            "INSERT INTO users (full_name, email, password) VALUES (%s, %s, %s)",
            (full_name, email, hashed_pw),
        )
        db.commit()
        flash("Registration successful! Please log in.", "success")
        return redirect(url_for("login"))

    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form["email"]
        password = request.form["password"]

        cursor.execute("SELECT * FROM users WHERE email = %s", (email,))
        user = cursor.fetchone()

        if user and check_password_hash(user["password"], password):
            session["user_id"] = user["id"]
            session["full_name"] = user["full_name"]
            return redirect(url_for("get_started"))
        else:
            flash("Invalid credentials", "danger")

    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("landing"))


@app.route("/profile", methods=["GET"])
def profile():
    if "user_id" not in session:
        return redirect(url_for("login"))

    cursor.execute("SELECT * FROM users WHERE id = %s", (session["user_id"],))
    user = cursor.fetchone()
    return render_template("profile.html", user=user)


# ================= Career Counseling ================= #
@app.route("/get_started", methods=["GET", "POST"])
def get_started():
    if "user_id" not in session:
        return redirect(url_for("login"))

    session["want_options"] = 0

    if request.method == "POST":
        profile = request.form["profile"]
        interests = request.form["interests"]

        prompt = f"""
        Act as a world-class personalized career and skills advisor for Indian students. 
        Your goal is to provide a suitable career path based on the user's profile and interests.
        
        List all careers related to the users interests and profile.
        
        User Profile: {profile}
        User Interests: {interests}

        Provide a response with the following JSON structure (Do NOT include any explanations or additional text outside the JSON):
        {{
        "careers": {{"career_name":"About the career (Description, relevance, opportunities, expected salary range, growth prospects)"}},
        }}
        """

        try:
            for i in range(3):
                try:
                    response = client.models.generate_content(
                        model=model_name,
                        contents=prompt,
                    )
                except Exception as e:
                    if i == 2:
                        raise e
                    continue
        except Exception as e:
            flash(f"Error generating roadmap: {str(e)}", "danger")

        raw_output = response.candidates[0].content.parts[0].text
        raw_output = raw_output.strip("```")
        raw_output = raw_output.strip("json")

        response_json = json.loads(raw_output)

        cursor.execute(
            "UPDATE users SET career_result = %s WHERE id = %s",
            (json.dumps(response_json), session["user_id"]),
        )
        db.commit()

        session["want_options"] = 1

        return redirect(url_for("choose_career"))

    return render_template("get_started.html")


@app.route("/choose_career", methods=["GET", "POST"])
def choose_career():
    if "user_id" not in session:
        return redirect(url_for("login"))
    if session["want_options"] == 1:
        cursor.execute(
            "SELECT career_result FROM users WHERE id = %s", (session["user_id"],)
        )
        career_result = cursor.fetchone()
        if not career_result:
            return redirect(url_for("get_started"))
        career_result = json.loads(career_result["career_result"])
        careers = career_result["careers"]
    if request.method == "POST":
        career = request.form["career"]
        cursor.execute(
            "UPDATE users SET career = %s WHERE id = %s",
            (career, session["user_id"]),
        )
        db.commit()
        prompt = f"""Act as a world-class personalized career and skills advisor for Indian students. 
        Your goal is to provide a detailed, realistic, and actionable career roadmap.
        
        Suggest a detailed career roadmap for {career}. Include skills, courses, and steps to achieve it.
        
        Respond in JSON with fields: summary, roadmap, links. Do NOT include any explanations or additional text outside the JSON.
        
        "summary": "A brief summary of the career, including its relevance and opportunities, expected salary range, and growth prospects.",

        "roadmap": [{{
            "step": "Step 1: Foundational Skills",
            "details": "List of foundational skills with actionable suggestions, e.g., 'Learn Python and data structures'."
            }},
            {{
            "step": "Step 2: Core Concepts",
            "details": "Details about core concepts to master, e.g., 'Master machine learning algorithms'."
            }},
            {{
            "step": "Step 3: Advanced Training & Projects",
            "details": "Suggestions for advanced courses, certifications, and portfolio projects."
            }},
            {{
            "step": "Step 4: Job Preparation",
            "details": "Actionable advice on resume building, interview practice, and networking."
            }},
            {{
            "step": "Step 5: Continued Growth",
            "details": "How to stay relevant and continue professional development."
            }}
        ]

        "links": ["List of links to relevant courses, resources and communities."]

        """
        try:
            response = client.models.generate_content(
                model=model_name,
                contents=prompt,
            )
            raw_output = response.candidates[0].content.parts[0].text
            raw_output = raw_output.strip("```")
            raw_output = raw_output.strip("json")

            response_json = json.loads(raw_output)

            cursor.execute(
                "UPDATE users SET career_details = %s WHERE id = %s",
                (json.dumps(response_json), session["user_id"]),
            )
            db.commit()

            return redirect(url_for("career_details"))

        except Exception as e:
            flash(f"Error generating roadmap: {str(e)}", "danger")
    if session["want_options"] == 1:
        return render_template("choose_career.html", careers=careers)
    else:
        return render_template("career.html")


@app.route("/career_details", methods=["GET", "POST"])
def career_details():
    if "user_id" not in session:
        return redirect(url_for("landing"))
    cursor.execute(
        "SELECT career_details FROM users WHERE id = %s", (session["user_id"],)
    )
    career_details = cursor.fetchone()
    if not career_details:
        return redirect(url_for("get_started"))
    career_details = json.loads(career_details["career_details"])
    summary = career_details["summary"]
    roadmap = career_details["roadmap"]
    for r in roadmap:
        print(r.keys())
    formatted_details = {}
    formatted_details["summary"] = {"summary": format_markup(summary)}
    formatted_details["roadmap"] = {
        "roadmap": [
            {"step": r["step"], "details": format_markup(r["details"])} for r in roadmap
        ]
    }

    return render_template("career_details.html", summary=summary, roadmap=roadmap)


if __name__ == "__main__":
    app.run(debug=True)
