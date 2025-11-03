from flask import Flask, render_template, request, jsonify

app = Flask(__name__)

button_state = "released"

@app.route("/")
def index():
    return render_template("index.html", state=button_state)

@app.route("/update_button", methods=["POST"])
def update_button():
    global button_state
    button_state = request.form.get("state", "released")
    print(f"Button state updated: {button_state}")
    return jsonify({"status": "ok", "button_state": button_state})

@app.route("/get_state")
def get_state():
    return jsonify({"button_state": button_state})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
