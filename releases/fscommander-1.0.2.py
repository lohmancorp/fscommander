################################################################################
# fscommander.py is a script designed to query tickets from FreshService and
# to display them in a custom sorted order to allow an agent to work faster
# and according to priorities that FreshService is not capable of sorting.
#
# Author: Taylor Giddens - taylor.giddens@ingrammicro.com
# Version: 1.0.2
################################################################################

# Import necessary libraries
import argparse
import os
import logging
import requests
import base64
import json
import time
import signal
import sys
from datetime import datetime, timedelta
from dotenv import load_dotenv
from pathlib import Path
from prettytable import PrettyTable

# Construct the path to the .env file
env_path = Path(__file__).resolve().parent.parent / '.env'

# Load environment variables from the specified .env file
load_dotenv(dotenv_path=env_path)

# Script Variables:
SCRIPT_NAME = 'fscommander.py'
SCRIPT_VERSION = '1.0.2'  # Update with each release.

# Global variables for tracking
original_time_wait = None
interrupted = False

# Argument Parsing 
def parse_arguments():
    parser = argparse.ArgumentParser(description='Script to read and sort FreshService tickets.\n')
    parser.add_argument('-g', '--get-tickets', required=False, choices=['mine', 'group'], help='\nTells the script which set of tickets to retrieve.')
    parser.add_argument('-o', '--output', choices=['json', 'table'], default='json', help='Output format: json or table')
    parser.add_argument('-m', '--mode', required=True, choices=['staging', 'production', 'test'], help='API mode: staging, production, or test.')
    parser.add_argument('-f', '--file', required=False, help='Path to JSON file for test mode.')
    parser.add_argument('-t', '--time-wait', type=int, required=True, help='Time in milliseconds to wait between API calls.')
    parser.add_argument('-l', '--log-level', choices=['WARNING', 'DEBUG'], default='WARNING', help='Logging level')
    parser.add_argument('-v', '--version', default=SCRIPT_VERSION, help='Version of the script to use.')
    return parser.parse_args()

if __name__ == "__main__":
    args = parse_arguments()

# Environment variables
API_KEY = os.getenv('API_KEY')
FRESH_SERVICE_ENDPOINTS = {
    'staging': os.getenv('STAGING_ENDPOINT'),
    'production': os.getenv('PRODUCTION_ENDPOINT'),
}
LOG_DIRECTORY = os.getenv('LOG_DIRECTORY')
ERROR_PAYLOAD_DIRECTORY = os.getenv('ERROR_PAYLOAD_DIRECTORY')

# Signal handler for handling Ctrl+C
def signal_handler(signum, frame):
    global interrupted
    interrupted = True
    print("\nInterrupt received, finishing current ticket and exiting... \n\n")

# Register the signal handler
signal.signal(signal.SIGINT, signal_handler)

# Logging Configuration with Iteration
def setup_logging(args):
    today = datetime.now().strftime("%Y-%m-%d")
    input_filename = SCRIPT_NAME

    # Check if the log directory exists, create it if it does not
    if not os.path.exists(LOG_DIRECTORY):
        os.makedirs(LOG_DIRECTORY, exist_ok=True)

    iteration = 1
    while True:
        log_filename = f"{today}-{input_filename}_{iteration}.log"
        full_log_path = os.path.join(LOG_DIRECTORY, log_filename)
        if not os.path.exists(full_log_path):
            break
        iteration += 1

    # Set the baseline logging level to INFO
    logging.basicConfig(filename=full_log_path, filemode='a',
                        level=logging.INFO, 
                        format='%(asctime)s - %(levelname)s - %(message)s')

    # If the user's selected log level is DEBUG, adjust logging level accordingly
    if args.log_level.upper() == 'DEBUG':
        logging.getLogger().setLevel(logging.DEBUG)
        
# Generate the authorization header for API requests
def generate_auth_header(api_key):
    encoded_credentials = base64.b64encode(f"{api_key}:X".encode('utf-8')).decode('utf-8')
    return {
        "Content-Type": "application/json",
        "Authorization": f"Basic {encoded_credentials}"
    }

# Function to check the rate limit and adjust wait time if needed
def check_and_adjust_rate_limit(response, args):
    remaining_calls = int(response.headers.get('X-Ratelimit-Remaining', 0))
    if remaining_calls <= 40:
        args.time_wait = max(args.time_wait, 1000)  # Slowing down API calls
    else:
        args.time_wait = original_time_wait  # Resetting to original time wait

# Function to handle API requests with retries for timeouts and handle specific error codes
def make_api_request(method, url, headers, data=None, retries=2):
    try:
        response = requests.request(method, url, headers=headers, json=data)
        if response.status_code == 403:  # Handling 403 Forbidden Error
            logging.error(f"403 Forbidden error encountered. URL: {url} Method: {method}")
            print("It looks like FreshWorks doesn't like what you were doing and the user was locked.")
            print("Please check in FreshService that the user who your API KEY corresponds to is not locked.")
            print("https://support.cloudblue.com/agents")
            exit(1)
        elif response.status_code == 401:  # Handling 401 Unauthorized Error
            logging.error(f"401 Unauthorized error encountered. URL: {url} Method: {method}")
            print("It looks like the API KEY you provided has a problem.")
            print("Follow these instructions to make sure you are getting the correct API KEY:")
            print("https://support.freshservice.com/en/support/solutions/articles/50000000306-where-do-i-find-my-api-key-")
            print("Once you have the correct API KEY, open the .env file located in the root folder of the script to update the value.")
            exit(1)
        elif response.status_code == 429:  # Handling 429 Too Many Requests Error
            logging.error(f"429 Too Many Requests error encountered. URL: {url} Method: {method}")
            print("It looks like you exceeded the API rate limit.")
            print("Go get a coffee, check your user isn't locked, and try again.")
            exit(1)
        response.raise_for_status()
        return response
    except requests.exceptions.Timeout:
        if retries > 0:
            time.sleep(2)
            return make_api_request(method, url, headers, data, retries - 1)
        else:
            raise
    except requests.exceptions.RequestException as e:
        logging.error(f"API request failed: {e}")
        raise
    
# Function to get tickets assigned to the agent
def get_my_tickets(base_url, headers):
    agent_id = os.getenv("AGENT_ID")  # Read agent_id from .env file
    if not agent_id:
        logging.error("AGENT_ID not found in .env file.")
        sys.exit("AGENT_ID not set in .env file. Please set it and try again.")

    url = f"{base_url}/tickets/filter?query=\"agent_id: {agent_id}\""
    response = make_api_request("GET", url, headers)

    if response.status_code != 200:
        logging.error(f"Failed to fetch tickets for user: {response.status_code} - {response.text}")
        return None

    tickets_data = response.json()
    return tickets_data["tickets"]

# Function to get tickets assigned to the agent
def get_my_groups_tickets(base_url, headers):
    group_id = os.getenv("GROUP_ID")  # Read group_id from .env file
    if not group_id:
        logging.error("GROUP_ID not found in .env file.")
        sys.exit("GROUP_ID not set in .env file. Please set it and try again.")

    url = f"{base_url}/tickets/filter?query=\"group_id: {group_id}\""
    response = make_api_request("GET", url, headers)

    if response.status_code != 200:
        logging.error(f"Failed to fetch tickets for group: {response.status_code} - {response.text}")
        return None

    tickets_data = response.json()
    return tickets_data["tickets"]

def sort_tickets(tickets):
    def sorting_key(ticket):
        account_tier = ticket['custom_fields']['account_tier']
        priority = ticket['priority']
        is_escalated = ticket['is_escalated']
        ticket_type = ticket['custom_fields']['ticket_type']
        created_at = ticket['created_at']

        # Mapping account tier to a numerical value for sorting
        tier_map = {'A': 1, 'B': 2, 'C': 3, 'D': 4, 'E': 5}
        tier_value = tier_map.get(account_tier, 6)  # Default to a value higher than all tiers

        # Mapping ticket type to a numerical value for sorting (Incident: 1, Service Request: 2)
        type_value = 1 if ticket_type == "Incident or Problem" else 2

        # Custom sorting logic
        return (
            tier_value,  # First sort by account tier
            priority,  # Then by priority
            -int(is_escalated),  # Then by escalation status (True before False)
            type_value,  # Then by ticket type
            created_at  # Finally, by creation date
        )

    tickets.sort(key=sorting_key)
    return tickets

# Function to display tickets in JSON format
def display_as_json(tickets):
    print(json.dumps(tickets, indent=4))

# Function to display tickets in table format
def display_as_table(tickets):
    table = PrettyTable()
    table.field_names = ["id", "department_id", "subject", "priority", "status", "is_escalated", "environment", "account_tier", "ticket_type"]

    for ticket in tickets:
        table.add_row([
            ticket['id'],
            ticket['department_id'],
            ticket['subject'],
            ticket['priority'],
            ticket['status'],
            ticket['is_escalated'],
            ticket['custom_fields']['environment'],
            ticket['custom_fields']['account_tier'],
            ticket['custom_fields']['ticket_type']
        ])

    print(table)

# Function to read JSON file and return a list of tickets
def read_json_file(file_path):
    try:
        with open(file_path, 'r') as file:
            data = json.load(file)
            # Assuming the JSON structure contains a list of tickets under a key, modify as needed
            return data.get('tickets', [])
    except json.JSONDecodeError as e:
        logging.error(f"Invalid JSON file: {e}")
        sys.exit(f"Error: Invalid JSON file. {e}")
    except FileNotFoundError:
        logging.error("JSON file not found")
        sys.exit("Error: JSON file not found")
        
# Function to convert numerical status and priority to readable strings
def make_status_priority_readable(tickets):
    # Mappings for status and priority
    status_mapping = {
        2: "Open",
        3: "Pending",
        4: "Resolved",
        5: "Closed",
        6: "New",
        7: "Pending access",
        8: "Waiting for RnD",
        9: "Pending other ticket",
        10: "Waiting for maintenance",
        11: "Waiting for bugfix",
        12: "Service request triage",
        13: "Rejected",
        14: "Duplicate"
    }

    priority_mapping = {
        1: "Low",
        2: "Medium",
        3: "High",
        4: "Urgent"
    }

    # Iterate through each ticket and update status and priority
    for ticket in tickets:
        ticket['status'] = status_mapping.get(ticket['status'], "Unknown Status")
        ticket['priority'] = priority_mapping.get(ticket['priority'], "Unknown Priority")

    return tickets        

# Main function that does all the work.
def main():
    global original_time_wait   
    args = parse_arguments()
    setup_logging(args)
    
    # Set the global original_time_wait based on the argument
    original_time_wait = args.time_wait

    if args.mode == 'test':
        if not args.file:
            sys.exit("Error: Missing file path for test mode")

        tickets = read_json_file(args.file)
    else:
        # Setup API headers
        headers = generate_auth_header(API_KEY)

        # Get the base URL from the selected mode
        base_url = FRESH_SERVICE_ENDPOINTS[args.mode]

        if args.get_tickets == 'mine':
            tickets = get_my_tickets(base_url, headers)
        elif args.get_tickets == 'group':
            tickets = get_my_groups_tickets(base_url, headers)

    if tickets is None:
        logging.error("Failed to retrieve tickets")
        sys.exit("Error fetching tickets. Check logs for more details.")

    # Sort the tickets
    sorted_tickets = sort_tickets(tickets)

    # Filter out tickets with specific statuses
    filtered_tickets = [ticket for ticket in sorted_tickets if ticket['status'] not in [4, 5, 13, 14]]

    # Make status and priority readable
    readable_tickets = make_status_priority_readable(filtered_tickets)

    # Display sorted and filtered tickets based on the selected output format
    if args.output == 'json':
        display_as_json(readable_tickets)
    elif args.output == 'table':
        display_as_table(readable_tickets)

if __name__ == "__main__":
    main()

