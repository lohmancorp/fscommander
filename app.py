from flask import Flask, request, render_template
import subprocess
import os
import sys

SCRIPT_VERSION = "1.0"  # You can set the version as needed
app = Flask(__name__)

# Function to call fscommander.py with arguments
def run_fscommander(args):
    # Set the path to the fscommander.py script
    fscommander_script_path = "releases/fscommander.py"

    # Use the same Python interpreter as the current script
    python_executable = sys.executable

    # Build the command
    command = [python_executable, fscommander_script_path]
    command.extend(args)

    # Run the script and capture the output
    result = subprocess.run(command, capture_output=True, text=True, cwd=os.path.dirname(__file__))
    return result.stdout if result.returncode == 0 else result.stderr

# Function to handle form submission
def handle_form_submission():
    # Extract data from form
    get_tickets = request.form.get('get_tickets')
    output = request.form.get('output')
    mode = request.form.get('mode')
    file = request.form.get('file')
    time_wait = request.form.get('time_wait')
    log_level = request.form.get('log_level')

    # Prepare arguments for fscommander.py
    args = []
    if get_tickets:
        args.extend(['-g', get_tickets])
    if output:
        args.extend(['-o', output])
    if mode:
        args.extend(['-m', mode])
    if file:
        args.extend(['-f', file])
    if time_wait:
        args.extend(['-t', time_wait])
    if log_level:
        args.extend(['-l', log_level])

    # Run fscommander.py and get results
    results = run_fscommander(args)
    return results

@app.route('/', methods=['GET', 'POST'])
def index():
    if request.method == 'POST':
        # Call the handle_form_submission function to process the form data
        results = handle_form_submission()
        return render_template('index.html', results=results)

    # GET request
    return render_template('index.html', version=SCRIPT_VERSION)

if __name__ == '__main__':
    app.run(debug=True)
