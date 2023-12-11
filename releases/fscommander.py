################################################################################
# fscommander.py is a script designed to query tickets from FreshService and
# to display them in a custom sorted order to allow an agent to work faster
# and according to priorities that FreshService is not capable of sorting.
#
# Author: Taylor Giddens - taylor.giddens@ingrammicro.com
# Version: 1.0.5
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
from datetime import datetime
from dotenv import load_dotenv
from pathlib import Path
from prettytable import PrettyTable

# Construct the path to the .env file
env_path = Path(__file__).resolve().parent.parent / '.env'

# Load environment variables from the specified .env file
load_dotenv(dotenv_path=env_path)

# Script Variables:
SCRIPT_NAME = 'fscommander.py'
SCRIPT_VERSION = '1.0.5'  # Update with each release.

# Global variables for tracking
original_time_wait = None
interrupted = False

# Argument Parsing 
def parse_arguments():
    parser = argparse.ArgumentParser(description='Script to read and sort FreshService tickets.\n')
    parser.add_argument('-g', '--get-tickets', required=False, choices=['mine', 'mine_focused', 'group', 'group_focused'], help='\nTells the script which set of tickets to retrieve.')
    parser.add_argument('-o', '--output', choices=['json', 'table', 'html'], default='json', help='Output format: json, table, or html')
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
import logging
from datetime import datetime

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
    logging.basicConfig(filename=full_log_path, filemode='w',  # 'w' to write from the beginning
                        level=logging.INFO, 
                        format='%(asctime)s - %(levelname)s - %(message)s')

    # Start logging with script details
    logging.info('#' * 50)
    logging.info(f"{SCRIPT_NAME}")
    logging.info(f"Script Start Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    # Log the input parameters
    for arg in vars(args):
        logging.info(f"{arg}: {getattr(args, arg)}")

    logging.info('#' * 50)

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
        logging.warning(f"Slowing down API requests due to low remaining calls.  Current remaining {remaining_calls}")
    else:
        args.time_wait = original_time_wait  # Resetting to original time wait
        logging.info(f"Returning API timewait to orginal value.  Current remaining {remaining_calls}")

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

    url = f"{base_url}/tickets/filter?query=\"agent_id: {agent_id} AND status: 2 OR agent_id: {agent_id} AND status: 3 OR agent_id: {agent_id} AND status: 6 OR agent_id: {agent_id} AND status: 7 OR agent_id: {agent_id} AND status: 8 OR agent_id: {agent_id} AND status: 9 OR agent_id: {agent_id} AND status: 10 OR agent_id: {agent_id} AND status: 11 OR agent_id: {agent_id} AND status: 12\"&per_page=100"
    response = make_api_request("GET", url, headers)

    if response.status_code != 200:
        logging.error(f"Failed to fetch tickets for user: {response.status_code} - {response.text}")
        return None

    tickets_data = response.json()
    return tickets_data["tickets"]

# Function to get tickets assigned to the agent, but only the currently actionable tickets.
def get_my_tickets_focused(base_url, headers):
    agent_id = os.getenv("AGENT_ID")  # Read agent_id from .env file
    if not agent_id:
        logging.error("AGENT_ID not found in .env file.")
        sys.exit("AGENT_ID not set in .env file. Please set it and try again.")

    url = f"{base_url}/tickets/filter?query=\"agent_id: {agent_id} AND status: 2 OR agent_id: {agent_id} AND status: 6 OR agent_id: {agent_id} AND status: 12\"&per_page=100"
    response = make_api_request("GET", url, headers)

    if response.status_code != 200:
        logging.error(f"Failed to fetch tickets for user: {response.status_code} - {response.text}")
        return None

    tickets_data = response.json()
    return tickets_data["tickets"]

# Function to get tickets assigned to the group
def get_my_groups_tickets(base_url, headers):
    group_id = os.getenv("GROUP_ID")  # Read group_id from .env file
    if not group_id:
        logging.error("GROUP_ID not found in .env file.")
        sys.exit("GROUP_ID not set in .env file. Please set it and try again.")

    url = f"{base_url}/tickets/filter?query=\"group_id: {group_id} AND status: 2 OR group_id: {group_id} AND status: 3 OR group_id: {group_id} AND status: 6 OR group_id: {group_id} AND status: 7 OR group_id: {group_id} AND status: 8 OR group_id: {group_id} AND status: 9 OR group_id: {group_id} AND status: 10 OR group_id: {group_id} AND status: 11 OR group_id: {group_id} AND status: 12\"&per_page=100"
    response = make_api_request("GET", url, headers)

    if response.status_code != 200:
        logging.error(f"Failed to fetch tickets for group: {response.status_code} - {response.text}")
        return None

    tickets_data = response.json()
    return tickets_data["tickets"]

# Function to get tickets assigned to the group, but only the currently actionable tickets
def get_my_groups_tickets_focused(base_url, headers):
    group_id = os.getenv("GROUP_ID")  # Read group_id from .env file
    if not group_id:
        logging.error("GROUP_ID not found in .env file.")
        sys.exit("GROUP_ID not set in .env file. Please set it and try again.")

    url = f"{base_url}/tickets/filter?query=\"group_id: {group_id} AND status: 2 OR group_id: {group_id} AND status: 6 OR group_id: {group_id} AND status: 12\"&per_page=100"
    response = make_api_request("GET", url, headers)

    if response.status_code != 200:
        logging.error(f"Failed to fetch tickets for group: {response.status_code} - {response.text}")
        return None

    tickets_data = response.json()
    return tickets_data["tickets"]

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

#Scoring map used to sort tickets in a finute order. 
SCORING_MAP = {
    ('A', 4, 'Production', 'Incident or Problem'): 76,
    ('A', 4, 'Lab', 'Incident or Problem'): 75,
    ('B', 4, 'Production', 'Incident or Problem'): 74,
    ('B', 4, 'Lab', 'Incident or Problem'): 73,
    ('C', 4, 'Production', 'Incident or Problem'): 72,
    ('C', 4, 'Lab', 'Incident or Problem'): 71,
    ('D', 4, 'Production', 'Incident or Problem'): 70,
    ('D', 4, 'Lab', 'Incident or Problem'): 69,
    ('E', 4, 'Production', 'Incident or Problem'): 68,
    ('E', 4, 'Lab', 'Incident or Problem'): 67,
    ('A', 'escalated', 'Production'): 66,
    ('A', 'escalated', 'Lab'): 65,
    ('B', 'escalated', 'Production'): 64,
    ('B', 'escalated', 'Lab'): 63,
    ('C', 'escalated', 'Production'): 62,
    ('C', 'escalated', 'Lab'): 61,
    ('A', 3, 'Production', 'Incident or Problem'): 60,
    ('A', 3, 'Lab', 'Incident or Problem'): 59,
    ('B', 3, 'Production', 'Incident or Problem'): 58,
    ('B', 3, 'Lab', 'Incident or Problem'): 57,
    ('C', 3, 'Production', 'Incident or Problem'): 56,
    ('C', 3, 'Lab', 'Incident or Problem'): 55,
    ('A', 3, 'Production', 'Service request'): 54,
    ('A', 3, 'Lab', 'Service request'): 53,
    ('B', 3, 'Production', 'Service request'): 52,
    ('B', 3, 'Lab', 'Service request'): 51,
    ('C', 3, 'Production', 'Service request'): 50,
    ('C', 3, 'Lab', 'Service request'): 49,
    ('D', 3, 'Production', 'Incident or Problem'): 48,
    ('D', 3, 'Lab', 'Incident or Problem'): 47,
    ('E', 3, 'Production', 'Incident or Problem'): 46,
    ('E', 3, 'Lab', 'Incident or Problem'): 45,
    ('A', 2, 'Production', 'Incident or Problem'): 44,
    ('A', 2, 'Lab', 'Incident or Problem'): 43,
    ('B', 2, 'Production', 'Incident or Problem'): 42,
    ('B', 2, 'Lab', 'Incident or Problem'): 41,
    ('D', 'escalated', 'Production'): 40,
    ('D', 'escalated', 'Lab'): 39,
    ('C', 2, 'Production', 'Incident or Problem'): 38,
    ('C', 2, 'Lab', 'Incident or Problem'): 37,
    ('A', 2, 'Production', 'Service request'): 36,
    ('A', 2, 'Lab', 'Service request'): 35,
    ('B', 2, 'Production', 'Service request'): 34,
    ('B', 2, 'Lab', 'Service request'): 33,
    ('C', 2, 'Production', 'Service request'): 32,
    ('C', 2, 'Lab', 'Service request'): 31,
    ('E', 'escalated', 'Production'): 30,
    ('E', 'escalated', 'Lab'): 29,
    ('D', 2, 'Production', 'Incident or Problem'): 28,
    ('D', 2, 'Lab', 'Incident or Problem'): 27,
    ('D', 2, 'Production', 'Service request'): 26,
    ('D', 2, 'Lab', 'Service request'): 25,
    ('E', 2, 'Production', 'Incident or Problem'): 24,
    ('E', 2, 'Lab', 'Incident or Problem'): 23,
    ('E', 2, 'Production', 'Service request'): 22,
    ('E', 2, 'Lab', 'Service request'): 21,
    ('A', 1, 'Production', 'Incident or Problem'): 20,
    ('A', 1, 'Lab', 'Incident or Problem'): 19,
    ('B', 1, 'Production', 'Incident or Problem'): 18,
    ('B', 1, 'Lab', 'Incident or Problem'): 17,
    ('C', 1, 'Production', 'Incident or Problem'): 16,
    ('C', 1, 'Lab', 'Incident or Problem'): 15,
    ('A', 1, 'Production', 'Service request'): 14,
    ('A', 1, 'Lab', 'Service request'): 13,
    ('B', 1, 'Production', 'Service request'): 12,
    ('B', 1, 'Lab', 'Service request'): 11,
    ('C', 1, 'Production', 'Service request'): 10,
    ('C', 1, 'Lab', 'Service request'): 9,
    ('D', 1, 'Production', 'Incident or Problem'): 8,
    ('D', 1, 'Lab', 'Incident or Problem'): 7,
    ('D', 1, 'Production', 'Service request'): 6,
    ('D', 1, 'Lab', 'Service request'): 5,
    ('E', 1, 'Production', 'Incident or Problem'): 4,
    ('E', 1, 'Lab', 'Incident or Problem'): 3,
    ('E', 1, 'Production', 'Service request'): 2,
    ('E', 1, 'Lab', 'Service request'): 1
}
# Function that performs the scoring against the SCORING_MAP
def calculate_sort_key(ticket):
    # Extract values from ticket
    account_tier = ticket['custom_fields'].get('account_tier')
    environment = ticket['custom_fields'].get('environment')
    priority = ticket['priority']
    ticket_type = ticket['custom_fields']['ticket_type']
    is_escalated = ticket['is_escalated']

    # Log warning and assign default values if account_tier or environment is missing
    if account_tier is None:
        logging.warning(f"Ticket ID: {ticket['id']} does not have an account tier defined. Default value 'C' used.")
        account_tier = 'C'
    if environment is None:
        logging.warning(f"Ticket ID: {ticket['id']} does not have an environment defined. Default value 'Production' used.")
        environment = 'Production'

    # Determine score key based on whether the ticket is escalated
    if is_escalated:
        score_key = (account_tier, 'escalated', environment)
    else:
        score_key = (account_tier, priority, environment, ticket_type)

    # Get the score from the map
    score = SCORING_MAP.get(score_key, 0)

    return (-score, ticket['created_at'])


#Function to perform final sorting based on the final scoring (Sort Key).
def sort_tickets(tickets):
    # Calculate the sort key for each ticket and store the score
    for ticket in tickets:
        sort_key = calculate_sort_key(ticket)
        ticket['score'] = -sort_key[0]  # Store the actual score
        ticket['sort_key'] = sort_key   # Store the sort key

    # Sort the tickets based on the calculated sort key
    tickets.sort(key=lambda x: x['sort_key'])

    # Debug logging if needed
    if logging.getLogger().getEffectiveLevel() == logging.DEBUG:
        for ticket in tickets:
            logging.debug(f"Ticket ID: {ticket['id']}, Score: {ticket['score']}, Created At: {ticket['created_at']}, Tier: {ticket['custom_fields']['account_tier']}, Priority: {ticket['priority']}, Is Escalated: {ticket['is_escalated']}, Environment: {ticket['custom_fields']['environment']}, Type: {ticket['custom_fields']['ticket_type']}")

    return tickets

# Function to display tickets in JSON format
def display_as_json(tickets):
    print(json.dumps(tickets, indent=4))

# Function to display tickets in table format
def display_as_table(tickets, company_names):
    # Initialize the table with the basic columns
    field_names = ["id", "department_id", "company_name", "subject", "priority", "status", "is_escalated", "environment", "account_tier", "ticket_type", "created_at"]
    
    if logging.getLogger().getEffectiveLevel() == logging.DEBUG:
        field_names.append("Score")

    table = PrettyTable()
    table.field_names = field_names

    # Set alignment for company_name and subject to left
    table.align["company_name"] = "l"
    table.align["subject"] = "l"

    for ticket in tickets:
        # Convert Zulu time to local time and truncate subject and company name
        created_at_local = datetime.strptime(ticket['created_at'], "%Y-%m-%dT%H:%M:%SZ").strftime("%Y-%m-%d %H:%M:%S")
        subject_truncated = (ticket['subject'][:47] + '...') if len(ticket['subject']) > 50 else ticket['subject']
        
        #Call to lookup company name.
        company_name = company_names.get(ticket['department_id'], 'Unknown')
        company_name_truncated = (company_name[:27] + '...') if len(company_name) > 30 else company_name

        # Prepare the basic row with truncated subject and company name
        row = [
            ticket['id'],
            ticket['department_id'],
            company_name_truncated,
            subject_truncated,
            ticket['priority'],
            ticket['status'],
            ticket['is_escalated'],
            ticket['custom_fields'].get('environment', 'Production'),
            ticket['custom_fields'].get('account_tier', 'C'),
            ticket['custom_fields']['ticket_type'],
            created_at_local,
        ]
        
        if logging.getLogger().getEffectiveLevel() == logging.DEBUG:
            row.append(ticket.get('score', 'N/A'))

        table.add_row(row)

    print(table)    
    
# Function to display tickets as HTML table
def display_as_html(tickets, company_names):
    
    if logging.getLogger().getEffectiveLevel() == logging.DEBUG:
            log_level = 'DEBUG'
            
    # Define the HTML table header
    html_table = """

        <tr>
            <th>#</th>
            <th>Ticket ID</th>
            <th>Company Name</th>
            <th>Subject</th>
            <th>Priority</th>
            <th>Status</th>
            <th>Escalated</th>
            <th>Environment</th>
            <th>Tier</th>
            <th>Type</th>
            <th>Created</th>
            <th>Score</th>
        </tr>
    """

    for index, ticket in enumerate(tickets, start=1):
        # Convert Zulu time to local time and truncate subject and company name
        created_at_local = datetime.strptime(ticket['created_at'], "%Y-%m-%dT%H:%M:%SZ").strftime("%Y-%m-%d %H:%M:%S")
        subject_truncated = (ticket['subject'][:47] + '...') if len(ticket['subject']) > 50 else ticket['subject']
        
        # Call to lookup company name.
        company_name = company_names.get(ticket['department_id'], 'Unknown')
        company_name_truncated = (company_name[:27] + '...') if len(company_name) > 30 else company_name
        

        # Extract environment and account_tier values
        environment = ticket['custom_fields'].get('environment')
        account_tier = ticket['custom_fields'].get('account_tier')

        # Handle None values
        if environment is None:
            environment_display = 'Production'
        else:
            environment_display = environment

        if account_tier is None:
            account_tier_display = 'C'
        else:
            account_tier_display = account_tier

        # Prepare an HTML row with truncated subject and company name
        row = f"""
        <tr>
            <td>{index}</td>
            <td><a href="https://support.cloudblue.com/a/tickets/{ticket['id']}" target="_blank">{ticket['id']}</a></td>
            <td>{company_name_truncated}</td>
            <td>{subject_truncated}</td>
            <td>{ticket['priority']}</td>
            <td>{ticket['status']}</td>
            <td>{ticket['is_escalated']}</td>
            <td>{environment_display}</td>
            <td>{account_tier_display}</td>
            <td>{ticket['custom_fields']['ticket_type']}</td>
            <td>{created_at_local}</td>
            <td>{ticket.get('score', 'N/A')}</td>
        </tr>
        """

        html_table += row

    print(html_table)


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

# Get company names and store to memory so they can be used for lookups.           
def get_company_names(base_url, headers):
    companies = {}
    page = 1
    while True:
        url = f"{base_url}/departments?per_page=100&page={page}"
        response = make_api_request("GET", url, headers)
        data = response.json()
        
        # Check if there are departments in the response
        if 'departments' in data and data['departments']:
            for company in data['departments']:
                companies[company['id']] = company['name']
            page += 1
        else:
            break
    return companies

# Main function that does all the work.
def main():
    global original_time_wait   
    args = parse_arguments()
    setup_logging(args)
    
    original_time_wait = args.time_wait
    headers = generate_auth_header(API_KEY)

    # Use production endpoint for fetching company names if in test mode
    api_base_url = FRESH_SERVICE_ENDPOINTS['production'] if args.mode == 'test' else FRESH_SERVICE_ENDPOINTS[args.mode]

    # Fetch company names
    company_names = get_company_names(api_base_url, headers)

    if args.mode == 'test':
        if not args.file:
            sys.exit("Error: Missing file path for test mode")
        tickets = read_json_file(args.file)
    else:
        base_url = FRESH_SERVICE_ENDPOINTS[args.mode]
        if args.get_tickets == 'mine':
            tickets = get_my_tickets(base_url, headers)
        elif args.get_tickets == 'mine_focused':
            tickets = get_my_tickets_focused(base_url, headers)    
        elif args.get_tickets == 'group':
            tickets = get_my_groups_tickets(base_url, headers)
        elif args.get_tickets == 'group_focused':
            tickets = get_my_groups_tickets_focused(base_url, headers)

    if tickets is None:
        logging.error("Failed to retrieve tickets")
        sys.exit("Error fetching tickets. Check logs for more details.")

    # Sort the tickets
    sorted_tickets = sort_tickets(tickets)

    # Make status and priority readable
    readable_tickets = make_status_priority_readable(sorted_tickets)

    # Display sorted and filtered tickets based on the selected output format
    if args.output == 'json':
        display_as_json(readable_tickets)
    elif args.output == 'table':
        # Pass company_names to display_as_table
        display_as_table(readable_tickets, company_names)
    elif args.output == 'html':
        # Pass company_names to display_as_table
        display_as_html(readable_tickets, company_names)    

if __name__ == "__main__":
    main()
